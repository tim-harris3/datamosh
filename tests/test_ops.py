"""Pure-function tests for the effect primitives and the script op layer.

Nothing here touches ffmpeg -- frames are synthetic {"v": chunk, "key": bool}
dicts with hand-built RIFF-style payloads.
"""

import dataclasses
import random
import struct

import pytest

from datamosh import (
    MoshConfig,
    databend_blob,
    delete_keyframe,
    drop_frames,
    dup_frame,
    from_mapping,
    interleave,
    reorder_pframes,
    slice_frames,
)
from datamosh.script import OP_REGISTRY, Entry, MoshScript, Op


def blob(tag, payload=b"\x00" * 32):
    """A minimal movi chunk: 4-byte fourcc + LE size + payload."""
    return tag + struct.pack("<I", len(payload)) + payload


def frame(i, key=False):
    return {"v": {"data": blob(b"00dc", bytes([i]) * 24), "stream": "v", "key": key}, "key": key}


def frame_ids(frames):
    return [f["v"]["data"][8] for f in frames]


# ---------------------------------------------------------------------------
# effect primitives
# ---------------------------------------------------------------------------


def test_databend_blob_is_deterministic_and_bounded():
    original = blob(b"00dc", bytes(range(64)))
    bent1 = databend_blob(original, 4, skip=16, rng=random.Random(7))
    bent2 = databend_blob(original, 4, skip=16, rng=random.Random(7))
    assert bent1 == bent2, "same rng must produce the same corruption"
    assert bent1 != original
    assert len(bent1) == len(original)
    assert bent1[: 8 + 16] == original[: 8 + 16], "header and skip region untouched"


def test_databend_blob_leaves_tiny_payloads_alone():
    tiny = blob(b"00dc", b"\x01" * 4)
    assert databend_blob(tiny, 4, skip=16, rng=random.Random(1)) == tiny


def test_dup_frame_inserts_pflagged_copies():
    frames = [frame(0, key=True), frame(1)]
    dup_frame(frames, 0, 3)
    assert frame_ids(frames) == [0, 0, 0, 0, 1]
    assert [f["key"] for f in frames] == [True, False, False, False, False]


def test_delete_keyframe_removes_the_nth():
    frames = [frame(0, key=True), frame(1), frame(2, key=True), frame(3)]
    assert delete_keyframe(frames, which=1)
    assert frame_ids(frames) == [0, 1, 3]
    assert not delete_keyframe(frames, which=5)


def test_reorder_reverse_keeps_keyframes_in_front():
    frames = [frame(0, key=True), frame(1), frame(2), frame(3)]
    reorder_pframes(frames, "reverse")
    assert frame_ids(frames) == [0, 3, 2, 1]


def test_reorder_bounce_appends_reversed_copies():
    frames = [frame(0, key=True), frame(1), frame(2)]
    reorder_pframes(frames, "bounce")
    assert frame_ids(frames) == [0, 1, 2, 2, 1]


def test_reorder_shuffle_is_seeded():
    def make():
        return [frame(0, key=True)] + [frame(i) for i in range(1, 8)]

    a, b = make(), make()
    reorder_pframes(a, "shuffle", rng=random.Random(3))
    reorder_pframes(b, "shuffle", rng=random.Random(3))
    assert frame_ids(a) == frame_ids(b)


def test_reorder_unknown_pattern_raises():
    with pytest.raises(ValueError, match="unknown reorder pattern"):
        reorder_pframes([frame(0), frame(1), frame(2)], "spiral")


def test_drop_frames_supports_negative_indices():
    frames = [frame(i) for i in range(5)]
    drop_frames(frames, [0, -1])
    assert frame_ids(frames) == [1, 2, 3]
    with pytest.raises(ValueError, match="out of range"):
        drop_frames(frames, [10])


def test_interleave_spreads_audio_and_keeps_order():
    v = [{"data": f"v{i}", "stream": "v"} for i in range(4)]
    a = [{"data": f"a{i}", "stream": "a"} for i in range(8)]
    out = interleave(v, a)
    assert len(out) == 12
    assert [c["data"] for c in out if c["stream"] == "v"] == [c["data"] for c in v]
    assert [c["data"] for c in out if c["stream"] == "a"] == [c["data"] for c in a]
    assert out[0]["stream"] == "v"


# ---------------------------------------------------------------------------
# slice_frames
# ---------------------------------------------------------------------------


def movi(stream, i, key=False):
    return {"data": f"{stream}{i}", "stream": stream, "key": key}


def interleaved():
    """5 video frames with audio riding between them:
    v0 a0 v1 a1 v2 v3 a2 v4 a3."""
    return [
        movi("v", 0, key=True), movi("a", 0),
        movi("v", 1), movi("a", 1),
        movi("v", 2), movi("v", 3), movi("a", 2),
        movi("v", 4), movi("a", 3),
    ]


def test_slice_frames_is_half_open_and_carries_boundary_audio():
    out = slice_frames(interleaved(), 1, 3)
    assert [c["data"] for c in out] == ["v1", "a1", "v2"]
    assert sum(1 for c in out if c["stream"] == "v") == 3 - 1


def test_slice_frames_end_of_file_keeps_trailing_audio():
    out = slice_frames(interleaved(), 3, 5)
    assert [c["data"] for c in out] == ["v3", "a2", "v4", "a3"]


def test_slice_frames_resolves_negative_indices():
    chunks = interleaved()
    assert slice_frames(chunks, -2, -1) == slice_frames(chunks, 3, 4)
    assert slice_frames(chunks, 0, -1) == slice_frames(chunks, 0, 4)


def test_slice_frames_returns_an_uncopied_sublist():
    chunks = interleaved()
    assert slice_frames(chunks, 1, 3)[0] is chunks[2]


def test_slice_frames_rejects_empty_and_out_of_range():
    chunks = interleaved()
    for f0, f1 in [(2, 2), (3, 1), (0, 6), (-6, 2), (5, 6)]:
        with pytest.raises(ValueError, match="has 5 video frames"):
            slice_frames(chunks, f0, f1)


# ---------------------------------------------------------------------------
# MoshConfig
# ---------------------------------------------------------------------------


def test_config_source_defaults_to_none():
    assert MoshConfig().source is None


def test_from_mapping_rejects_unknown_keys():
    with pytest.raises(KeyError, match="unknown MoshConfig setting"):
        from_mapping({"melt_amount": 11})


def test_from_mapping_coerces_ranges_and_overlays():
    cfg = from_mapping({"dup_frames_range": [3, 9], "n": 4})
    assert cfg.dup_frames_range == (3, 9)
    assert cfg.n == 4


# ---------------------------------------------------------------------------
# script ops: registry and serialization
# ---------------------------------------------------------------------------


def test_every_registered_op_round_trips():
    for name, opcls in OP_REGISTRY.items():
        op = opcls()
        d = op.to_dict()
        assert d["op"] == name
        assert Op.from_dict(d) == op


def test_unknown_op_name_raises():
    with pytest.raises(KeyError, match="unknown op"):
        Op.from_dict({"op": "melt_everything"})


def test_unknown_op_field_raises():
    with pytest.raises(KeyError, match="unknown field"):
        Op.from_dict({"op": "reorder", "patern": "reverse"})


# ---------------------------------------------------------------------------
# Entry / MoshScript validation and serialization
# ---------------------------------------------------------------------------


def test_entry_requires_exactly_one_addressing_mode():
    with pytest.raises(ValueError, match="exactly one addressing mode"):
        Entry()
    with pytest.raises(ValueError, match="exactly one addressing mode"):
        Entry(source="a.avi", t0=0, t1=1, avi="b.avi", section=0)
    with pytest.raises(ValueError, match="exactly one addressing mode"):
        Entry(source="a.avi", t0=0, t1=1, f0=0, f1=3)
    with pytest.raises(ValueError, match="need both avi and section"):
        Entry(avi="b.avi")
    with pytest.raises(ValueError, match="need all of source, t0, t1"):
        Entry(source="a.avi", t0=0)


def test_entry_frame_range_validation():
    Entry(avi="m.avi", f0=0, f1=10)  # valid: half-open frame range
    Entry(avi="m.avi", f0=-5, f1=-1)  # negatives resolve at materialize time
    with pytest.raises(ValueError, match="not both"):
        Entry(avi="m.avi", section=0, f0=0, f1=10)
    with pytest.raises(ValueError, match="need all of avi, f0, f1"):
        Entry(avi="m.avi", f0=0)
    with pytest.raises(ValueError, match="need all of avi, f0, f1"):
        Entry(f0=0, f1=10)
    with pytest.raises(ValueError, match="half-open"):
        Entry(avi="m.avi", f0=3, f1=3)
    with pytest.raises(ValueError, match="half-open"):
        Entry(avi="m.avi", f0=7, f1=2)


def test_frame_range_entry_round_trips():
    entry = Entry(avi="m.avi", f0=2, f1=10, ops=[{"op": "reorder"}])
    d = entry.to_dict()
    assert d["f0"] == 2 and d["f1"] == 10
    assert "section" not in d
    assert Entry.from_dict(d).to_dict() == d


def test_chunks_entries_refuse_serialization():
    entry = Entry(chunks=[{"data": b"", "stream": "v", "key": True}])
    with pytest.raises(ValueError, match="cannot be serialized"):
        entry.to_dict()


def test_script_round_trips_through_dict():
    script = MoshScript(
        output="output/x.avi",
        seed=42,
        base_config={"keyframe_delete_prob": 1.0},
        entries=[
            Entry(avi="m.avi", section=0, ops=[{"op": "reorder", "pattern": "reverse"}]),
            Entry(avi="m.avi", f0=4, f1=-1, ops=[{"op": "databend", "nbytes": 2}]),
            Entry(
                source="s.avi",
                t0=0.0,
                t1=1.0,
                seed=7,
                ops=[{"op": "dup_frames", "at": [1, -1], "count": 3}],
            ),
        ],
    )
    rebuilt = MoshScript.from_dict(script.to_dict())
    assert rebuilt.to_dict() == script.to_dict()
    # ops became real dataclasses on the way in
    assert dataclasses.is_dataclass(rebuilt.entries[0].ops[0])


def test_script_rejects_unknown_fields_and_versions():
    with pytest.raises(KeyError, match="unknown MoshScript field"):
        MoshScript.from_dict({"entires": []})
    with pytest.raises(ValueError, match="unsupported script version"):
        MoshScript(version=99)


def test_script_save_load(tmp_path):
    script = MoshScript(
        output="output/x.avi",
        entries=[Entry(avi="m.avi", section=1, ops=[{"op": "delete_keyframe"}])],
    )
    path = tmp_path / "script.json"
    script.save(str(path))
    assert MoshScript.load(str(path)).to_dict() == script.to_dict()
