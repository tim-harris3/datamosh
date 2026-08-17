"""Rapid-cut mosh of the 8-14 folder: random base sections, flashback clips spliced in.

Files longer than the folder's average duration are "base videos"; everything
shorter is a "clip". Pass 1 builds the timeline by picking keyframe sections
from random base videos -- split-second takes, so the cutting is relentless --
lightly moshed. Sections are drawn WITHOUT replacement (a base's sections don't
repeat until every one has been used) and the draw is weighted by motion: each
section is scored by its mean P-frame size in the moshable (static shots
compress to almost nothing, moving shots don't), so the film rests on static
shots rarely and stays mobile. Then random sections of the timeline are
replaced with even shorter keyframe sections pulled from the clips, and pass 2
moshes ONLY the spliced sections, hard: their keyframe is almost always
deleted, so the surrounding base motion blooms over each flash. Base sections
pass through untouched, so the average frame of the final video is a
base-video P-frame.

The sources are MJPEG camera footage (every frame a keyframe, no P-frames to
mangle), so each file goes through make_moshable -- re-encoded to MPEG-4 ASP
with keyframes only at random gaps -- at its ORIGINAL resolution and framerate:
the gap ranges below are what set the cutting pace.

Run:
    .venv\\Scripts\\python recipes\\narrative_8_14.py
"""

import os
import random
from dataclasses import replace

from datamosh import (MoshConfig, audio_video_ratio, example_section_pool,
                      ffmpeg, keyframe_shots, make_moshable, mosh_segment,
                      parse_avi, paths, replace_sections, run_mosh,
                      split_sections, write_avi)

FOLDER = "8-14"                          # sources: everything *.AVI in here
OUTPUT = "output/8-14_narrative.avi"
PASS1 = "output/8-14_pass1.avi"          # base timeline before splicing
BASE_DIR = "output/8-14_moshable"        # base moshables
POOL_DIR = "output/8-14_pool"            # clip moshables (flashback section pool)
SEED = 5                                 # same seed = same film
PASS1_SECONDS = 250.0                    # base timeline length; flash inflation
                                         #   must land the raw render past 4:35
REPLACE_PROB = 0.50                      # chance a base section becomes a flashback
EXCLUDE = {"MVI_0181.AVI", "MVI_0182.AVI", "MVI_0171.AVI"}   # left out entirely
OPENER = "MVI_0159.AVI"                  # base video the film opens on (None = random)
MELT_SOURCES = {"MVI_0156.AVI", "MVI_0167.AVI"}  # bases whose I-frames appear less
MELT_KEYFRAME_DELETE = 0.6               # their pass-1 keyframe deletion (vs 0.15)
MELT_PICK_WEIGHT = 2.5                   # their pick weight (P-frames appear more)
BASE_GAP = (0.3, 1.0)                    # base keyframe sections: barely longer than flashes
CLIP_GAP = (0.25, 0.8)                   # flashback sections: blink-length flashes
MOTION_BIAS = 2.5                        # >0 favors high-motion sections; 0 = uniform

# Pass 1, the base timeline: mostly clean playback, occasional mild melt and stutter.
BASE_CFG = MoshConfig(
    output=PASS1, seed=None, reset=False, fixup=False,
    keyframe_delete_prob=0.15,
    video_transplant_prob=0.0,
    video_shuffle_prob=0.03,
    video_reverse_prob=0.03,
    video_bounce_prob=0.05,
    video_skip_prob=0.05, video_skip_frac=0.10,
    video_databend_prob=0.08, video_databend_frac=0.08,
    video_databend_bytes=(1, 3),
    dup_frames_range=(0, 2), dup_count_range=(1, 2),
    escalate=0.0,
    audio_reverse_prob=0.05, audio_scramble_prob=0.05,
    audio_databend_prob=0.02, audio_databend_frac=0.2,
)

# Pass 2, the flashbacks: keyframe almost always deleted (the clip erupts through
# the base motion), shuffled, databent, mangled audio. Duplication and bounce are
# kept moderate so the flashes stay brief and base P-frames dominate the frame
# count. escalate ramps intensity across the film via the pass-2 loop.
CLIP_CFG = MoshConfig(
    output=OUTPUT, seed=None, reset=False, fixup=False,
    keyframe_delete_prob=0.9,
    video_transplant_prob=0.5,
    video_shuffle_prob=0.5,
    video_reverse_prob=0.6,
    video_bounce_prob=0.5,
    video_skip_prob=0.35, video_skip_frac=0.30,
    video_databend_prob=0.65, video_databend_frac=0.40,
    video_databend_bytes=(2, 10),
    dup_frames_range=(1, 4), dup_count_range=(2, 3),
    escalate=1.0,
    audio_reverse_prob=0.7, audio_scramble_prob=0.5,
    audio_databend_prob=0.25, audio_databend_frac=0.5,
)

random.seed(SEED)
base_dir, pool_dir = paths.resolve(BASE_DIR), paths.resolve(POOL_DIR)
os.makedirs(base_dir, exist_ok=True)
os.makedirs(pool_dir, exist_ok=True)

# --- sort the folder into base videos and clips by duration -------------------
folder = paths.resolve(FOLDER)
sources = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
           if f.lower().endswith(".avi") and f not in EXCLUDE]
durations = {src: ffmpeg.duration(src) for src in sources}
average = sum(durations.values()) / len(durations)
bases = [s for s in sources if durations[s] > average]
clips = [s for s in sources if durations[s] <= average]
print(f"{len(sources)} videos, average {average:.1f}s -> "
      f"{len(bases)} base videos, {len(clips)} clips")
for s in bases:
    print(f"  base: {os.path.basename(s)} ({durations[s]:.0f}s)")

# --- flashback pool: every clip made moshable with short keyframe sections ----
# (clear stale moshables first -- reclassification can move a file out of the
# clip set, and example_section_pool picks from every .avi in the folder)
for f in os.listdir(pool_dir):
    if f.lower().endswith(".avi"):
        os.remove(os.path.join(pool_dir, f))
for src in clips:
    name = os.path.splitext(os.path.basename(src))[0]
    make_moshable(src, os.path.join(pool_dir, name + ".avi"), gap_range=CLIP_GAP)
pool = example_section_pool(pool_dir)


_prepped = {}
_unused = {}   # base source -> section indices not yet used (no-repeat draw)

def prep(src):
    """make_moshable a base on first use; return (moshable, shot map, motion weights)."""
    if src not in _prepped:
        name = os.path.splitext(os.path.basename(src))[0]
        m = make_moshable(src, os.path.join(base_dir, name + ".avi"),
                          gap_range=BASE_GAP)
        shot_map = keyframe_shots(m)
        # motion score per section: mean P-frame payload size (static shots
        # compress to almost nothing, camera/subject motion doesn't)
        _, _, mchunks = parse_avi(m)
        msections = split_sections(mchunks)
        weights = [1.0] * len(shot_map)
        if len(msections) == len(shot_map):
            scores = []
            for sec in msections:
                sizes = [len(c["data"]) for c in sec
                         if c["stream"] == "v" and not c["key"]]
                scores.append(sum(sizes) / len(sizes) if sizes else 1.0)
            mean = sum(scores) / len(scores)
            weights = [(x / mean) ** MOTION_BIAS for x in scores]
        _prepped[src] = (m, shot_map, weights)
        _unused[src] = list(range(len(shot_map)))
    return _prepped[src]


def draw_section(src):
    """Motion-weighted draw without replacement (refills only when exhausted)."""
    _, shot_map, weights = prep(src)
    if not _unused[src]:
        _unused[src] = list(range(len(shot_map)))
        print(f"({os.path.basename(src)}: all sections used, refilling)")
    idx = _unused[src]
    j = random.choices(idx, weights=[weights[i] for i in idx], k=1)[0]
    idx.remove(j)
    return shot_map[j]


# --- pass 1: motion-weighted random base sections, lightly moshed -------------
pass1_path = paths.resolve(PASS1)
if os.path.exists(pass1_path):
    os.remove(pass1_path)

planned, picks = 0.0, 0
while planned < PASS1_SECONDS:
    if picks == 0 and OPENER:
        src = next(s for s in bases if os.path.basename(s) == OPENER)
    else:
        src = random.choices(
            bases, weights=[MELT_PICK_WEIGHT if os.path.basename(s) in MELT_SOURCES
                            else 1.0 for s in bases], k=1)[0]
    moshable = prep(src)[0]
    sec = draw_section(src)
    cfg = replace(BASE_CFG, source=moshable, n=1)
    if os.path.basename(src) in MELT_SOURCES:
        cfg = replace(cfg, keyframe_delete_prob=MELT_KEYFRAME_DELETE)
    run_mosh(cfg, shots=[sec])
    planned += sec[1] - sec[0]
    picks += 1
print(f"\npass 1: {picks} base sections, {planned:.0f}s planned")

# --- splice: swap random base sections for flashback sections -----------------
header, movi_start, chunks = parse_avi(pass1_path)
chunks = replace_sections(chunks, REPLACE_PROB, pool)

# --- pass 2: mosh ONLY the spliced sections, hard; base passes through --------
sections = split_sections(chunks)
av = audio_video_ratio(prep(bases[0])[0])
out, prev_pframes, n_spliced = [], None, 0
for i, sec in enumerate(sections):
    if i and any(c.get("spliced") for c in sec):
        intensity = 1 + CLIP_CFG.escalate * (i / max(1, len(sections) - 1))
        moshed, prev_pframes = mosh_segment(
            CLIP_CFG, sec, keep_keyframe=False, donor_pframes=prev_pframes,
            intensity=intensity, av_ratio=av)
        out.extend(moshed)
        n_spliced += 1
    else:
        out.extend(sec)
print(f"pass 2: moshed {n_spliced}/{len(sections)} spliced sections")

out_path = paths.resolve(OUTPUT)
write_avi(out_path, header, movi_start, out)
print(f"wrote {out_path} ({ffmpeg.duration(out_path):.1f}s)")
ffmpeg.fixup(out_path)
