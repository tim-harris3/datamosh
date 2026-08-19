# Roadmap: beat-sync as a library helper (`datamosh/beat.py`)

Goal: promote recipes/beat_mosh.py's beat-grid machinery into library helpers
so users build beat-synced MoshScripts themselves, and the recipe shrinks to a
thin policy layer. One PR.

## Load-bearing findings from the recipe

- The exact grid math (beat_mosh.py ~183–196): `unit = (60/bpm)/GRID_DIV`
  seconds; per-section snapped units clamped to `[k_lo, k_hi]`; and the
  drift-killer: `quota = round((cum+k)*unit*fps) - round(cum*unit*fps)` with
  `cum` an integer running total of grid units — boundaries computed against
  the running total, never per-section.
- `len(seg)` counts frames only because the conform step uses `-an` — the
  library version must count `stream == "v"` chunks or audio-bearing moshables
  silently double cut lengths.
- Trim/freeze-pad and melt map 1:1 onto existing ops: `FrameQuota` already
  pads exactly like the recipe; melt = `DeleteKeyframe` after `ClassicMosh`.
- **Regression risk**: `run_script` rewrites the whole output AVI after every
  entry — a full song at sixteenth-note cuts is hundreds of entries → O(n²)
  I/O. Needs an opt-out (below).
- "Behavior-preserving" = structurally identical (same grid, pool discipline,
  effects), not byte-identical per SEED: moving mosh rolls from global random
  to the engine's derived per-entry seeds necessarily changes RNG streams.

## API (`datamosh/beat.py`, new — consumes script.py types, doesn't extend it)

```python
@dataclass(frozen=True)
class BeatGrid:
    bpm: float; fps: float; div: int = 4      # div=4 → sixteenth notes
    def frame_at(self, units: int) -> int     # round(units*unit_seconds*fps)
        # THE interface: all placement goes through this (drift-free), and a
        # future onset-detected non-uniform grid reimplements just this.
    def quota(self, start_units, length_units) -> int
    def snap_units(self, n_frames, lo=1, hi=None) -> int   # round(): half-to-even
    def clamp_range(self, min_beats, max_beats) -> tuple[int, int]

@dataclass(frozen=True)
class Placement: start_units: int; units: int; frames: int

def place_sections(frame_counts, grid, target_seconds, *,
                   min_beats=0.25, max_beats=4.0) -> list[Placement]

@dataclass(frozen=True)
class SectionRef: avi: str; section: int; vframes: int

def load_section_refs(avis) -> list[SectionRef]   # video-only frame counts!
def cycle_shuffled(items, rng)     # infinite shuffled no-repeat-per-cycle iter
def section_pool(avis, rng)        # cycle_shuffled(load_section_refs(avis), rng)

def entries_from_beats(refs, grid, target_seconds, *,
                       min_beats=0.25, max_beats=4.0,
                       ops_for=None) -> tuple[list[Entry], list[Placement]]
    # each Entry: (avi, section) + ops_for(i, ref, placement) with
    # FrameQuota(count=placement.frames) appended LAST (trim-after-mosh order)
```

`ops_for` keeps policy (melt rolls, escalation) in the caller — beat.py stays
policy-free and randomness lives in the caller's rng. Nothing touches global
random.

## Supporting changes

- **`ffmpeg.mux_audio(video_src, audio_src, dst, *, audio_start=0.0, crf=18,
  preset="medium", audio_bitrate="192k")`** — replace-all-audio + H.264/AAC
  mp4 (flags lifted from the recipe). Conform/letterbox stays in the recipe
  (folder-workflow policy, not a primitive).
- **`MoshScript.checkpoint: bool = True`** in script.py — guards the per-entry
  `write_avi`; final write always happens. Serialized only when False (sparse
  pattern). Beat recipe sets `checkpoint=False` to avoid O(n²) rewrites.
- Exports from `datamosh/__init__.py`.

## Tests (`tests/test_beat.py`)

Pure math (no ffmpeg): cumulative grid never drifts >0.5 frames over ~500
sections at awkward pairs (97.3 bpm/29.97 fps etc.); naive per-section
rounding demonstrably diverges; clamping; placement covers target;
cycle_shuffled is a permutation per cycle and seed-reproducible;
entries_from_beats shapes (FrameQuota last, no disk access with fake refs).

Integration (moshable fixture, 15 fps so bpm=120/div=4 = 1.875 frames/unit,
genuinely non-integer): output video frame count == sum of placement frames ==
`grid.frame_at(end)`; same-seed determinism sha; checkpoint=False bytes ==
checkpoint=True bytes.

## Recipe rewrite (last)

Keep argv/constants/conform/make_moshable loop; replace the placement+mosh
body with BeatGrid + section_pool + entries_from_beats + MoshScript
(`checkpoint=False`, escalate becomes an explicit intensity formula in
`ops_for`, melt never hits entry 0). Bonus: `script.save(...)` makes the whole
beat timeline a shareable, re-runnable artifact. Fix the stale CUT_BEATS
comment while touching the file.

## Onset-detection hook (doc only, no code)

A future `[beats]` extra (librosa) adds `grid_from_beat_times(beat_times,
fps, div)` with the same `frame_at`/`quota`/`snap_units` interface built on
measured beat times — everything downstream already only calls that interface.

## Risks

Video-only frame counts in load_section_refs; FrameQuota must be last;
round() is half-to-even (don't assert half-up in tests); don't skip
checkpoint=False.
