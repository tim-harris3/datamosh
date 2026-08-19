"""The determinism contract: same script + same sources = byte-identical output.

Entries address the moshable fixture by (avi, section), which removes the
ffmpeg encode from the loop entirely -- runs must match to the byte on the same
machine. (Golden files are NOT committed: outputs differ across ffmpeg builds.)
"""

import hashlib

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
