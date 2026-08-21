"""The determinism contract: same script + same sources = byte-identical output.

Entries address the moshable fixture by (avi, section) and (avi, f0, f1), which
removes the ffmpeg encode from the loop entirely -- runs must match to the byte
on the same machine. (Golden files are NOT committed: outputs differ across
ffmpeg builds.)
"""

import hashlib
import logging

import pytest

from datamosh import Entry, MoshScript, parse_avi, run_script

from .conftest import needs_ffmpeg


def build_script(moshable, output, seed=0):
    return MoshScript(
        output=output,
        seed=seed,
        fixup=False,
        base_config={"escalate": 0.0},
        entries=[
            Entry(avi=moshable, section=0, ops=[{"op": "dup_frames", "at": 1, "count": 4}]),
            Entry(avi=moshable, f0=1, f1=5, ops=[{"op": "databend", "nbytes": 4}]),
            Entry(
                avi=moshable,
                section=1,
                ops=[
                    {"op": "delete_keyframe"},
                    {"op": "reorder", "pattern": "shuffle"},
                    {"op": "databend", "nbytes": 6},
                    {"op": "audio_scramble"},
                ],
            ),
            Entry(
                avi=moshable,
                section=2,
                ops=[{"op": "transplant", "donor": "prev"}, {"op": "frame_quota", "count": 12}],
            ),
            Entry(avi=moshable, section=0, ops=[{"op": "classic_mosh", "config": {"keyframe_delete_prob": 1.0}}]),
        ],
    )


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


@needs_ffmpeg
def test_same_script_is_byte_identical(moshable, tmp_path):
    out_a, _ = run_script(build_script(moshable, str(tmp_path / "a.avi")))
    out_b, _ = run_script(build_script(moshable, str(tmp_path / "b.avi")))
    assert sha(out_a) == sha(out_b)


@needs_ffmpeg
def test_script_seed_changes_the_roll(moshable, tmp_path):
    out_a, _ = run_script(build_script(moshable, str(tmp_path / "a.avi"), seed=1))
    out_b, _ = run_script(build_script(moshable, str(tmp_path / "b.avi"), seed=2))
    assert sha(out_a) != sha(out_b)


@needs_ffmpeg
def test_saved_script_reproduces_the_original(moshable, tmp_path):
    """save() -> load() -> run() must equal running the in-memory script."""
    script = build_script(moshable, str(tmp_path / "a.avi"))
    out_a, _ = run_script(script)

    path = script.save(str(tmp_path / "script.json"))
    loaded = MoshScript.load(path)
    loaded.output = str(tmp_path / "b.avi")
    out_b, _ = run_script(loaded)
    assert sha(out_a) == sha(out_b)


@needs_ffmpeg
def test_script_output_parses_and_ops_took_effect(moshable, tmp_path):
    out, _ = run_script(build_script(moshable, str(tmp_path / "a.avi")))
    _, _, chunks = parse_avi(out)
    video = [c for c in chunks if c["stream"] == "v"]
    assert video, "script output has no video frames"
    # entry 1's keyframe was deleted, entry 3's was rolled away with prob 1.0:
    # only the two section-0 keyframes may survive at most
    _, _, src_chunks = parse_avi(moshable)
    src_keys = sum(1 for c in src_chunks if c["stream"] == "v" and c["key"])
    out_keys = sum(1 for c in video if c["key"])
    assert out_keys < src_keys


@needs_ffmpeg
def test_frame_range_entry_yields_exact_count(moshable, tmp_path):
    """A no-op frame entry emits exactly f1-f0 video frames -- the mode's point."""
    script = MoshScript(
        output=str(tmp_path / "a.avi"),
        fixup=False,
        entries=[Entry(avi=moshable, f0=0, f1=6, ops=[])],
    )
    out, _ = run_script(script)
    _, _, chunks = parse_avi(out)
    assert sum(1 for c in chunks if c["stream"] == "v") == 6


@needs_ffmpeg
def test_keyframeless_frame_range_warns_at_stream_start(moshable, tmp_path, caplog):
    """A range that skips frame 0 has no keyframe -- melt by design, warned once."""
    caplog.set_level(logging.INFO, logger="datamosh")
    script = MoshScript(
        output=str(tmp_path / "a.avi"),
        fixup=False,
        entries=[Entry(avi=moshable, f0=1, f1=5, ops=[])],
    )
    run_script(script)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("starts without a keyframe" in r.message for r in warnings)


@needs_ffmpeg
def test_overrange_frame_entry_is_skipped(moshable, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="datamosh")
    script = MoshScript(
        output=str(tmp_path / "a.avi"),
        fixup=False,
        entries=[Entry(avi=moshable, f0=0, f1=10_000, ops=[])],
    )
    with pytest.raises(RuntimeError, match="no entry produced any frames"):
        run_script(script)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "materialize failed" in r.message and "video frames" in r.message
        for r in warnings
    )
