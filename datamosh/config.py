"""MoshConfig -- one object that holds everything run_mosh() needs for a render.

The first block of fields says WHAT to mosh (source, output, n, seed, ...); the
rest are the effect tunables. Tunable fields carry metadata (bounds, step, group,
help text) that mosh.py uses to auto-generate CLI flags and app.py uses to
auto-generate sliders, so adding a tunable here is the ONLY step needed to expose
it everywhere.

Typical use in a recipe:

    from datamosh import MoshConfig, run_mosh
    cfg = MoshConfig(source="media/sample.avi", output="output/mine.avi",
                     n=12, seed=7, keyframe_delete_prob=0.8)
    run_mosh(cfg)
"""

from dataclasses import dataclass, field, fields, replace


def tunable(default, lo, hi, step=0.01, group="video", help=""):
    """A MoshConfig field with slider/flag metadata. Tuple default = (lo, hi) range."""
    kind = "range" if isinstance(default, tuple) else "float"
    return field(
        default=default,
        metadata={
            "tunable": True,
            "kind": kind,
            "lo": lo,
            "hi": hi,
            "step": step,
            "group": group,
            "help": help,
        },
    )


def structural(default, help=""):
    """A MoshConfig field that is not an effect tunable (no auto slider)."""
    return field(default=default, metadata={"help": help})


@dataclass
class MoshConfig:
    # --- what / where / how much -------------------------------------------------
    source: str | None = structural(
        None,
        help="video file to extract shots from (absolute, or relative to the project root)",
    )
    output: str = structural("output/moshed_output.avi", help="output AVI path")
    n: int = structural(10, help="segments appended per run")
    seed: int | None = structural(
        None, help="RNG seed; same seed = same result (None = different every run)"
    )
    min_shot: float = structural(
        0.20, help="ignore shots shorter than this many seconds"
    )
    scene_threshold: float = structural(
        0.30, help="scene-detection sensitivity (lower = more cuts)"
    )
    reset: bool = structural(
        True, help="start a fresh output file (False = append to an existing one)"
    )
    fixup: bool = structural(
        True, help="also write a more broadly-seekable *_fixed.avi via ffmpeg -c copy"
    )

    # --- video mangles -----------------------------------------------------------
    keyframe_delete_prob: float = tunable(
        0.25,
        0,
        1,
        group="video",
        help="chance to delete the shot's leading keyframe (the classic melt/bloom)",
    )
    short_gop_bias: float = tunable(
        0.0,
        -3,
        6,
        step=0.05,
        group="video",
        help="bias shot pick toward short shots (weight = 1/sec**bias); 0 = uniform",
    )
    escalate: float = tunable(
        2.0,
        0,
        6,
        step=0.1,
        group="video",
        help="0 = constant; >0 ramps duplication amount across the run",
    )
    video_transplant_prob: float = tunable(
        0.15,
        0,
        1,
        group="video",
        help="weld the previous clip's motion (P-frames) onto this clip's keyframe",
    )
    video_shuffle_prob: float = tunable(
        0.30, 0, 1, group="video", help="shuffle the P-frame order"
    )
    video_reverse_prob: float = tunable(
        0.40,
        0,
        1,
        group="video",
        help="reverse the P-frame order (motion runs backwards)",
    )
    video_bounce_prob: float = tunable(
        0.60, 0, 1, group="video", help="ping-pong: P-frames forward then reversed"
    )
    video_skip_prob: float = tunable(
        0.10, 0, 1, group="video", help="drop random P-frames (lurching motion)"
    )
    video_skip_frac: float = tunable(
        0.20, 0, 1, group="video", help="fraction of P-frames dropped when skipping"
    )
    video_databend_prob: float = tunable(
        0.20,
        0,
        1,
        group="video",
        help="byte-corrupt some P-frame payloads (macroblock glitch)",
    )
    video_databend_frac: float = tunable(
        0.20,
        0,
        1,
        group="video",
        help="fraction of P-frames corrupted when databending",
    )

    # --- audio mangles -----------------------------------------------------------
    audio_reverse_prob: float = tunable(
        0.40, 0, 1, group="audio", help="chance to reverse the clip's audio chunk order"
    )
    audio_scramble_prob: float = tunable(
        0.15,
        0,
        1,
        group="audio",
        help="chance to shuffle a window of audio chunks (glitchy skipping)",
    )
    audio_databend_prob: float = tunable(
        0.05,
        0,
        1,
        group="audio",
        help="chance to byte-corrupt some of the clip's audio (digital noise)",
    )
    audio_databend_frac: float = tunable(
        0.5, 0, 1, group="audio", help="fraction of chunks corrupted when databending"
    )

    # --- (lo, hi) ranges: a fresh amount is rolled inside the range each time ----
    dup_frames_range: tuple = tunable(
        (1, 10),
        0,
        100,
        step=1,
        group="video",
        help="how many distinct P-frames to duplicate per segment",
    )
    dup_count_range: tuple = tunable(
        (2, 5),
        0,
        100,
        step=1,
        group="video",
        help="how many extra copies to make of each chosen P-frame",
    )
    video_databend_bytes: tuple = tunable(
        (1, 6), 1, 32, step=1, group="video", help="bytes flipped per corrupted P-frame"
    )
    audio_scramble_window: tuple = tunable(
        (4, 40),
        1,
        100,
        step=1,
        group="audio",
        help="scramble window length (audio chunks)",
    )
    audio_databend_bytes: tuple = tunable(
        (1, 8),
        1,
        32,
        step=1,
        group="audio",
        help="bytes flipped per corrupted audio chunk",
    )
    audio_grain_range: tuple = tunable(
        (1, 3),
        1,
        12,
        step=1,
        group="audio",
        help="loop-stretch grain length (chunks, ~32ms each); smaller = smoother, larger = choppier",
    )


def tunable_fields():
    """Effect-tunable fields, in declaration order (drives CLI flags and UI sliders)."""
    return [f for f in fields(MoshConfig) if f.metadata.get("tunable")]


def float_fields():
    """The scalar tunables (kind 'float'), in declaration order."""
    return [f for f in tunable_fields() if f.metadata["kind"] == "float"]


def range_fields():
    """The (lo, hi) tunables (kind 'range'), in declaration order."""
    return [f for f in tunable_fields() if f.metadata["kind"] == "range"]


def escalation_intensity(escalate, index, total):
    """Intensity multiplier for clip `index` of `total`: ramps 1 -> 1+escalate across
    the run (0 = constant 1.0). Shared by run_mosh and mosh_pass so both escalate
    identically."""
    return 1 + escalate * (index / max(1, total - 1)) if escalate else 1.0


# settings dropped in the 2026-08 granular-stretch refactor; old preset files may still
# carry them, so from_mapping() skips them instead of raising
_REMOVED_SETTINGS = {"audio_seg_range", "audio_loop_range"}


def from_mapping(mapping, base=None):
    """Build a MoshConfig from a plain dict (e.g. a preset), over `base` (or defaults).

    Lists become tuples for range fields; an unknown key raises a helpful error.
    """
    base = base if base is not None else MoshConfig()
    valid = {f.name: f for f in fields(MoshConfig)}
    updates = {}
    for name, val in mapping.items():
        f = valid.get(name)
        if f is None:
            if name in _REMOVED_SETTINGS:
                continue
            raise KeyError(
                f"unknown MoshConfig setting {name!r}; valid names: {', '.join(sorted(valid))}"
            )
        if f.metadata.get("kind") == "range":
            val = tuple(val)
        updates[name] = val
    return replace(base, **updates)


def describe():
    """Print every tunable with its default, bounds, and what it does."""
    for f in tunable_fields():
        m = f.metadata
        print(
            f"{f.name:24} default {str(f.default):>8}  [{m['lo']}..{m['hi']}]  {m['help']}"
        )
