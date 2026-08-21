"""Shared fixtures: a tiny generated source clip and a moshable AVI made from it.

Everything is generated with ffmpeg's pattern sources at session start -- no media
files live in the repo. Tests that need ffmpeg skip cleanly when it is missing.
"""

import random
import shutil
import subprocess

import pytest

HAVE_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")

needs_ffmpeg = pytest.mark.skipif(
    not HAVE_FFMPEG, reason="ffmpeg/ffprobe not on PATH"
)


def _have_libxvid():
    if not HAVE_FFMPEG:
        return False
    from datamosh.ffmpeg import have_libxvid

    return have_libxvid()


HAVE_LIBXVID = _have_libxvid()

needs_libxvid = pytest.mark.skipif(
    not HAVE_LIBXVID, reason="ffmpeg build lacks the libxvid encoder"
)


@pytest.fixture(scope="session")
def tiny_src(tmp_path_factory):
    """A 2-second 160x120 generated source clip (testsrc2 video + sine audio)."""
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    dest = tmp_path_factory.mktemp("media") / "tiny_src.avi"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-t", "2", "-i", "testsrc2=size=160x120:rate=15",
            "-f", "lavfi", "-t", "2", "-i", "sine=frequency=440:sample_rate=48000",
            "-c:v", "mpeg4", "-qscale:v", "4",
            "-c:a", "ac3", "-b:a", "192k",
            str(dest),
        ],
        check=True,
    )
    return str(dest)


@pytest.fixture(scope="session")
def moshable(tiny_src, tmp_path_factory):
    """tiny_src re-encoded by make_moshable() into several keyframe sections."""
    from datamosh import make_moshable

    dest = tmp_path_factory.mktemp("media") / "moshable.avi"
    random.seed(99)  # make_moshable rolls its keyframe gaps from the global rng
    make_moshable(tiny_src, str(dest), gap_range=(0.4, 0.7))
    return str(dest)
