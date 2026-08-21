"""Beat-synced mosh: cut clips from a folder onto a song's beat grid.

Normalizes every clip in the folder to a common resolution/fps, then runs
make_moshable() on each with the keyframe gap tied to the song's tempo: gaps
are drawn between a sixteenth note and a bar, so sections natively last 1/4-4
beats. Every keyframe section from every moshable goes into a shuffled
no-repeat pool; sections are drawn onto the timeline and each is snapped
(trimmed or freeze-padded) to its nearest sixteenth-note multiple, clamped to
the same 1/4-4 beat range, on the cumulative grid -- every cut lands exactly
on the sixteenth grid, from machine-gun 16th-note cuts to full-bar holds. Each
section is byte-moshed with mosh_segment() as it is placed (transplants, dup
blooms, reorders), and a share of sections get their keyframe deleted for the
classic melt -- so blooms and melts happen ON the beat. Chroma corruption and
pixel sorting run on top, and the song is muxed in as the only audio track of
the final mp4.

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
    MoshConfig,
    chroma_databend,
    enable_console_logging,
    ffmpeg,
    make_moshable,
    mosh_segment,
    parse_avi,
    paths,
    pixel_sort,
    split_sections,
    write_avi,
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
CUT_BEATS = (0.25, 0.5)  # cut length bounds in beats: sixteenth note .. one bar;
# also the make_moshable keyframe gap range
GRID_DIV = 4  # grid resolution per beat (4 = sixteenth notes)
MELT_PROB = 0.30  # keyframe-delete chance per section (never the first)
CHROMA = dict(mode="random", planes="uv", frac=0.3)  # frac 0 disables
PIXELSORT = dict(
    mode="threshold", key="hue", direction="h", frac=0.2, lo=64, hi=192, reverse=False
)  # frac 0 disables
VIDEO_EXTS = (".avi", ".mp4", ".mov", ".mkv", ".webm", ".m4v")

GRID_AVI = "output/beat_grid.avi"  # moshed, cuts on the grid
CHROMA_AVI = "output/beat_chroma.avi"
SORT_AVI = "output/beat_sort.avi"
NORM_DIR = "output/beat_norm"  # normalized clip cache
MOSH_DIR = "output/beat_moshable"  # bpm-gapped moshable cache

# only the mangle tunables are read (mosh_segment is driven directly below);
# keyframe deletion stays off because melts are applied manually on the grid.
# Everything else sits well under the MoshConfig defaults -- sections are
# trimmed to short beat quotas, so even a modest dup bloom fills a whole cut
CFG = MoshConfig(
    keyframe_delete_prob=0.0,
    escalate=1,
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

# ---- pool every keyframe section from every moshable ----
header = movi_start = None
pool_master = []
for m in moshables:
    hd, ms, chunks = parse_avi(m)
    if header is None:
        header, movi_start = hd, ms
    for sec in split_sections(chunks):
        pool_master.append((os.path.basename(m), sec))
print(f"{len(pool_master)} keyframe sections pooled from {len(moshables)} moshables")

# ---- draw sections onto the grid, snapping each to a sixteenth multiple ----
random.seed(SEED)
rng = random.Random(SEED + 3)  # melt rolls, independent of the effect RNG
target = TARGET_SECONDS if TARGET_SECONDS else ffmpeg.duration(song) - SONG_START
unit = beat / GRID_DIV  # grid step (a sixteenth note), in seconds
k_lo = max(1, round(CUT_BEATS[0] * GRID_DIV))
k_hi = max(k_lo, round(CUT_BEATS[1] * GRID_DIV))
pool = []
out, prev_pframes, last_v = [], None, None
melts = cum = i = 0
while cum * unit < target:
    if not pool:
        pool = list(pool_master)
        random.shuffle(pool)
    name, sec = pool.pop()
    seg = [dict(c) for c in sec]  # mosh_segment mutates; the pool may be redrawn
    k = min(max(round(len(seg) / (unit * fps)), k_lo), k_hi)  # in grid units
    quota = round((cum + k) * unit * fps) - round(cum * unit * fps)
    intensity = (
        1 + CFG.escalate * min(1.0, cum * unit / target) if CFG.escalate else 1.0
    )
    moshed, prev_pframes = mosh_segment(
        CFG,
        seg,
        keep_keyframe=(i == 0),
        donor_pframes=prev_pframes,
        intensity=intensity,
    )
    vid = [c for c in moshed if c["stream"] == "v"]
    melt = bool(i) and rng.random() < MELT_PROB
    if melt:
        vid = [c for c in vid if not c["key"]]
        melts += 1
    kept = vid[:quota]
    pad = kept[-1] if kept else last_v
    while len(kept) < quota:
        kept.append({**dict(pad), "key": False})
    out.extend(kept)
    last_v = kept[-1]
    print(
        f"[{i}] {name}: {len(seg)}f section -> {k / GRID_DIV:g}-beat cut "
        f"({quota}f){' (melt)' if melt else ''}"
    )
    cum += k
    i += 1
grid = paths.resolve(GRID_AVI)
write_avi(grid, header, movi_start, out)
print(
    f"wrote {grid} ({len(out)} frames over {cum / GRID_DIV:g} beats, " f"{melts} melts)"
)

# ---- post effects: chroma -> pixel sort (same order and seeds as the UI) ----
cur = grid
if CHROMA["frac"] > 0:
    cur = chroma_databend(cur, paths.resolve(CHROMA_AVI), **CHROMA, seed=SEED + 1)
    print(f"chroma -> {cur}")
if PIXELSORT["frac"] > 0:
    cur = pixel_sort(cur, paths.resolve(SORT_AVI), **PIXELSORT, seed=SEED + 2)
    print(f"pixel sort -> {cur}")

# ---- mux the song in as the only audio track ----
final = paths.resolve(OUTPUT_MP4)
subprocess.run(
    [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        cur,
        "-ss",
        f"{SONG_START:.3f}",
        "-i",
        song,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        final,
    ],
    check=True,
)
print(f"wrote {final} ({ffmpeg.duration(final):.1f}s)")
