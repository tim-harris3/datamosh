"""Beat-synced mosh: cut clips from a folder onto a song's beat grid.

Normalizes every clip in the folder to a common resolution/fps, then runs
make_moshable() on each with the keyframe gap tied to the song's tempo: gaps
are drawn across the CUT_BEATS range, so sections natively last about as long
as a cut. Every keyframe section from every moshable goes into a shuffled
no-repeat pool (section_pool); entries_from_beats() draws sections onto the
timeline, snapping each (trimmed or freeze-padded) to its nearest grid
multiple on the *cumulative* grid -- every cut lands exactly on the sixteenth
grid, drift-free for the whole song. Each section is byte-moshed as it is
placed (ClassicMosh: transplants, dup blooms, reorders), and a share of
sections get their keyframe deleted for the classic melt -- so blooms and
melts happen ON the beat. The whole timeline runs as one deterministic
MoshScript (saved next to the output, shareable and re-runnable), chroma
corruption and pixel sorting run on top, and the song is muxed in as the only
audio track of the final mp4.

Clips keep their native resolution: the conform target is the most common
resolution/fps among the folder's clips; only mismatched clips are letterboxed.

Run (venv active):
    python recipes/beat_mosh.py <clips_folder> <song> <bpm> [out.mp4]
"""

import os
import random
import subprocess
import sys
from collections import Counter

from datamosh import (
    BeatGrid,
    ClassicMosh,
    DeleteKeyframe,
    MoshScript,
    chroma_databend,
    enable_console_logging,
    entries_from_beats,
    ffmpeg,
    make_moshable,
    paths,
    pixel_sort,
    run_script,
    section_pool,
)

enable_console_logging()  # recipes are CLI tools: show the render lines

if len(sys.argv) < 4:
    sys.exit("usage: python recipes/beat_mosh.py <clips_folder> <song> <bpm> [out.mp4]")
FOLDER = sys.argv[1]
SONG = sys.argv[2]
BPM = float(sys.argv[3])
OUTPUT_MP4 = sys.argv[4] if len(sys.argv) > 4 else "output/beat_mosh.mp4"

SEED = random.randint(1, 9999)
SONG_START = 0.0  # seconds into the song to start from
TARGET_SECONDS = None  # None = full song (duration - SONG_START)
FPS = None  # None = most common native fps among the clips
WIDTH = HEIGHT = None  # None = most common native resolution (nudged to even)
CUT_BEATS = (0.25, 0.5)  # cut length bounds in beats: sixteenth .. eighth note;
# also the make_moshable keyframe gap range
GRID_DIV = 4  # grid resolution per beat (4 = sixteenth notes)
MELT_PROB = 0.30  # keyframe-delete chance per section (never the first)
ESCALATE = 1.0  # intensity ramps 1 -> 1+ESCALATE across the song (0 disables)
CHROMA = dict(mode="random", planes="uv", frac=0.3)  # frac 0 disables
PIXELSORT = dict(
    mode="threshold", key="hue", direction="h", frac=0.2, lo=64, hi=192, reverse=False
)  # frac 0 disables
VIDEO_EXTS = (".avi", ".mp4", ".mov", ".mkv", ".webm", ".m4v")

GRID_AVI = "output/beat_grid.avi"  # moshed, cuts on the grid
SCRIPT_JSON = "output/beat_mosh_script.json"  # the timeline, re-runnable
CHROMA_AVI = "output/beat_chroma.avi"
SORT_AVI = "output/beat_sort.avi"
NORM_DIR = "output/beat_norm"  # normalized clip cache
MOSH_DIR = "output/beat_moshable"  # bpm-gapped moshable cache

# only the mangle tunables are set (ClassicMosh reads them via base_config);
# keyframe deletion stays off because melts are applied explicitly on the grid.
# Everything else sits well under the MoshConfig defaults -- sections are
# trimmed to short beat quotas, so even a modest dup bloom fills a whole cut
CFG = dict(
    keyframe_delete_prob=0.0,
    video_transplant_prob=0.20,
    video_shuffle_prob=0.15,
    video_reverse_prob=0.15,
    video_bounce_prob=0.20,
    video_skip_prob=0.05,
    video_databend_prob=0.00,
    dup_frames_range=(1, 3),
    dup_count_range=(2, 5),
)

# ---- gather clips and conform to the folder's dominant resolution/fps ----
ffmpeg.require_ffmpeg()
folder = paths.resolve(FOLDER)
song = paths.resolve(SONG)
sources = [
    os.path.join(folder, f)
    for f in sorted(os.listdir(folder))
    if f.lower().endswith(VIDEO_EXTS)
]
if not sources:
    sys.exit(f"no video clips found in {folder}")

w, h = (
    (WIDTH, HEIGHT)
    if WIDTH and HEIGHT
    else Counter(ffmpeg.dimensions(s) for s in sources).most_common(1)[0][0]
)
w, h = w - w % 2, h - h % 2  # post effects require even dimensions
fps = (
    FPS or Counter(round(ffmpeg.frame_rate(s), 3) for s in sources).most_common(1)[0][0]
)
print(f"{len(sources)} clips -> conform {w}x{h} @ {fps:g} fps")

norm_dir = paths.resolve(NORM_DIR)
os.makedirs(norm_dir, exist_ok=True)
clips = []
for src in sources:
    dst = os.path.join(norm_dir, os.path.basename(src).replace(".", "_") + ".avi")
    if not (os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(src)):
        print(f"normalizing {os.path.basename(src)}")
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps:.6f}"
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                src,
                "-vf",
                vf,
                "-an",
                "-c:v",
                "libx264",
                "-crf",
                "16",
                "-preset",
                "fast",
                "-f",
                "avi",
                dst,
            ],
            check=True,
        )
    clips.append(dst)

# ---- make each clip moshable, keyframe gaps spanning the cut-length range ----
beat = 60.0 / BPM
gap = (CUT_BEATS[0] * beat, CUT_BEATS[1] * beat)
mosh_dir = paths.resolve(MOSH_DIR)
os.makedirs(mosh_dir, exist_ok=True)
moshables = []
for src in clips:
    stem = os.path.splitext(os.path.basename(src))[0]
    # bpm and cut range in the name so retuning either invalidates the cache
    dst = os.path.join(
        mosh_dir, f"{stem}_{BPM:g}bpm_{CUT_BEATS[0]:g}-{CUT_BEATS[1]:g}b.avi"
    )
    # per-clip derived seed: make_moshable rolls its keyframe times on the
    # global RNG, so this keeps placement (and everything downstream)
    # reproducible whether or not the cached file is reused
    random.seed(f"{SEED}:{os.path.basename(dst)}")
    if not (os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(src)):
        make_moshable(src, dst, gap_range=gap)
    moshables.append(dst)

# ---- draw sections onto the grid and mosh them as one deterministic script ----
target = TARGET_SECONDS if TARGET_SECONDS else ffmpeg.duration(song) - SONG_START
grid = BeatGrid(bpm=BPM, fps=fps, div=GRID_DIV)
pool = section_pool(moshables, random.Random(SEED))
melt_rng = random.Random(SEED + 3)  # melt rolls, independent of the effect RNG


def ops_for(i, ref, placement):
    # escalation: intensity ramps from 1 up to 1+ESCALATE across the song
    ramp = min(1.0, placement.start_units * grid.unit_seconds / target)
    ops = [ClassicMosh(intensity=1.0 + ESCALATE * ramp)]
    # melt: delete the keyframe ON the beat -- never entry 0, the stream's start
    if i and melt_rng.random() < MELT_PROB:
        ops.append(DeleteKeyframe())
    return ops


entries, placements = entries_from_beats(
    pool, grid, target, min_beats=CUT_BEATS[0], max_beats=CUT_BEATS[1], ops_for=ops_for
)
script = MoshScript(
    entries=entries,
    output=GRID_AVI,
    seed=SEED,
    fixup=False,
    checkpoint=False,  # hundreds of grid entries: skip the per-entry rewrites
    base_config=CFG,
)
script.save(SCRIPT_JSON)  # the whole beat timeline, shareable and re-runnable
grid_avi, _ = run_script(script)
melts = sum(any(isinstance(op, DeleteKeyframe) for op in e.ops) for e in entries)
end = placements[-1].start_units + placements[-1].units
print(
    f"wrote {grid_avi} ({len(entries)} cuts, {sum(p.frames for p in placements)} "
    f"frames over {end / GRID_DIV:g} beats, {melts} melts) -- script: {SCRIPT_JSON}"
)

# ---- post effects: chroma -> pixel sort (same order and seeds as the UI) ----
cur = grid_avi
if CHROMA["frac"] > 0:
    cur = chroma_databend(cur, paths.resolve(CHROMA_AVI), **CHROMA, seed=SEED + 1)
    print(f"chroma -> {cur}")
if PIXELSORT["frac"] > 0:
    cur = pixel_sort(cur, paths.resolve(SORT_AVI), **PIXELSORT, seed=SEED + 2)
    print(f"pixel sort -> {cur}")

# ---- mux the song in as the only audio track ----
final = ffmpeg.mux_audio(cur, song, OUTPUT_MP4, audio_start=SONG_START)
print(f"wrote {final} ({ffmpeg.duration(final):.1f}s)")
