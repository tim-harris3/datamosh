"""Beat-grid helpers: place keyframe sections on a musical grid and turn the
placement into MoshScript entries, so cuts (and melts, and blooms) land on the
beat.

The core trick is drift-free placement. A grid unit (e.g. a sixteenth note at
div=4) is rarely a whole number of frames, so summing per-section rounded
lengths drifts off the song by a frame every few bars -- audibly late by the
last chorus. Instead, every boundary is computed against an integer running
total of grid units: section frames are `frame_at(cum + k) - frame_at(cum)`,
which telescopes, so the total is always within half a frame of the true beat
time no matter how many sections are placed.

Policy stays with the caller: which sections to draw (`section_pool` /
`cycle_shuffled`), what to do to each one (the `ops_for` callback -- melts,
escalation, anything), and every random decision comes from the caller's
`random.Random`. Nothing here touches the global random module, and only
`load_section_refs` / `section_pool` read from disk -- the grid math and
`entries_from_beats` are pure.

A future `[beats]` extra (librosa) can add onset detection here as
`grid_from_beat_times(beat_times, fps, div)`: an object built on *measured*
beat times exposing the same `frame_at` / `quota` / `snap_units` interface.
All placement already goes through that interface alone, so a non-uniform
detected grid would slot in with no changes downstream.
"""

from dataclasses import dataclass

from . import paths
from .avi import parse_avi
from .script import Entry, FrameQuota
from .sections import split_sections


@dataclass(frozen=True)
class BeatGrid:
    """A uniform musical grid over a constant frame rate.

    `div` is the grid resolution per beat: 4 subdivides each beat into
    sixteenth notes (at 4/4), 1 places only on whole beats. All positions and
    lengths are integer *grid units*; `frame_at` is the single bridge from
    units to frames, and every placement boundary must go through it -- that is
    what keeps the grid drift-free (see the module docstring).
    """

    bpm: float
    fps: float
    div: int = 4

    @property
    def unit_seconds(self):
        """One grid unit in seconds (a sixteenth note at div=4)."""
        return (60.0 / self.bpm) / self.div

    def frame_at(self, units):
        """The frame number where grid position `units` falls.

        THE interface: quota() and all placement derive from it, so a future
        measured-beat grid reimplements just this.
        """
        return round(units * self.unit_seconds * self.fps)

    def quota(self, start_units, length_units):
        """Exact frame count for a cut of `length_units` starting at
        `start_units` -- boundaries are computed against the running total,
        never per-section, so consecutive quotas telescope without drift."""
        return self.frame_at(start_units + length_units) - self.frame_at(start_units)

    def snap_units(self, n_frames, lo=1, hi=None):
        """The grid length nearest to `n_frames` frames, clamped to [lo, hi].

        Note round() is banker's rounding (half-to-even), matching frame_at.
        """
        k = max(round(n_frames / (self.unit_seconds * self.fps)), lo)
        return k if hi is None else min(k, hi)

    def clamp_range(self, min_beats, max_beats):
        """(lo, hi) cut-length bounds in grid units for bounds given in beats
        (each at least 1 unit -- a zero-length cut would stall placement)."""
        k_lo = max(1, round(min_beats * self.div))
        k_hi = max(k_lo, round(max_beats * self.div))
        return k_lo, k_hi


@dataclass(frozen=True)
class Placement:
    """One placed cut: where it starts (grid units), how long it is (grid
    units), and exactly how many frames it must occupy on the timeline."""

    start_units: int
    units: int
    frames: int


def place_sections(frame_counts, grid, target_seconds, *, min_beats=0.25, max_beats=4.0):
    """Place sections onto the grid until `target_seconds` is covered.

    `frame_counts` is an iterable of section video-frame counts (a finite list
    or an infinite iterator); each is snapped to its nearest grid length within
    [min_beats, max_beats] and given a drift-free frame quota against the
    cumulative grid position. Returns the placements in order; the last one is
    the first to reach or pass the target. Raises ValueError if a finite
    iterable runs out first.
    """
    lo, hi = grid.clamp_range(min_beats, max_beats)
    counts = iter(frame_counts)
    placements = []
    cum = 0
    while cum * grid.unit_seconds < target_seconds:
        try:
            n = next(counts)
        except StopIteration:
            raise ValueError(
                f"ran out of sections after {len(placements)} placements "
                f"({cum * grid.unit_seconds:.1f}s of {target_seconds:.1f}s) -- "
                "pass more sections or an infinite pool (cycle_shuffled)"
            ) from None
        k = grid.snap_units(n, lo, hi)
        placements.append(Placement(start_units=cum, units=k, frames=grid.quota(cum, k)))
        cum += k
    return placements


@dataclass(frozen=True)
class SectionRef:
    """A keyframe section by address -- `(avi, section)` exactly as MoshScript
    entries take it -- plus its video-only frame count, so placement never has
    to touch the file again."""

    avi: str
    section: int
    vframes: int


def load_section_refs(avis):
    """One SectionRef per keyframe section of each AVI, in file-then-section
    order.

    Frame counts include only video chunks (`stream == "v"`): moshable AVIs
    can carry interleaved audio, and counting whole sections would silently
    double every cut length.
    """
    refs = []
    for avi in avis:
        _, _, chunks = parse_avi(paths.resolve(avi))
        for idx, sec in enumerate(split_sections(chunks)):
            vframes = sum(1 for c in sec if c["stream"] == "v")
            refs.append(SectionRef(avi=avi, section=idx, vframes=vframes))
    return refs


def cycle_shuffled(items, rng):
    """Infinite iterator over `items` in shuffled order, reshuffling each time
    the pool empties -- no item repeats until every item has been drawn.

    All order comes from `rng` (a random.Random), so the same seed draws the
    same sequence forever.
    """
    items = list(items)
    if not items:
        raise ValueError("cycle_shuffled needs at least one item")
    while True:
        order = list(items)
        rng.shuffle(order)
        yield from order


def section_pool(avis, rng):
    """The standard beat-mosh draw: every keyframe section of every AVI,
    shuffled with no repeats per cycle -- `cycle_shuffled(load_section_refs(
    avis), rng)`."""
    return cycle_shuffled(load_section_refs(avis), rng)


def entries_from_beats(
    refs, grid, target_seconds, *, min_beats=0.25, max_beats=4.0, ops_for=None
):
    """Draw sections from `refs` onto the grid and return matching
    `(entries, placements)` lists, ready for a MoshScript.

    Each drawn SectionRef becomes an `Entry(avi=..., section=...)` whose ops
    are `ops_for(i, ref, placement)` (default none) with
    `FrameQuota(count=placement.frames)` appended LAST -- trim/freeze-pad runs
    after every other op, so a melt or dup bloom still lands the cut exactly on
    the grid. `ops_for` is where policy lives: melts, escalation ramps, and any
    randomness (from the caller's own rng) -- this function rolls nothing.
    """
    drawn = []

    def counts():
        for ref in refs:
            drawn.append(ref)
            yield ref.vframes

    placements = place_sections(
        counts(), grid, target_seconds, min_beats=min_beats, max_beats=max_beats
    )
    entries = []
    for i, (ref, placement) in enumerate(zip(drawn, placements)):
        ops = list(ops_for(i, ref, placement)) if ops_for else []
        ops.append(FrameQuota(count=placement.frames))
        entries.append(Entry(avi=ref.avi, section=ref.section, ops=ops))
    return entries, placements
