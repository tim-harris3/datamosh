"""Motion-vector analysis (mv.py + the mv-dump verb): field shape/units on a
known-motion fixture, zero fields where nothing moves, the pinned-down ffprobe
schema handling, the codecview overlay, and the CLI table/JSON round-trip."""

import json
import subprocess

import numpy as np
import pytest

from datamosh import cli, disable_console_logging, extract_mv_fields, mv_overlay
from datamosh import mv as mv_mod
from datamosh.ffmpeg import dimensions, video_encode_flags

from .conftest import HAVE_FFMPEG, needs_ffmpeg


@pytest.fixture(autouse=True)
def _clean_console_handler():
    """cli.main() enables console logging; don't leak the handler between tests."""
    disable_console_logging()
    yield
    disable_console_logging()


def _lavfi_moshable(dest, source, vf=None):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-t", "2", "-i", source]
    if vf:
        cmd += ["-vf", vf]
    cmd += [*video_encode_flags("mpeg4"), str(dest)]
    subprocess.run(cmd, check=True)
    return str(dest)


@pytest.fixture(scope="session")
def pan_avi(tmp_path_factory):
    """The known-motion fixture: a 128x96 crop panning 2 px/frame across
    testsrc2, so every genuinely-coded macroblock's true motion is
    (+-4, 0) half-pels."""
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    dest = tmp_path_factory.mktemp("mv") / "pan.avi"
    return _lavfi_moshable(dest, "testsrc2=size=256x192:rate=15", "crop=128:96:x='n*2':y=0")


@pytest.fixture(scope="session")
def static_avi(tmp_path_factory):
    """A truly static fixture (testsrc2 animates, a solid color does not)."""
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    dest = tmp_path_factory.mktemp("mv") / "static.avi"
    return _lavfi_moshable(dest, "color=c=gray:size=128x96:rate=15")


# --- extract_mv_fields on real clips -----------------------------------------


@needs_ffmpeg
def test_field_shape_one_per_frame(pan_avi):
    fields = extract_mv_fields(pan_avi)
    assert len(fields) == 30  # 2 s at 15 fps, cfr
    for f in fields:
        assert f.shape == (6, 8, 2)  # ceil(96/16), ceil(128/16)
        assert f.dtype == np.float32


@needs_ffmpeg
def test_keyframe_field_is_zero(pan_avi):
    fields = extract_mv_fields(pan_avi)
    assert not fields[0].any()


@needs_ffmpeg
def test_pan_median_vector_is_horizontal_4_halfpel(pan_avi):
    fields = extract_mv_fields(pan_avi)
    moving = np.concatenate(
        [f[np.hypot(f[..., 0], f[..., 1]) > 0] for f in fields[2:-2]]
    )
    assert len(moving) > 20, "the pan should move plenty of macroblocks"
    # magnitude + axis, not sign: the pan is horizontal at 2 px = 4 half-pels
    assert np.median(np.abs(moving[:, 0])) == pytest.approx(4.0, abs=1.0)
    assert np.median(np.abs(moving[:, 1])) <= 1.0


@needs_ffmpeg
def test_static_fields_near_zero(static_avi):
    fields = extract_mv_fields(static_avi)
    assert len(fields) > 1
    assert max(float(np.abs(f).max()) for f in fields) < 0.5


@needs_ffmpeg
def test_extraction_is_deterministic(pan_avi):
    a = extract_mv_fields(pan_avi)
    b = extract_mv_fields(pan_avi)
    assert all(np.array_equal(x, y) for x, y in zip(a, b))


def test_unknown_backend_raises():
    with pytest.raises(ValueError, match="backend"):
        extract_mv_fields("whatever.avi", backend="bogus")


# --- the pinned ffprobe schema handling (pure parsing, no ffmpeg needed) -----


def _probe_frames(side_data_lists):
    return [
        {"width": 128, "height": 96, "side_data_list": sd} for sd in side_data_lists
    ]


def test_probe_parser_places_vectors_in_halfpel():
    # one 16x16 vector: center (8, 8) -> cell (0, 0); motion_x=4/scale 2 = +4 half-pel
    frames = _probe_frames(
        [
            None,  # keyframe: no side data at all
            [
                {
                    "side_data_type": "Motion vectors",
                    "motion_vectors": [
                        {"source": -1, "w": 16, "h": 16, "src_x": 10, "src_y": 8,
                         "dst_x": 8, "dst_y": 8, "motion_x": 4, "motion_y": 0,
                         "motion_scale": 2},
                    ],
                }
            ],
        ]
    )
    fields = mv_mod._fields_from_probe(frames, 128, 96, "fake.avi")
    assert len(fields) == 2 and not fields[0].any()
    assert fields[1][0, 0].tolist() == [4.0, 0.0]
    assert not fields[1][1:].any() and not fields[1][0, 1:].any()


def test_probe_parser_collapses_8x8_blocks_by_mean():
    # four inter4v 8x8 vectors inside macroblock (1, 0), mixed motion
    vecs = [
        {"w": 8, "h": 8, "src_x": 0, "src_y": 0, "dst_x": 16 + dx, "dst_y": dy,
         "motion_x": mx, "motion_y": my, "motion_scale": 2}
        for (dx, dy, mx, my) in [(4, 4, 4, 0), (12, 4, 4, 0), (4, 12, 8, 2), (12, 12, 8, 2)]
    ]
    [field] = mv_mod._fields_from_probe(
        _probe_frames([[{"side_data_type": "Motion vectors", "motion_vectors": vecs}]]),
        128, 96, "fake.avi",
    )
    assert field[0, 1].tolist() == [6.0, 1.0]  # mean of 4,4,8,8 / 0,0,2,2 half-pels


def test_probe_parser_falls_back_to_src_minus_dst():
    vecs = [{"w": 16, "h": 16, "src_x": 6, "src_y": 8, "dst_x": 8, "dst_y": 8}]
    [field] = mv_mod._fields_from_probe(
        _probe_frames([[{"side_data_type": "Motion vectors", "motion_vectors": vecs}]]),
        128, 96, "fake.avi",
    )
    assert field[0, 0].tolist() == [-4.0, 0.0]  # (src - dst) full-pel doubled


def test_probe_parser_rejects_unserialized_builds():
    # what every stock ffprobe emits today: the type, no vectors
    frames = _probe_frames([[{"side_data_type": "Motion vectors"}]] * 3)
    with pytest.raises(RuntimeError, match="serialize"):
        mv_mod._fields_from_probe(frames, 128, 96, "fake.avi")


def test_probe_parser_rejects_unknown_vector_shape():
    vecs = [{"weird": 1}]
    frames = _probe_frames([[{"side_data_type": "Motion vectors", "motion_vectors": vecs}]])
    with pytest.raises(RuntimeError, match="unrecognized shape"):
        mv_mod._fields_from_probe(frames, 128, 96, "fake.avi")


# --- mv_overlay --------------------------------------------------------------


@needs_ffmpeg
def test_mv_overlay_writes_decodable_video(pan_avi, tmp_path):
    out = mv_overlay(pan_avi, str(tmp_path / "overlay.avi"))
    assert dimensions(out) == (128, 96)
    decode = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", out, "-f", "null", "-"], capture_output=True
    )
    assert decode.returncode == 0, decode.stderr


# --- the mv-dump verb --------------------------------------------------------


@needs_ffmpeg
def test_mv_dump_prints_a_row_per_frame(pan_avi, capsys):
    cli.main(["mv-dump", pan_avi])
    lines = capsys.readouterr().out.strip().splitlines()
    assert "30 frames" in lines[0] and "8x6 macroblocks" in lines[0]
    assert lines[1].split() == ["frame", "moving", "mean|v|", "dir"]
    rows = [ln.split() for ln in lines[2:]]
    assert len(rows) == 30
    assert [r[0] for r in rows] == [str(i) for i in range(30)]
    assert any(r[3] in ("E", "W") for r in rows), "a horizontal pan should dominate"


@needs_ffmpeg
def test_mv_dump_frame_filter(pan_avi, capsys):
    cli.main(["mv-dump", pan_avi, "--frame", "3"])
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 3 and lines[2].split()[0] == "3"


@needs_ffmpeg
def test_mv_dump_json_round_trips(pan_avi, tmp_path, capsys):
    dump = tmp_path / "fields.json"
    cli.main(["mv-dump", pan_avi, "--json", str(dump)])
    data = json.loads(dump.read_text())
    assert data["unit"] == "half-pel" and data["mb_size"] == 16
    assert data["shape"] == [6, 8]
    assert [f["index"] for f in data["fields"]] == list(range(30))
    fields = extract_mv_fields(pan_avi)
    for entry in data["fields"]:
        arr = np.array(entry["field"], np.float32)
        assert arr.shape == (6, 8, 2)
        assert np.array_equal(arr, fields[entry["index"]])


@needs_ffmpeg
def test_mv_dump_overlay_flag(pan_avi, tmp_path, capsys):
    out = tmp_path / "arrows.avi"
    cli.main(["mv-dump", pan_avi, "--frame", "1", "--overlay", str(out)])
    assert out.exists() and dimensions(str(out)) == (128, 96)
