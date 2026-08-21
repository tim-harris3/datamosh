"""Export to shareable formats: per-format codec contracts, format inference,
output-side trims, and the `datamosh export` CLI surface."""

import os
import subprocess

import pytest

from datamosh import EXPORT_FORMATS, cli, disable_console_logging, export, ffmpeg

from .conftest import needs_ffmpeg


@pytest.fixture(autouse=True)
def _clean_console_handler():
    """cli.main() enables console logging; don't leak the handler between tests."""
    disable_console_logging()
    yield
    disable_console_logging()


def _video_stream(path, *entries):
    """ffprobe the requested first-video-stream entries as a list of strings."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=" + ",".join(entries),
            "-of", "csv=p=0", path,
        ],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return out.split(",")


@needs_ffmpeg
def test_mp4_is_h264_yuv420p(moshable, tmp_path):
    dst = str(tmp_path / "out.mp4")
    assert export(moshable, dst, "mp4") == dst
    assert os.path.getsize(dst) > 0
    assert _video_stream(dst, "codec_name", "pix_fmt") == ["h264", "yuv420p"]


@needs_ffmpeg
def test_webm_is_vp9_and_format_inferred_from_extension(moshable, tmp_path):
    dst = str(tmp_path / "out.webm")
    export(moshable, dst)  # no fmt: inferred from the .webm extension
    assert _video_stream(dst, "codec_name") == ["vp9"]


@needs_ffmpeg
def test_gif_honors_fps_and_width(moshable, tmp_path):
    dst = str(tmp_path / "out.gif")
    export(moshable, dst, fps=10, width=120)
    codec, width, rate = _video_stream(dst, "codec_name", "width", "avg_frame_rate")
    assert codec == "gif"
    assert int(width) == 120
    num, den = rate.split("/")
    assert abs(float(num) / float(den) - 10) < 0.5


@needs_ffmpeg
def test_default_dst_is_mp4_next_to_src(moshable):
    dst = export(moshable)
    assert dst == os.path.splitext(moshable)[0] + ".mp4"
    assert os.path.getsize(dst) > 0


def test_unknown_format_raises_before_any_subprocess():
    # no ffmpeg, no src file needed: validation fires first
    with pytest.raises(ValueError) as e:
        export("does_not_exist.avi", fmt="mov")
    assert all(f in str(e.value) for f in EXPORT_FORMATS)


@needs_ffmpeg
def test_start_duration_trim_output_side(moshable, tmp_path):
    dst = str(tmp_path / "trim.mp4")
    export(moshable, dst, start="0.5", duration="1")
    assert abs(ffmpeg.duration(dst) - 1.0) < 0.35  # container rounding slack


def test_cli_export_help_lists_the_flags(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["export", "--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "--to" in out and "--fps" in out and "--width" in out
