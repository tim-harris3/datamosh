"""The verb CLI: prepare/inspect round-trips, seed reproducibility, and the
flat-form back-compat guarantee (no first token -> the original mosh parser)."""

import json

import pytest

from datamosh import cli, disable_console_logging, parse_avi, split_sections

from .conftest import needs_ffmpeg


@pytest.fixture(autouse=True)
def _clean_console_handler():
    """main() enables console logging; don't leak the handler between tests."""
    disable_console_logging()
    yield
    disable_console_logging()


@pytest.fixture(scope="module")
def prepared(tiny_src, tmp_path_factory):
    """tiny_src pre-encoded in-process via `datamosh prepare` with a fixed seed."""
    dest = tmp_path_factory.mktemp("cli") / "prepared_moshable.avi"
    cli.main(
        ["prepare", tiny_src, "-o", str(dest), "--gap", "0.4", "0.7", "--seed", "99"]
    )
    return str(dest)


def _section_layout(path):
    """Per-section video frame counts -- the layout contract a seed pins down."""
    _, _, chunks = parse_avi(path)
    return [
        sum(1 for c in sec if c["stream"] == "v") for sec in split_sections(chunks)
    ]


@needs_ffmpeg
def test_prepare_writes_parseable_multi_section_avi(prepared):
    _, _, chunks = parse_avi(prepared)
    sections = split_sections(chunks)
    assert len(sections) >= 2, "0.4-0.7s gaps over 2s should yield several sections"
    video = [c for c in chunks if c["stream"] == "v"]
    assert video[0]["key"], "prepared AVI must start on a keyframe"


@needs_ffmpeg
def test_prepare_same_seed_same_section_layout(tiny_src, prepared, tmp_path):
    again = tmp_path / "again_moshable.avi"
    cli.main(
        ["prepare", tiny_src, "-o", str(again), "--gap", "0.4", "0.7", "--seed", "99"]
    )
    assert _section_layout(str(again)) == _section_layout(prepared)


@needs_ffmpeg
def test_inspect_table_lists_every_section(prepared, capsys):
    cli.main(["inspect", prepared])
    out = capsys.readouterr().out
    _, _, chunks = parse_avi(prepared)
    n = len(split_sections(chunks))
    lines = out.strip().splitlines()
    assert f"{n} sections" in lines[0]
    rows = [ln for ln in lines if ln.split() and ln.split()[0].isdigit()]
    assert len(rows) == n


@needs_ffmpeg
def test_inspect_json_has_per_section_keys(prepared, capsys):
    cli.main(["inspect", prepared, "--json"])
    info = json.loads(capsys.readouterr().out)
    assert (info["width"], info["height"]) == (160, 120)
    assert info["frames"] > 0 and info["duration"] > 0
    assert info["sections"], "expected at least one section"
    for s in info["sections"]:
        assert {"index", "start", "duration", "frames", "keyframes", "audio_chunks"} <= set(s)
    assert [s["index"] for s in info["sections"]] == list(range(len(info["sections"])))


def test_flat_form_help_still_lists_config_flags(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "--source" in out and "--preset" in out
    # the epilog advertises the verbs
    assert "prepare" in out and "inspect" in out and "export" in out


def test_mosh_verb_help_is_the_flat_parser(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["mosh", "--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "--source" in out and "--preset" in out
