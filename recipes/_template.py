"""Template recipe: every knob listed with its default and what it does.

Copy this file, rename it, un-comment the lines you want to change, and run it:
    .venv\\Scripts\\python recipes\\your_copy.py

Most lines are commented out -- a commented line just means "use the default".
Ranges are (low, high) pairs; a fresh amount inside the range is rolled each time.
"""

from datamosh import MoshConfig, run_mosh

cfg = MoshConfig(
    # --- what / where / how much ---------------------------------------------
    source="media/[z303-as043] Adult Swim 10.30.05.mkv",  # video to extract shots from
    #   (footage with no scene cuts reads as one giant shot -- see splice_from_examples.py)
    output="output/my_mosh.avi",         # where the render lands
    n=10,                                # glitch segments appended per run
    seed=5,                              # same seed = same result (None = new roll every run)
    # min_shot=0.20,                     # ignore shots shorter than this (seconds)
    # scene_threshold=0.30,              # scene-detection sensitivity (lower = more cuts)
    # reset=True,                        # False = append onto an existing output file
    # fixup=True,                        # also write a more-seekable *_fixed.avi copy

    # --- video mangles (probabilities are 0..1) ------------------------------
    # keyframe_delete_prob=0.25,         # chance to delete a shot's leading keyframe (the classic melt)
    # short_gop_bias=0.0,                # >0 prefers short shots; 0 = uniform pick
    # escalate=2.0,                      # >0 ramps duplication intensity across the run
    # video_transplant_prob=0.15,        # weld the previous clip's motion onto this clip
    # video_shuffle_prob=0.30,           # shuffle the P-frame order
    # video_reverse_prob=0.40,           # motion runs backwards
    # video_bounce_prob=0.60,            # ping-pong: forward then reversed
    # video_skip_prob=0.10,              # drop random P-frames (lurching motion)
    # video_skip_frac=0.20,              # fraction of P-frames dropped when skipping
    # video_databend_prob=0.20,          # byte-corrupt P-frames (macroblock glitch)
    # video_databend_frac=0.20,          # fraction of P-frames corrupted when databending

    # --- audio mangles -------------------------------------------------------
    # audio_reverse_prob=0.40,           # play the clip's audio backwards
    # audio_scramble_prob=0.15,          # shuffle a window of audio chunks (glitchy skipping)
    # audio_databend_prob=0.05,          # byte-corrupt some audio (digital noise)
    # audio_databend_frac=0.5,           # fraction of chunks corrupted when databending

    # --- (low, high) ranges --------------------------------------------------
    # dup_frames_range=(1, 10),          # distinct P-frames duplicated per segment
    # dup_count_range=(2, 5),            # extra copies made of each chosen P-frame
    # video_databend_bytes=(1, 6),       # bytes flipped per corrupted P-frame
    # audio_scramble_window=(4, 40),     # scramble window length (audio chunks)
    # audio_databend_bytes=(1, 8),       # bytes flipped per corrupted audio chunk
    # audio_seg_range=(2, 20),           # loop-stretch: audio window length (chunks)
    # audio_loop_range=(2, 10),          # loop-stretch: times each window repeats
)

run_mosh(cfg)
