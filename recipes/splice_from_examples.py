"""Multi-pass splice mosh: bloom one video's motion over another's pixels.

Pipeline:
  1. re-encode the source to a moshable AVI with keyframes at RANDOM timestamps,
     so the file divides into random-length keyframe sections
  2. run_mosh over it, using those keyframe sections as the shot map (scene
     detection would see plain footage as one giant shot)
  3. randomly replace some keyframe sections with sections pulled from the AVIs
     in the examples folder, then delete most of the spliced keyframes so the
     source's motion blooms straight over the spliced pixels
  4. mosh everything again (pass 2) over the merged sections

Copy, rename, change the numbers, run:
    python recipes/your_copy.py    (venv active)

EXAMPLES must point at a folder holding at least one AVI to steal sections from
(any AVIs -- your own renders from output/ work great).
"""

import random

from datamosh import (
    MoshConfig,
    audio_video_ratio,
    delete_tagged_keyframes,
    example_section_pool,
    ffmpeg,
    keyframe_shots,
    make_moshable,
    mosh_pass,
    parse_avi,
    replace_sections,
    run_mosh,
    write_avi,
)

SOURCE = "media/sample.avi"  # footage to mosh (python -m datamosh.sample)
EXAMPLES = "media/examples"  # folder of AVIs to steal sections from
OUTPUT = "output/splice_moshed.avi"
SEED = 5  # same seed = same result
KEYFRAME_GAP = (0.2, 10.0)  # random seconds between forced keyframes
N_SECTIONS = 10  # sections moshed and appended in pass 1
REPLACE_PROB = 0.25  # chance each section is swapped for example material
SPLICED_KEYFRAME_DELETE = 0.75  # fraction of spliced keyframes deleted after the swap

random.seed(SEED)

# 1. moshable conversion with random keyframes
moshable = make_moshable(SOURCE, "output/splice_moshable.avi", gap_range=KEYFRAME_GAP)

# 2. mosh pass 1, keyframe sections as the shot map
cfg = MoshConfig(
    source=moshable,
    output="output/splice_pass1.avi",
    n=N_SECTIONS,
    reset=True,
    fixup=False,
)
run_mosh(cfg, shots=keyframe_shots(moshable))
header, movi_start, chunks = parse_avi(cfg.output)

# 3. splice in example sections, then delete most of their keyframes
chunks = replace_sections(chunks, REPLACE_PROB, example_section_pool(EXAMPLES))
chunks = delete_tagged_keyframes(chunks, SPLICED_KEYFRAME_DELETE)

# 4. mosh pass 2 over the merged sections
chunks = mosh_pass(cfg, chunks, "pass 2", av_ratio=audio_video_ratio(moshable))

write_avi(OUTPUT, header, movi_start, chunks)
print(f"wrote {OUTPUT}")
ffmpeg.fixup(OUTPUT)
