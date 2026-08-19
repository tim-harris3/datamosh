"""Chroma corruption: glitch only the COLOUR while brightness stays razor-sharp.

chroma_databend() decodes the video, corrupts the U/V (colour) planes on a random
fraction of frames, and re-encodes to a mosh-ready AVI -- so you can stop there,
or feed the result straight into run_mosh for byte-level mangling on top.

Modes: "databend" (digital speckle), "shift" (diagonal colour bleed),
"invert" (complementary hues), "bias" (hue push), "gray" (kill a colour axis),
"swap" (exchange U and V), or "random" (fresh mode per corrupted frame).

Copy, rename, change the numbers, run:
    python recipes/your_copy.py    (venv active)
"""

from datamosh import chroma_databend

chroma_databend(
    "media/sample.avi",  # source video (generate with: python -m datamosh.sample)
    "output/chroma_corrupt.avi",  # output
    mode="random",  # one of the modes above, or "random"
    planes="uv",  # which colour planes to touch: "u", "v", or "uv"
    frac=0.30,  # fraction of frames corrupted (rest pass clean)
    seed=5,  # same seed = same result
)

# Want to mosh the result too? Un-comment:
# from datamosh import MoshConfig, run_mosh
# run_mosh(MoshConfig(source="output/chroma_corrupt.avi",
#                     output="output/chroma_corrupt_moshed.avi", n=8, seed=5))
