"""Pixel sorting sampler: one short clip, one output per sorting method.

pixel_sort() decodes the video, reorders runs of pixels into monotone streaks on a
fraction of frames, and re-encodes to a mosh-ready AVI -- so you can stop there, or
feed any output straight into run_mosh for byte-level mangling on top.

Two independent choices define a method:
  interval mode -- WHICH pixels sort: "threshold" (midtone runs between LO..HI),
                   "bright" (runs >= HI), "dark" (runs <= LO), "bands" (full rows
                   split at random points -- venetian-blind melt), "full" (whole rows)
  sort key      -- WHAT order they take: "luma", "sat", "hue", "red", "green", "blue"
Plus direction "h"/"v", and every one of them accepts "random" to reroll per frame.

This recipe cuts one short clip and renders the samplers below against it, so you
can flip through output/pixelsort_*.avi and compare like-for-like. frac=1.0 keeps
the effect on every frame while you compare; drop it for the in-and-out flicker.

Copy, rename, change the numbers, run:
    .venv\\Scripts\\python recipes\\your_copy.py
"""

from datamosh import extract_shot, pixel_sort

SOURCE = "output/8-14_moshable/mvi_0156.avi"  # footage to sample from
CLIP = "output/pixelsort_clip.avi"       # the shared demo clip every method sorts
T0, DUR = 12.0, 8.0                      # where to cut the clip (seconds), how long
SEED = 5                                 # same seed = same result
LO, HI = 64, 192                         # luma bounds for threshold/bright/dark
                                         #   (fine for daylight footage; on dark material
                                         #    drop them -- e.g. 40/170 for media/truck.AVI)

# one entry per render: (name, pixel_sort keyword overrides)
DEMOS = [
    ("threshold", dict(mode="threshold")),                  # midtone streaks, the classic
    ("bright",    dict(mode="bright")),                     # highlights smear (lights, signs)
    ("dark",      dict(mode="dark")),                       # shadows smear (heavy at night)
    ("bands",     dict(mode="bands", band_range=(8, 120))), # venetian-blind melt
    ("full",      dict(mode="full")),                       # whole rows, total abstraction
    ("hue_v",     dict(mode="threshold", key="hue", direction="v")),  # colour-ordered, vertical
    ("chaos",     dict(mode="random", key="random", direction="random", frac=0.4)),
]

extract_shot(SOURCE, T0, DUR, CLIP)      # single-keyframe cut, same shape run_mosh uses

for name, overrides in DEMOS:
    kwargs = dict(frac=1.0, lo=LO, hi=HI, seed=SEED)
    kwargs.update(overrides)
    pixel_sort(CLIP, f"output/pixelsort_{name}.avi", **kwargs)

# Want to mosh a favourite? Un-comment:
# from datamosh import MoshConfig, run_mosh
# run_mosh(MoshConfig(source="output/pixelsort_bands.avi",
#                     output="output/pixelsort_bands_moshed.avi", n=8, seed=SEED))
