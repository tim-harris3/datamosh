"""Generate a demo clip so datamosh works out of the box with no footage of your own.

    python -m datamosh.sample            -> media/sample.avi (~24s, 640x360)
    python -m datamosh.sample somewhere/clip.avi

The clip is six 4-second segments from ffmpeg's built-in pattern generators
(testsrc2, mandelbrot, game-of-life, ...), each with its own audio tone -- so it
has real scene cuts for scene detection to find, plenty of motion for P-frame
mangling to smear, and no copyright to worry about. Every recipe and README
example defaults to it; swap in your own footage whenever you're ready.
"""

import argparse
import logging
import os
import subprocess

from . import paths
from .ffmpeg import require_ffmpeg
from .log import enable_console_logging

logger = logging.getLogger(__name__)

DEFAULT_DEST = "media/sample.avi"
SIZE, FPS, SEG_SECONDS = "640x360", 30, 4

# (video lavfi source, audio tone Hz) per segment -- distinct looks so the scene
# cuts are unmistakable, all motion-rich so moshing has vectors to play with
_SEGMENTS = [
    ("testsrc2=size={s}:rate={r}", 220),
    ("mandelbrot=size={s}:rate={r}", 330),
    ("life=size={s}:rate={r}:ratio=0.08:life_color=#66ff88:death_color=#180820", 440),
    ("gradients=size={s}:rate={r}:speed=0.4", 262),
    ("sierpinski=size={s}:rate={r}", 392),
    ("testsrc=size={s}:rate={r}", 523),
]


def generate_sample(dest=DEFAULT_DEST):
    """Write the demo clip to `dest` (project-relative) and return its resolved path."""
    require_ffmpeg()
    dest = paths.resolve(dest)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for vsrc, hz in _SEGMENTS:
        cmd += ["-f", "lavfi", "-t", str(SEG_SECONDS), "-i", vsrc.format(s=SIZE, r=FPS)]
        cmd += ["-f", "lavfi", "-t", str(SEG_SECONDS), "-i", f"sine=frequency={hz}:sample_rate=48000"]
    pairs = "".join(f"[{i * 2}:v][{i * 2 + 1}:a]" for i in range(len(_SEGMENTS)))
    cmd += [
        "-filter_complex",
        f"{pairs}concat=n={len(_SEGMENTS)}:v=1:a=1[v][a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "mpeg4", "-qscale:v", "4",
        "-c:a", "ac3", "-b:a", "192k",
        dest,
    ]
    subprocess.run(cmd, check=True)
    logger.info(f"wrote {dest} ({len(_SEGMENTS) * SEG_SECONDS}s demo clip)")
    return dest


def main():
    enable_console_logging()
    ap = argparse.ArgumentParser(
        prog="python -m datamosh.sample",
        description="Generate a copyright-free demo clip to mosh.",
    )
    ap.add_argument("dest", nargs="?", default=DEFAULT_DEST, help=f"output path (default {DEFAULT_DEST})")
    args = ap.parse_args()
    dest = generate_sample(args.dest)
    print(f"try:  datamosh --source \"{dest}\" --n 8 --seed 7")


if __name__ == "__main__":
    main()
