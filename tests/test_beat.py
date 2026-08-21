"""Beat-grid helpers: drift-free placement math and the beat -> script bridge.

The pure-math tests need no ffmpeg and no files: BeatGrid/place_sections/
entries_from_beats never touch disk (fake SectionRefs prove it). The
integration tests run a beat-placed MoshScript over the moshable fixture --
15 fps against a 120 bpm sixteenth grid is 1.875 frames per unit, genuinely
non-integer, so the drift-killer is actually exercised.
"""

import hashlib
import random

import pytest

from datamosh import (
    BeatGrid,
    Entry,
    FrameQuota,
    MoshScript,
    SectionRef,
    cycle_shuffled,
    entries_from_beats,
    load_section_refs,
    parse_avi,
    place_sections,
    run_script,
)

from .conftest import needs_ffmpeg

# awkward pairs: frames-per-unit far from any integer, where naive per-section
# rounding drifts fast
AWKWARD = [(97.3, 29.97, 4), (120.0, 15.0, 4), (133.7, 23.976, 4), (89.0, 30.0, 3)]


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# ---------------------------------------------------------------------------
# pure grid math
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bpm,fps,div", AWKWARD)
def test_cumulative_grid_never_drifts(bpm, fps, div):
    """Every boundary stays within half a frame of true beat time, and the
    total telescopes exactly to frame_at(end) -- over ~500 sections."""
    grid = BeatGrid(bpm=bpm, fps=fps, div=div)
    rng = random.Random(1)
    counts = (rng.randint(1, 40) for _ in iter(int, 1))  # infinite
    target = 1000 * grid.unit_seconds  # cuts clamp to 1-2 units -> 500+ sections
    placements = place_sections(counts, grid, target, min_beats=0.25, max_beats=0.5)
    assert len(placements) >= 500  # sanity: a real workload
    cum = 0
    total = 0
    for p in placements:
        assert p.start_units == cum  # contiguous: no gaps, no overlaps
        cum += p.units
        total += p.frames
        boundary = grid.frame_at(cum)
        assert abs(boundary - cum * grid.unit_seconds * fps) <= 0.5
    assert total == grid.frame_at(cum)  # the telescoping identity, exactly


def test_naive_per_section_rounding_diverges():
    """The reason the running total exists: summing per-section rounded
    lengths drifts by whole frames where the cumulative grid cannot."""
    grid = BeatGrid(bpm=97.3, fps=29.97, div=4)
    counts = [5] * 500  # 5 frames -> 1 unit at 4.62 frames/unit
    target = 499 * grid.unit_seconds
    placements = place_sections(counts, grid, target)
    end = sum(p.units for p in placements)
    ideal = end * grid.unit_seconds * grid.fps
    naive = sum(round(p.units * grid.unit_seconds * grid.fps) for p in placements)
    assert abs(naive - ideal) > 0.5  # naive: off the song by many frames
    assert abs(sum(p.frames for p in placements) - ideal) <= 0.5  # ours: never


def test_snap_units_clamps():
    grid = BeatGrid(bpm=120, fps=15, div=4)  # 1.875 frames/unit
    assert grid.snap_units(8) == 4  # 8/1.875 = 4.27 -> 4
    assert grid.snap_units(0) == 1  # lo floor: a cut is never 0 units
    assert grid.snap_units(1000, hi=16) == 16
    assert grid.snap_units(8, lo=6) == 6


def test_clamp_range():
    grid = BeatGrid(bpm=120, fps=15, div=4)
    assert grid.clamp_range(0.25, 4.0) == (1, 16)
    assert grid.clamp_range(0.0, 0.0) == (1, 1)  # never a zero-length cut
    # a degenerate hi below lo collapses to lo, not an inverted range
    assert grid.clamp_range(2.0, 0.25) == (8, 8)


def test_placement_covers_target():
    grid = BeatGrid(bpm=97.3, fps=29.97, div=4)
    target = 30.0
    placements = place_sections([7] * 1000, grid, target)
    end = placements[-1].start_units + placements[-1].units
    assert end * grid.unit_seconds >= target  # covered
    # and not overshot by more than the final cut
    prev_end = placements[-1].start_units
    assert prev_end * grid.unit_seconds < target


def test_place_sections_exhaustion_raises():
    grid = BeatGrid(bpm=120, fps=30, div=4)
    with pytest.raises(ValueError, match="ran out of sections"):
        place_sections([10, 10], grid, 60.0)


def test_cycle_shuffled_is_a_permutation_per_cycle():
    items = list(range(9))
    it = cycle_shuffled(items, random.Random(7))
    first = [next(it) for _ in items]
    second = [next(it) for _ in items]
    assert sorted(first) == items  # every item once per cycle
    assert sorted(second) == items
    assert first != items or second != items  # actually shuffled (seed 7 does)


def test_cycle_shuffled_is_seed_reproducible():
    a = cycle_shuffled("abcdef", random.Random(3))
    b = cycle_shuffled("abcdef", random.Random(3))
    assert [next(a) for _ in range(20)] == [next(b) for _ in range(20)]


def test_cycle_shuffled_rejects_empty():
    with pytest.raises(ValueError, match="at least one item"):
        next(cycle_shuffled([], random.Random(0)))


def test_entries_from_beats_shapes():
    """Fake refs, no disk access: entries carry (avi, section), ops_for's ops
    come first, and FrameQuota(count=placement.frames) is always LAST."""
    grid = BeatGrid(bpm=120, fps=15, div=4)
    refs = cycle_shuffled(
        [SectionRef(avi="fake_a.avi", section=i, vframes=4 + i) for i in range(5)],
        random.Random(11),
    )
    seen = []

    def ops_for(i, ref, placement):
        seen.append((i, ref, placement))
        return [{"op": "delete_keyframe"}] if i else []

    entries, placements = entries_from_beats(refs, grid, 8.0, ops_for=ops_for)
    assert len(entries) == len(placements) == len(seen)
    for i, (entry, placement) in enumerate(zip(entries, placements)):
        assert entry.avi == "fake_a.avi" and entry.section is not None
        quota = entry.ops[-1]
        assert isinstance(quota, FrameQuota) and quota.count == placement.frames
        assert len(entry.ops) == (2 if i else 1)  # ops_for's op precedes it
        assert seen[i][0] == i and seen[i][2] == placement


def test_entries_from_beats_default_ops():
    grid = BeatGrid(bpm=120, fps=15, div=4)
    entries, placements = entries_from_beats(
        [SectionRef("x.avi", 0, 6)] * 50, grid, 2.0
    )
    assert all(len(e.ops) == 1 for e in entries)
    assert sum(p.frames for p in placements) == grid.frame_at(
        placements[-1].start_units + placements[-1].units
    )


# ---------------------------------------------------------------------------
# integration: a beat-placed script over the moshable fixture
# ---------------------------------------------------------------------------


def build_beat_script(moshable, output, seed=0):
    """15 fps fixture on a 120 bpm sixteenth grid: 1.875 frames per unit."""
    grid = BeatGrid(bpm=120, fps=15, div=4)
    refs = cycle_shuffled(load_section_refs([moshable]), random.Random(seed))
    melt_rng = random.Random(seed + 1)

    def ops_for(i, ref, placement):
        ops = [{"op": "classic_mosh", "config": {"keyframe_delete_prob": 0.0}}]
        if i and melt_rng.random() < 0.4:
            ops.append({"op": "delete_keyframe"})
        return ops

    entries, placements = entries_from_beats(refs, grid, 1.8, ops_for=ops_for)
    script = MoshScript(
        entries=entries, output=output, seed=seed, fixup=False, checkpoint=False
    )
    return script, grid, placements


@needs_ffmpeg
def test_load_section_refs_counts_video_frames_only(moshable):
    """The audio-bearing fixture is the trap: counting whole sections would
    double every cut length."""
    refs = load_section_refs([moshable])
    _, _, chunks = parse_avi(moshable)
    audio = [c for c in chunks if c["stream"] == "a"]
    assert audio, "fixture must carry audio for this test to mean anything"
    assert sum(r.vframes for r in refs) == sum(
        1 for c in chunks if c["stream"] == "v"
    )
    assert [r.section for r in refs] == list(range(len(refs)))


@needs_ffmpeg
def test_beat_script_lands_exactly_on_the_grid(moshable, tmp_path):
    script, grid, placements = build_beat_script(moshable, str(tmp_path / "a.avi"))
    out, _ = run_script(script)
    _, _, chunks = parse_avi(out)
    vframes = sum(1 for c in chunks if c["stream"] == "v")
    end = placements[-1].start_units + placements[-1].units
    assert vframes == sum(p.frames for p in placements) == grid.frame_at(end)


@needs_ffmpeg
def test_beat_script_same_seed_is_byte_identical(moshable, tmp_path):
    script_a, _, _ = build_beat_script(moshable, str(tmp_path / "a.avi"), seed=5)
    script_b, _, _ = build_beat_script(moshable, str(tmp_path / "b.avi"), seed=5)
    out_a, _ = run_script(script_a)
    out_b, _ = run_script(script_b)
    assert sha(out_a) == sha(out_b)


@needs_ffmpeg
def test_checkpoint_off_matches_checkpoint_on(moshable, tmp_path):
    """checkpoint only changes how often the file is rewritten, never its bytes."""
    script_a, _, _ = build_beat_script(moshable, str(tmp_path / "a.avi"))
    script_b, _, _ = build_beat_script(moshable, str(tmp_path / "b.avi"))
    script_a.checkpoint = False
    script_b.checkpoint = True
    out_a, _ = run_script(script_a)
    out_b, _ = run_script(script_b)
    assert sha(out_a) == sha(out_b)


@needs_ffmpeg
def test_checkpoint_round_trips_sparsely(moshable, tmp_path):
    """checkpoint=False survives save/load; default scripts don't serialize it."""
    script, _, _ = build_beat_script(moshable, str(tmp_path / "a.avi"))
    assert script.checkpoint is False
    loaded = MoshScript.load(script.save(str(tmp_path / "s.json")))
    assert loaded.checkpoint is False
    default = MoshScript(entries=[Entry(avi=moshable, section=0)])
    assert "checkpoint" not in default.to_dict()
