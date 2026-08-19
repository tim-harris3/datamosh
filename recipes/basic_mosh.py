"""The minimal mosh: pick a source, set a few knobs, render.

Copy this file, rename it, change the numbers, then run it from the project folder:
    python recipes/your_copy.py    (venv active)

Works best on sources with real scene cuts (shows, edits, trailers). Continuous
footage with no cuts reads as ONE giant shot, so every segment moshes the whole
clip -- for that kind of footage use splice_from_examples.py, which chops it into
random sections first.

No footage handy? `python -m datamosh.sample` generates media/sample.avi.
"""

from datamosh import MoshConfig, run_mosh

cfg = MoshConfig(
    source="media/sample.avi",  # any video; relative to the project folder
    output="output/basic_mosh.avi",  # where the render lands
    n=8,  # how many glitch segments to append
    seed=7,  # same seed = same result; change it to reroll
    keyframe_delete_prob=0.8,  # 0..1 -- higher = more melty transitions
    video_bounce_prob=0.9,  # 0..1 -- ping-pong motion
)

run_mosh(cfg)
