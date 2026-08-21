"""Deterministic scripting: an ordered list of source slices -- keyframe
sections or exact frame ranges -- each with explicit per-frame mosh
instructions, executed by run_script().

Where run_mosh() rolls every effect decision from config probabilities and one
global seed, a MoshScript says exactly what happens: entry 2 deletes its
keyframe, duplicates frame 5 three times, reverses its P-frames. Two layers:

* Explicit ops (DupFrames, DeleteKeyframe, Reorder, Databend, Transplant,
  DropFrames, FrameQuota, plus audio ops) -- fully deterministic operations on
  a section's frame list, in listed order.
* ClassicMosh -- the config layer: runs the classic mosh_segment-style rolls
  over the section with a per-entry MoshConfig override and an isolated seed,
  so controlled randomness composes with precise edits.

Nothing here touches the global random module. Ops with inherent randomness
(which bytes to XOR, shuffle order) take an optional seed; when omitted, one is
derived from (script.seed, entry, op index) via SHA-256, so the same script +
same sources = byte-identical output, and reordering entries never shifts
another entry's randomness. Scripts round-trip to JSON via save()/load().
"""

import dataclasses
import hashlib
import importlib.metadata
import json
import logging
import os
import random
import subprocess
from dataclasses import dataclass, field

from . import ffmpeg, paths
from .avi import parse_avi, write_avi
from .config import MoshConfig, from_mapping
from .effects import (
    databend_blob,
    delete_keyframe,
    drop_frames,
    dup_frame,
    interleave,
    mangle_audio,
    mosh_video,
    reorder_pframes,
    scramble_audio,
    stretch_audio,
    transplant_pframes,
)
from .scenes import DEFAULT_AUDIO_VIDEO_RATIO, audio_video_ratio, extract_shot
from .sections import slice_frames, split_sections

logger = logging.getLogger(__name__)

SCRIPT_VERSION = 1

OP_REGISTRY = {}


def register_op(cls):
    """Class decorator: make an Op subclass usable in scripts by its `op` name.

    Registration is what makes {"op": "..."} JSON resolve to the class in
    Op.from_dict. Registering an existing name silently overwrites it --
    deliberate for in-process experimentation (monkeypatching a built-in op in
    a notebook); entry-point plugins go through load_plugin_ops, which turns
    that same overwrite into a hard error.
    """
    OP_REGISTRY[cls.op] = cls
    return cls


PLUGIN_GROUP = "datamosh.ops"
_plugins_loaded = False
_plugin_errors = {}  # entry-point name -> error string, surfaced in unknown-op errors


def load_plugin_ops():
    """Discover and register ops from installed plugin packages (idempotent).

    Scans the "datamosh.ops" entry-point group; loading an entry point imports
    the plugin module, whose own @register_op calls do the registering. This
    runs lazily on the first unknown op name in Op.from_dict, so built-in-only
    scripts pay zero import cost -- call it eagerly when you need the full op
    list up front (UI/tooling). Entry points load in (distribution, entry
    point) name order, so registration is deterministic across environments.

    Two rules are enforced on each entry point's newly registered names:

    * Plugin op names must contain a "." (e.g. "wobble.stutter"). Built-ins
      are never dotted -- that is a promise -- so a plugin can't shadow one.
      Violations are unregistered and recorded, not fatal.
    * An op name whose class *changed* means two distributions claimed it:
      hard RuntimeError naming both. Silent divergence of what an op name
      means across machines is the worst outcome for the determinism
      contract, so collisions fail loudly rather than first-wins.

    A plugin that crashes on import is recorded in _plugin_errors (and appended
    to unknown-op KeyErrors, so a typo and a broken install look different) but
    never takes down scripts that don't use it.
    """
    global _plugins_loaded
    if _plugins_loaded:
        return
    # set immediately: a crashing plugin must not be re-imported on every miss
    _plugins_loaded = True

    def _sort_key(ep):
        dist = getattr(ep, "dist", None)  # can be None on some importlib versions
        return (dist.name if dist is not None else "", ep.name)

    def _label(ep):
        dist = getattr(ep, "dist", None)
        return dist.name if dist is not None else f"entry point {ep.name!r}"

    owners = {}  # op name -> distribution label that registered it
    for ep in sorted(importlib.metadata.entry_points(group=PLUGIN_GROUP), key=_sort_key):
        before = dict(OP_REGISTRY)
        try:
            ep.load()
        except Exception as e:
            OP_REGISTRY.clear()
            OP_REGISTRY.update(before)  # drop anything a half-imported module left
            _plugin_errors[ep.name] = f"{type(e).__name__}: {e}"
            logger.warning(f"plugin {ep.name!r} failed to load: {e}")
            continue
        for name, cls in list(OP_REGISTRY.items()):
            if before.get(name) is cls:
                continue
            if name in before:
                raise RuntimeError(
                    f"op name {name!r} is claimed by both "
                    f"{owners.get(name, 'the datamosh built-ins')} and {_label(ep)}; "
                    f"plugin op names must be unique"
                )
            if "." not in name:
                del OP_REGISTRY[name]
                _plugin_errors[ep.name] = (
                    f"op name {name!r} lacks the required dot prefix "
                    f"(plugin ops must be namespaced, e.g. 'wobble.stutter')"
                )
                logger.warning(f"plugin {ep.name!r}: {_plugin_errors[ep.name]}")
                continue
            owners[name] = _label(ep)


def _derive_rng(script_seed, entry_key, tag, explicit_seed=None):
    """An isolated random.Random for one op (or the entry's audio stretch).

    explicit_seed pins the op independently of its position; otherwise the seed
    is SHA-256 of (script seed, entry key, op tag) -- stable across Python
    versions and runs, unlike hash().
    """
    if explicit_seed is not None:
        return random.Random(explicit_seed)
    digest = hashlib.sha256(f"{script_seed}:{entry_key}:{tag}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _resolve_index(i, n, what="frame"):
    """Python-style index resolution with a clear out-of-range error."""
    j = i + n if i < 0 else i
    if not 0 <= j < n:
        raise ValueError(f"{what} index {i} out of range (section has {n} {what}s)")
    return j


class OpContext:
    """Everything an op can see and mutate while its entry renders.

    Stable plugin API (safe to rely on from third-party ops):

    frames -- the entry's video frames as [{"v": chunk, "key": bool}, ...]
    audio  -- the entry's audio chunk list (pre-stretch)
    entry_index -- position of this entry in the script
    base_config -- the script's MoshConfig overlay base
    keep_keyframe_default -- whether this entry starts the output stream
    rng_for(op_index, explicit_seed) -- the op's isolated random.Random. All
        randomness must come from here, never the global random module --
        byte-identical reruns are the contract, and a plugin that breaks it
        is buggy.

    Internal, may change between releases (executor plumbing): script_seed and
    entry_key (use rng_for instead), donors, prev_donor, donor_cache, last_v.
    """

    def __init__(
        self,
        *,
        frames,
        audio,
        entry_index,
        script_seed,
        entry_key,
        base_config,
        donors,
        prev_donor,
        donor_cache,
        last_v,
        keep_keyframe_default,
    ):
        self.frames = frames
        self.audio = audio
        self.entry_index = entry_index
        self.script_seed = script_seed
        self.entry_key = entry_key
        self.base_config = base_config
        self.donors = donors  # per-entry genuine P-frame payload lists
        self.prev_donor = prev_donor
        self.donor_cache = donor_cache  # avi path -> P-frame payloads
        self.last_v = last_v  # previous entry's last emitted video chunk
        self.keep_keyframe_default = keep_keyframe_default

    def rng_for(self, op_index, explicit_seed=None):
        return _derive_rng(self.script_seed, self.entry_key, op_index, explicit_seed)


@dataclass
class Op:
    """Base class for script ops. The subclass contract:

    * a class-level `op` name (the JSON "op" key; plugin ops must be
      dot-prefixed, e.g. "wobble.stutter", built-ins never are);
    * a dataclass body whose fields are all JSON-serializable -- that alone
      makes the op round-trip through save()/load();
    * apply(ctx, op_index) mutating ctx.frames / ctx.audio in place;
    * if the op has randomness, an optional `seed: int = None` field fed to
      ctx.rng_for(op_index, self.seed) -- never the global random module.
    """

    def apply(self, ctx, op_index):
        raise NotImplementedError

    def to_dict(self):
        d = {"op": self.op}
        for f in dataclasses.fields(self):
            val = getattr(self, f.name)
            d[f.name] = list(val) if isinstance(val, tuple) else val
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        name = d.pop("op", None)
        if name is None:
            raise KeyError(f"op entry missing 'op' name: {d}")
        opcls = OP_REGISTRY.get(name)
        if opcls is None:
            load_plugin_ops()  # first miss: maybe it's a not-yet-loaded plugin op
            opcls = OP_REGISTRY.get(name)
        if opcls is None:
            msg = f"unknown op {name!r}; valid ops: {', '.join(sorted(OP_REGISTRY))}"
            if _plugin_errors:
                failures = "; ".join(
                    f"{ep}: {err}" for ep, err in sorted(_plugin_errors.items())
                )
                msg += f" (plugin load failures: {failures})"
            raise KeyError(msg)
        valid = {f.name for f in dataclasses.fields(opcls)}
        unknown = set(d) - valid
        if unknown:
            raise KeyError(
                f"unknown field(s) {sorted(unknown)} for op {name!r}; "
                f"valid fields: {', '.join(sorted(valid)) or '(none)'}"
            )
        return opcls(**d)


# ---------------------------------------------------------------------------
# video ops
# ---------------------------------------------------------------------------


@register_op
@dataclass
class DeleteKeyframe(Op):
    """Remove the nth keyframe outright -- the classic melt."""

    op = "delete_keyframe"
    which: int = 0

    def apply(self, ctx, op_index):
        delete_keyframe(ctx.frames, which=self.which)


@register_op
@dataclass
class DupFrames(Op):
    """Insert `count` extra copies of each addressed frame right after it
    (copies are P-flagged, so a duplicated frame smears its motion)."""

    op = "dup_frames"
    at: object = 0  # int or list[int]
    count: int = 2

    def apply(self, ctx, op_index):
        indices = self.at if isinstance(self.at, list) else [self.at]
        n = len(ctx.frames)
        resolved = sorted({_resolve_index(i, n) for i in indices}, reverse=True)
        for j in resolved:  # back-to-front so earlier indices stay valid
            dup_frame(ctx.frames, j, self.count)


@register_op
@dataclass
class Reorder(Op):
    """Reorder the section's P-frames; keyframes stay anchored at the front.
    Patterns: reverse, shuffle (seeded), bounce (ping-pong)."""

    op = "reorder"
    pattern: str = "reverse"
    seed: int = None

    def apply(self, ctx, op_index):
        reorder_pframes(
            ctx.frames, self.pattern, rng=ctx.rng_for(op_index, self.seed)
        )


@register_op
@dataclass
class DropFrames(Op):
    """Delete the frames at exactly these indices."""

    op = "drop_frames"
    frames: list = field(default_factory=list)

    def apply(self, ctx, op_index):
        drop_frames(ctx.frames, self.frames)


@register_op
@dataclass
class Databend(Op):
    """XOR `nbytes` random bytes inside each addressed frame's payload
    (skipping the VOP start code). frames=None corrupts every P-frame."""

    op = "databend"
    frames: list = None
    nbytes: int = 4
    seed: int = None

    def apply(self, ctx, op_index):
        rng = ctx.rng_for(op_index, self.seed)
        n = len(ctx.frames)
        if self.frames is None:
            targets = [j for j, f in enumerate(ctx.frames) if not f["key"]]
        else:
            targets = [_resolve_index(i, n) for i in self.frames]
        for j in targets:
            f = ctx.frames[j]
            f["v"] = dict(f["v"])
            f["v"]["data"] = databend_blob(f["v"]["data"], self.nbytes, skip=16, rng=rng)


@register_op
@dataclass
class Transplant(Op):
    """Overwrite this section's P-frame payloads with donor motion: "prev" uses
    the previous entry's genuine P-frames (like run_mosh's transplant chain),
    an int uses that entry's capture, `avi` pulls from an explicit file."""

    op = "transplant"
    donor: object = "prev"  # "prev" | entry index
    avi: str = None

    def apply(self, ctx, op_index):
        if self.avi is not None:
            path = paths.resolve(self.avi)
            if path not in ctx.donor_cache:
                _, _, chunks = parse_avi(path)
                ctx.donor_cache[path] = [
                    c["data"] for c in chunks if c["stream"] == "v" and not c["key"]
                ]
            payloads = ctx.donor_cache[path]
        elif self.donor == "prev":
            payloads = ctx.prev_donor
        else:
            j = self.donor
            if not isinstance(j, int) or not 0 <= j < len(ctx.donors):
                raise ValueError(
                    f"transplant donor {self.donor!r} is not 'prev', a prior entry "
                    f"index (< {len(ctx.donors)}), or an avi path"
                )
            payloads = ctx.donors[j]
        if not payloads:
            logger.warning(
                f"  transplant: no donor P-frames available (entry {ctx.entry_index}); skipped"
            )
            return
        transplant_pframes(ctx.frames, payloads)


@register_op
@dataclass
class FrameQuota(Op):
    """Force the section to exactly `count` video frames: trim the tail, or pad
    with freeze copies of the last frame (beat-grid trick). An emptied section
    pads from the previous entry's last emitted frame."""

    op = "frame_quota"
    count: int = 0
    pad: str = "freeze"

    def apply(self, ctx, op_index):
        if self.count < 0:
            raise ValueError(f"frame_quota count must be >= 0, got {self.count}")
        frames = ctx.frames
        del frames[self.count :]
        if len(frames) < self.count:
            if frames:
                src = frames[-1]["v"]
            elif ctx.last_v is not None:
                src = ctx.last_v
            else:
                raise ValueError(
                    f"frame_quota: entry {ctx.entry_index} has no frames to pad from"
                )
            while len(frames) < self.count:
                frames.append({"v": dict(src), "key": False})


# ---------------------------------------------------------------------------
# audio ops
# ---------------------------------------------------------------------------


@register_op
@dataclass
class AudioReverse(Op):
    """Play the section's audio backwards."""

    op = "audio_reverse"

    def apply(self, ctx, op_index):
        ctx.audio.reverse()


@register_op
@dataclass
class AudioScramble(Op):
    """Shuffle a window of audio chunks (whole list when length is None)."""

    op = "audio_scramble"
    start: int = 0
    length: int = None
    seed: int = None

    def apply(self, ctx, op_index):
        aud = ctx.audio
        if len(aud) < 2:
            return
        start = _resolve_index(self.start, len(aud), what="audio chunk")
        length = len(aud) - start if self.length is None else self.length
        scramble_audio(aud, start, length, rng=ctx.rng_for(op_index, self.seed))


@register_op
@dataclass
class AudioDatabend(Op):
    """Byte-corrupt audio chunks: explicit indices, or a seeded `frac` of all."""

    op = "audio_databend"
    chunks: list = None
    frac: float = 0.5
    nbytes: int = 4
    seed: int = None

    def apply(self, ctx, op_index):
        rng = ctx.rng_for(op_index, self.seed)
        aud = ctx.audio
        if self.chunks is not None:
            targets = [_resolve_index(i, len(aud), what="audio chunk") for i in self.chunks]
        else:
            targets = [j for j in range(len(aud)) if rng.random() < self.frac]
        for j in targets:
            aud[j]["data"] = databend_blob(aud[j]["data"], self.nbytes, rng=rng)


# ---------------------------------------------------------------------------
# the config layer
# ---------------------------------------------------------------------------


@register_op
@dataclass
class ClassicMosh(Op):
    """Run the classic config-driven rolls (transplant/dup/keyframe/reorder/
    databend + audio mangles) over this section with an isolated seed.

    `config` overlays the script's base_config (unknown keys raise, same as
    presets). keep_keyframe=None keeps it automatically when this entry starts
    the output stream."""

    op = "classic_mosh"
    config: dict = None
    seed: int = None
    intensity: float = 1.0
    keep_keyframe: bool = None

    def apply(self, ctx, op_index):
        cfg = from_mapping(self.config or {}, base=ctx.base_config)
        rng = ctx.rng_for(op_index, self.seed)
        keep = (
            ctx.keep_keyframe_default
            if self.keep_keyframe is None
            else self.keep_keyframe
        )
        ctx.frames[:] = mosh_video(
            cfg,
            ctx.frames,
            keep_keyframe=keep,
            donor_pframes=ctx.prev_donor,
            intensity=self.intensity,
            rng=rng,
        )
        mangle_audio(cfg, ctx.audio, rng=rng)


# ---------------------------------------------------------------------------
# Entry / MoshScript
# ---------------------------------------------------------------------------


@dataclass
class Entry:
    """One ordered timeline slot: a keyframe section plus its instructions.

    Exactly one addressing mode:
      source + t0 + t1  -- extract this time range from a video (re-encoded to a
                           clean single-keyframe moshable AVI, like run_mosh)
      avi + section     -- the nth keyframe section of an existing moshable AVI
                           (split_sections; no re-encode, most reproducible)
      avi + f0 + f1     -- video frames [f0, f1) of an existing moshable AVI
                           (half-open, Python-style negatives resolved against
                           the file's frame count; yields exactly f1-f0 frames
                           before ops, no re-encode). A range not starting on a
                           keyframe has none -- melt by design.
      chunks            -- a pre-split in-memory chunk list (advanced; not
                           JSON-serializable)
    """

    source: str = None
    t0: float = None
    t1: float = None
    avi: str = None
    section: int = None
    f0: int = None
    f1: int = None
    chunks: list = None
    ops: list = field(default_factory=list)
    seed: int = None  # overrides the entry's derived rng stream
    audio_grain: tuple = (1, 3)  # grain range for the automatic stretch
    demote_extra_keyframes: bool = True  # mpeg4 hard-cut slip workaround

    def __post_init__(self):
        modes = [
            self.source is not None or self.t0 is not None or self.t1 is not None,
            self.avi is not None
            or self.section is not None
            or self.f0 is not None
            or self.f1 is not None,
            self.chunks is not None,
        ]
        if sum(modes) != 1:
            raise ValueError(
                "Entry needs exactly one addressing mode: source+t0+t1, "
                "avi+section, avi+f0+f1, or chunks"
            )
        if modes[0] and (self.source is None or self.t0 is None or self.t1 is None):
            raise ValueError("time-range entries need all of source, t0, t1")
        if modes[1]:
            by_frames = self.f0 is not None or self.f1 is not None
            if self.section is not None and by_frames:
                raise ValueError("avi entries take either section or f0+f1, not both")
            if by_frames:
                if self.avi is None or self.f0 is None or self.f1 is None:
                    raise ValueError("frame-range entries need all of avi, f0, f1")
                if self.f0 >= 0 and self.f1 >= 0 and self.f1 <= self.f0:
                    raise ValueError(
                        f"frame range [{self.f0}, {self.f1}) is empty "
                        "(ranges are half-open: f1 must exceed f0)"
                    )
            elif self.avi is None or self.section is None:
                raise ValueError("section entries need both avi and section")
        self.audio_grain = tuple(self.audio_grain)
        self.ops = [Op.from_dict(o) if isinstance(o, dict) else o for o in self.ops]

    def to_dict(self):
        if self.chunks is not None:
            raise ValueError(
                "chunks-mode entries hold raw in-memory data and cannot be "
                "serialized; use source+t0+t1 or avi+section for savable scripts"
            )
        d = {}
        if self.source is not None:
            d.update(source=self.source, t0=self.t0, t1=self.t1)
        elif self.section is not None:
            d.update(avi=self.avi, section=self.section)
        else:
            d.update(avi=self.avi, f0=self.f0, f1=self.f1)
        if self.seed is not None:
            d["seed"] = self.seed
        if self.audio_grain != (1, 3):
            d["audio_grain"] = list(self.audio_grain)
        if not self.demote_extra_keyframes:
            d["demote_extra_keyframes"] = False
        d["ops"] = [op.to_dict() for op in self.ops]
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        valid = {f.name for f in dataclasses.fields(cls)}
        unknown = set(d) - valid
        if unknown:
            raise KeyError(
                f"unknown Entry field(s) {sorted(unknown)}; "
                f"valid fields: {', '.join(sorted(valid))}"
            )
        return cls(**d)


@dataclass
class MoshScript:
    """An ordered, JSON-serializable mosh timeline: entries + global settings."""

    entries: list = field(default_factory=list)
    output: str = "output/scripted.avi"
    seed: int = 0
    reset: bool = True
    fixup: bool = True
    base_config: dict = field(default_factory=dict)  # ClassicMosh overlay base
    version: int = SCRIPT_VERSION

    def __post_init__(self):
        if self.version != SCRIPT_VERSION:
            raise ValueError(
                f"unsupported script version {self.version!r} "
                f"(this build reads version {SCRIPT_VERSION})"
            )
        self.entries = [
            Entry.from_dict(e) if isinstance(e, dict) else e for e in self.entries
        ]

    def add(self, entry):
        """Append an Entry (chainable)."""
        self.entries.append(entry)
        return self

    def to_dict(self):
        return {
            "version": self.version,
            "output": self.output,
            "seed": self.seed,
            "reset": self.reset,
            "fixup": self.fixup,
            "base_config": {
                k: list(v) if isinstance(v, tuple) else v
                for k, v in self.base_config.items()
            },
            "entries": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        valid = {f.name for f in dataclasses.fields(cls)}
        unknown = set(d) - valid
        if unknown:
            raise KeyError(
                f"unknown MoshScript field(s) {sorted(unknown)}; "
                f"valid fields: {', '.join(sorted(valid))}"
            )
        return cls(**d)

    def save(self, path):
        """Write the script as JSON (raises on failure -- a lost script is a bug)."""
        path = paths.resolve(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)
        return path

    @classmethod
    def load(cls, path):
        with open(paths.resolve(path), encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))


# ---------------------------------------------------------------------------
# executor
# ---------------------------------------------------------------------------


def _demote_extra_keyframes(video_chunks):
    """Flip any keyframe after the first to a P-flag (the mpeg4 encoder can slip
    a second keyframe on hard scene cuts, which would break index-0 addressing)."""
    seen = False
    demoted = 0
    for c in video_chunks:
        if c["key"]:
            if seen:
                c["key"] = False
                demoted += 1
            seen = True
    return demoted


def _materialize(entry, i, temp, avi_cache, av_ratios):
    """Turn an entry into (header, movi_start, chunks, av_ratio); chunks are a
    fresh copy the ops may mutate freely."""
    if entry.chunks is not None:
        return None, None, [dict(c) for c in entry.chunks], DEFAULT_AUDIO_VIDEO_RATIO
    if entry.avi is not None:
        path = paths.resolve(entry.avi)
        if path not in avi_cache:
            header, movi_start, chunks = parse_avi(path)
            avi_cache[path] = (header, movi_start, chunks, split_sections(chunks))
        header, movi_start, chunks, sections = avi_cache[path]
        if entry.section is not None:
            if not 0 <= entry.section < len(sections):
                raise ValueError(
                    f"entry {i}: section {entry.section} out of range "
                    f"({os.path.basename(path)} has {len(sections)} sections)"
                )
            seg = sections[entry.section]
        else:
            try:
                seg = slice_frames(chunks, entry.f0, entry.f1)
            except ValueError as e:
                raise ValueError(f"entry {i}: {e}") from e
        if path not in av_ratios:
            av_ratios[path] = audio_video_ratio(path)
        return header, movi_start, [dict(c) for c in seg], av_ratios[path]
    src = paths.resolve(entry.source)
    if not os.path.exists(src):
        raise RuntimeError(f"entry {i}: source not found: {src}")
    extract_shot(src, entry.t0, entry.t1 - entry.t0, temp)
    header, movi_start, chunks = parse_avi(temp)
    # av_ratio probed from the extracted moshable, not the original: extract_shot
    # re-encodes audio to AC3 which can silently resample (see run_mosh)
    if src not in av_ratios:
        av_ratios[src] = audio_video_ratio(temp)
    return header, movi_start, chunks, av_ratios[src]


def run_script(script, *, progress=None):
    """Execute a MoshScript and return (output_path, fixed_path_or_None).

    Entries render strictly in order; each entry's ops run in listed order over
    its video frame list and pre-stretch audio list, then the audio is
    granular-stretched to match the video duration and interleaved. Determinism
    contract: same script + same source files (+ same ffmpeg build for
    time-range entries) = byte-identical output.
    """
    ffmpeg.require_ffmpeg()
    output = paths.resolve(script.output)

    if script.reset and os.path.exists(output):
        os.remove(output)

    template_header = template_movi_start = None
    if os.path.exists(output):
        template_header, template_movi_start, out_chunks = parse_avi(output)
    else:
        out_chunks = []

    base_config = from_mapping(script.base_config, base=MoshConfig())
    temp = os.path.join(os.path.dirname(output) or ".", ".mosh_script_tmp.avi")
    avi_cache = {}  # avi path -> (header, movi_start, chunks, sections)
    av_ratios = {}  # source path -> audio chunks per video frame
    donor_cache = {}  # transplant avi path -> P-frame payloads
    donors = []  # per-entry genuine P-frame captures
    prev_donor = None
    last_v = None  # last emitted video chunk, for cross-entry freeze padding
    total = len(script.entries)

    for i, entry in enumerate(script.entries):
        try:
            header, movi_start, seg, av_ratio = _materialize(
                entry, i, temp, avi_cache, av_ratios
            )
        except (subprocess.CalledProcessError, ValueError) as e:
            logger.warning(f"[{i}] materialize failed ({e}); skipped")
            donors.append([])
            prev_donor = None
            continue
        if template_header is None and header is not None:
            template_header, template_movi_start = header, movi_start

        video = [c for c in seg if c["stream"] == "v"]
        audio = [c for c in seg if c["stream"] == "a"]
        if not video:
            logger.warning(f"[{i}] no video frames; skipped")
            donors.append([])
            prev_donor = None
            continue
        if entry.demote_extra_keyframes:
            demoted = _demote_extra_keyframes(video)
            if demoted:
                logger.info(f"[{i}] demoted {demoted} extra keyframe(s)")

        # capture genuine motion before any op can overwrite it
        capture = [c["data"] for c in video if not c["key"]]
        donors.append(capture)

        entry_key = entry.seed if entry.seed is not None else i
        stream_start = len(out_chunks) == 0
        ctx = OpContext(
            frames=[{"v": c, "key": c["key"]} for c in video],
            audio=audio,
            entry_index=i,
            script_seed=script.seed,
            entry_key=entry_key,
            base_config=base_config,
            donors=donors[:-1],  # an entry may not donate to itself
            prev_donor=prev_donor,
            donor_cache=donor_cache,
            last_v=last_v,
            keep_keyframe_default=stream_start,
        )

        for j, op in enumerate(entry.ops):
            try:
                op.apply(ctx, j)
            except Exception as e:
                raise type(e)(f"entry {i}, op {j} ({op.op}): {e}") from e

        if stream_start and (not ctx.frames or not ctx.frames[0]["key"]):
            logger.warning(
                f"[{i}] output starts without a keyframe -- most players "
                "will show garbage until the first keyframe (honoring the script)"
            )

        vout = [f["v"] for f in ctx.frames]
        aout = []
        if ctx.audio and vout:
            target = max(1, round(len(vout) * av_ratio))
            aout = stretch_audio(
                ctx.audio,
                target,
                entry.audio_grain,
                rng=_derive_rng(script.seed, entry_key, "stretch"),
            )
        moshed = interleave(vout, aout)

        logger.info(
            f"[{i}] {len(video)}->{len(vout)} vframes, "
            f"{len(entry.ops)} op(s), keyframe "
            f"{'kept' if any(f['key'] for f in ctx.frames) else 'gone'}"
        )
        if progress:
            progress((i + 1) / total, f"entry {i + 1}/{total}")

        out_chunks.extend(moshed)
        if vout:
            last_v = vout[-1]
        prev_donor = capture
        if template_header is not None:
            write_avi(output, template_header, template_movi_start, out_chunks)

    if os.path.exists(temp):
        try:
            os.remove(temp)
        except OSError:
            pass  # transient Windows lock (AV scan); next run overwrites it anyway

    if template_header is None:
        raise RuntimeError("no entry produced any frames; nothing written")

    logger.info(
        f"wrote {output} ({len(out_chunks)} movi chunks, "
        f"{sum(1 for c in out_chunks if c['stream']=='v')} video frames)"
    )

    fixed = ffmpeg.fixup(output) if script.fixup else None
    return output, fixed
