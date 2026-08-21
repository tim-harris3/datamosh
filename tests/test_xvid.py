"""Xvid encoder support: libxvid emits the same moshable shape as native mpeg4
(the packed-bitstream canary), renders stay seed-reproducible, scripts serialize
the encoder sparsely, and an unavailable encoder fails fast with an actionable
error. (No golden hashes: bytes vary per libxvidcore build.)"""

import hashlib
import random

import pytest

from datamosh import (
    Entry,
    MoshConfig,
    MoshScript,
    cli,
    disable_console_logging,
    ffmpeg,
    make_moshable,
    parse_avi,
    run_mosh,
    run_script,
    split_sections,
)

from .conftest import needs_ffmpeg, needs_libxvid


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _section_layout(path):
    _, _, chunks = parse_avi(path)
    return [
        sum(1 for c in sec if c["stream"] == "v") for sec in split_sections(chunks)
    ]


# ---------------------------------------------------------------------------
# flags + detection (no ffmpeg needed)
# ---------------------------------------------------------------------------


def test_video_encode_flags_swaps_only_the_codec():
    m = ffmpeg.video_encode_flags("mpeg4")
    x = ffmpeg.video_encode_flags("xvid")
    assert m == ffmpeg.VIDEO_ENCODE_FLAGS, "the documented alias is the mpeg4 shape"
    assert x[:2] == ["-c:v", "libxvid"]
    assert x[2:] == m[2:], "every non-codec flag must stay identical"
    assert "-bf" in x, "-bf 0 is what keeps libxvid's packed bitstream off"


def test_video_encode_flags_unknown_encoder_raises():
    with pytest.raises(ValueError, match="unknown encoder"):
        ffmpeg.video_encode_flags("h264")


def test_require_encoder_unknown_name_raises():
    with pytest.raises(ValueError, match="unknown encoder"):
        ffmpeg.require_encoder("nope")


def test_require_encoder_xvid_error_is_actionable(monkeypatch):
    monkeypatch.setattr(ffmpeg, "have_libxvid", lambda: False)
    with pytest.raises(RuntimeError, match="libxvid") as e:
        ffmpeg.require_encoder("xvid")
    assert "mpeg4" in str(e.value), "the error must name the working fallback"


def test_require_encoder_mpeg4_never_probes_ffmpeg(monkeypatch):
    def boom():
        raise AssertionError("mpeg4 must not pay the -encoders probe")

    monkeypatch.setattr(ffmpeg, "have_libxvid", boom)
    ffmpeg.require_encoder("mpeg4")


# ---------------------------------------------------------------------------
# fail-fast paths (ffmpeg present, libxvid monkeypatched away)
# ---------------------------------------------------------------------------


@needs_ffmpeg
def test_run_mosh_fails_fast_without_libxvid(tiny_src, tmp_path, monkeypatch):
    monkeypatch.setattr(ffmpeg, "have_libxvid", lambda: False)
    cfg = MoshConfig(source=tiny_src, output=str(tmp_path / "x.avi"), encoder="xvid")
    with pytest.raises(RuntimeError, match="libxvid"):
        run_mosh(cfg)


@needs_ffmpeg
def test_run_script_fails_fast_without_libxvid(moshable, tmp_path, monkeypatch):
    monkeypatch.setattr(ffmpeg, "have_libxvid", lambda: False)
    script = MoshScript(
        output=str(tmp_path / "x.avi"),
        encoder="xvid",
        entries=[Entry(avi=moshable, section=0)],
    )
    with pytest.raises(RuntimeError, match="libxvid"):
        run_script(script)


# ---------------------------------------------------------------------------
# the encode itself (needs a libxvid build)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def xvid_moshable(tiny_src, tmp_path_factory):
    """tiny_src through make_moshable(encoder="xvid"), same seed as `moshable`."""
    dest = tmp_path_factory.mktemp("media") / "xvid_moshable.avi"
    random.seed(99)  # same gap rolls as the mpeg4 `moshable` fixture
    make_moshable(tiny_src, str(dest), gap_range=(0.4, 0.7), encoder="xvid")
    return str(dest)


@needs_libxvid
def test_xvid_moshable_parses_with_same_section_structure(moshable, xvid_moshable):
    assert _section_layout(xvid_moshable) == _section_layout(moshable), (
        "same seed + same forced keyframes must yield the same section layout "
        "from either encoder"
    )


@needs_libxvid
def test_xvid_one_chunk_per_frame(xvid_moshable):
    """The packed-bitstream canary: a packed encode fuses frames into shared
    chunks, so the '00dc' chunk count would drift from ffprobe's packet count."""
    _, _, chunks = parse_avi(xvid_moshable)
    n_video = sum(1 for c in chunks if c["stream"] == "v")
    assert n_video == len(ffmpeg.video_keyflags(xvid_moshable))


@needs_libxvid
def test_run_mosh_xvid_same_seed_same_bytes(tiny_src, tmp_path):
    def render(name):
        cfg = MoshConfig(
            source=tiny_src,
            output=str(tmp_path / name),
            n=3,
            seed=11,
            fixup=False,
            min_shot=0.1,
            encoder="xvid",
        )
        out, _ = run_mosh(cfg)
        return out

    out_a = render("a.avi")
    _, _, chunks = parse_avi(out_a)
    assert sum(1 for c in chunks if c["stream"] == "v") > 0
    assert sha(out_a) == sha(render("b.avi")), "same seed must reproduce the render"


# ---------------------------------------------------------------------------
# script serialization (pure JSON, no libxvid needed)
# ---------------------------------------------------------------------------


def test_script_default_encoder_is_not_serialized(tmp_path):
    script = MoshScript(entries=[Entry(avi="m.avi", section=0)])
    assert "encoder" not in script.to_dict(), (
        "sparse serialization: default scripts must stay loadable by builds "
        "that predate the encoder field"
    )
    path = script.save(str(tmp_path / "default.json"))
    assert MoshScript.load(path).encoder == "mpeg4"


def test_script_xvid_encoder_round_trips(tmp_path):
    script = MoshScript(encoder="xvid", entries=[Entry(avi="m.avi", section=0)])
    assert script.to_dict()["encoder"] == "xvid"
    path = script.save(str(tmp_path / "xvid.json"))
    assert MoshScript.load(path).encoder == "xvid"


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_console_handler():
    """main() enables console logging; don't leak the handler between tests."""
    disable_console_logging()
    yield
    disable_console_logging()


def test_flat_form_help_lists_encoder(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    assert "--encoder" in capsys.readouterr().out


def test_prepare_help_lists_encoder(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["prepare", "--help"])
    assert e.value.code == 0
    assert "--encoder" in capsys.readouterr().out


def test_unknown_encoder_is_an_argparse_choices_error(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--source", "x.avi", "--encoder", "nope"])
    assert e.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
