"""Export a moshed AVI to something shareable: mp4, webm, or gif.

Moshed AVIs decode directly -- ffmpeg's mpeg4 decoder soldiers through the
corrupt P-frames, and that decode *is* the moshed look -- so an export is a
faithful recording of how ffmpeg plays the glitch, no fixup remux needed. The
real hazard is seeking: a moshed AVI's index lies, so --start/--duration are
applied output-side (-ss/-t after -i) -- decode from the start and drop
frames, which is accurate and preserves the glitch smear at the cut point.

These encodes are deliberately not `ffmpeg.transcode()` (the ultrafast UI
preview) and deliberately not VIDEO_ENCODE_FLAGS (the moshable encode shape,
the opposite of what a share-ready file wants): mp4 and webm favor quality at
sane defaults, gif gets a proper single-command two-pass palette.
"""

import logging
import os
import subprocess

from . import paths
from .ffmpeg import require_ffmpeg

logger = logging.getLogger(__name__)

EXPORT_FORMATS = ("mp4", "webm", "gif")


def _vf(fps, width):
    """Scale (and optional fps) filterchain for the yuv420p encodes.

    yuv420p needs even dimensions, so with no width we still round both axes
    down to even; with a width, `-2` derives an even height from it.
    """
    scale = (
        f"scale={width}:-2" if width else "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    )
    return (f"fps={fps}," if fps else "") + scale


def _mp4_args(fps, width, loop, crf):
    return [
        "-vf",
        _vf(fps, width),
        "-c:v",
        "libx264",
        "-crf",
        str(18 if crf is None else crf),
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
    ]


def _webm_args(fps, width, loop, crf):
    return [
        "-vf",
        _vf(fps, width),
        "-c:v",
        "libvpx-vp9",
        "-crf",
        str(32 if crf is None else crf),
        "-b:v",
        "0",
        "-row-mt",
        "1",
        "-cpu-used",
        "2",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "libopus",
        "-b:a",
        "128k",
    ]


def _gif_args(fps, width, loop, crf):
    # one command, two passes: palettegen on a split copy, then paletteuse --
    # no temp palette file. fps/width caps are gif-only; a full-rate,
    # full-size gif is enormous, so they default on here and nowhere else.
    graph = (
        f"fps={fps or 15},scale={width or 480}:-1:flags=lanczos,"
        "split[a][b];[a]palettegen=stats_mode=diff[p];"
        "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
    )
    return ["-filter_complex", graph, "-loop", str(loop), "-an"]


_BUILDERS = {"mp4": _mp4_args, "webm": _webm_args, "gif": _gif_args}


def export(
    src,
    dst=None,
    fmt=None,
    *,
    fps=None,
    width=None,
    start=None,
    duration=None,
    loop=0,
    crf=None,
):
    """Transcode a (moshed) video to a shareable format. Returns dst.

    fmt is inferred from dst's extension when not given; with both omitted the
    export lands next to src as an mp4. fps/width re-time and resize the
    output (gif defaults to 15 fps at width 480; mp4/webm stay native unless
    asked). start/duration trim output-side, so ffmpeg time syntax works and
    moshed AVIs' lying indexes are never fast-seeked. loop is the gif loop
    count (0 = forever); crf overrides the mp4/webm quality default (18/32,
    lower = better).
    """
    if fmt is None:
        fmt = os.path.splitext(dst)[1][1:] if dst else "mp4"
    fmt = fmt.lower()
    if fmt not in EXPORT_FORMATS:
        raise ValueError(
            f"unknown export format {fmt!r} -- pick one of: "
            + ", ".join(EXPORT_FORMATS)
        )
    src = paths.resolve(src)
    dst = paths.resolve(dst) if dst else os.path.splitext(src)[0] + "." + fmt
    if os.path.normcase(src) == os.path.normcase(dst):
        raise ValueError(
            f"export would overwrite the source ({src}) -- pass a different dst"
        )
    require_ffmpeg()

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src]
    if start is not None:
        cmd += ["-ss", str(start)]
    if duration is not None:
        cmd += ["-t", str(duration)]
    if fmt != "gif":
        # explicit maps with an optional audio stream, so silent sources export
        cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
    cmd += _BUILDERS[fmt](fps, width, loop, crf)
    cmd.append(dst)
    # stderr passes through: an ffmpeg built without libvpx-vp9/libopus should
    # say so on screen, not vanish into a bare CalledProcessError
    subprocess.run(cmd, check=True)
    logger.info(f"wrote {dst}")
    return dst
