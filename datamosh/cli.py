"""Shared command-line plumbing: one flag per MoshConfig field, generated from the
dataclass, so every script's CLI stays in sync with the config automatically.

    ap = argparse.ArgumentParser(...)
    cli.add_config_args(ap)
    args = ap.parse_args()
    cfg = cli.config_from_args(args)   # preset (if any) + explicit flags over defaults

main() is the installed `datamosh` command (and what mosh.py at the project root
runs). It dispatches on the first token -- `datamosh prepare` pre-encodes a
moshable AVI, `datamosh inspect` lists its keyframe sections -- and anything
else falls through to the original mosh form (every MoshConfig setting as a
flag, --preset to start from presets.json), so `datamosh --source ...` keeps
working unchanged.
"""

import argparse
import json
import random
import sys
from dataclasses import fields, replace
from pathlib import Path

from . import paths, presets
from .config import MoshConfig, float_fields, range_fields, tunable_fields

# structural fields whose flag passes the value straight through to the config
# (reset/fixup are excluded: they're exposed as the inverted --append/--no-fixup)
_PASSTHROUGH = tuple(
    f.name
    for f in fields(MoshConfig)
    if not f.metadata.get("tunable") and f.name not in ("reset", "fixup")
)


def _flag(name):
    return "--" + name.replace("_", "-")


def add_config_args(ap):
    """Add --preset, the structural flags, and an auto-generated flag per tunable."""
    d = MoshConfig()
    ap.add_argument(
        "--preset",
        default=None,
        metavar="NAME",
        help="start from a named preset (see presets.json); flags override it",
    )
    ap.add_argument(
        "--source",
        default=None,
        help="source video (required unless a preset or recipe sets one; "
        "`python -m datamosh.sample` generates a demo clip)",
    )
    ap.add_argument(
        "--output", default=None, help=f"output AVI path (default {d.output})"
    )
    ap.add_argument(
        "--n",
        type=int,
        default=None,
        help=f"segments to append this run (default {d.n})",
    )
    ap.add_argument(
        "--seed", type=int, default=None, help="RNG seed (same seed = same result)"
    )
    ap.add_argument(
        "--min-shot",
        type=float,
        default=None,
        help=f"min shot length in seconds (default {d.min_shot})",
    )
    ap.add_argument(
        "--scene-threshold",
        type=float,
        default=None,
        help=f"scene-detection sensitivity, lower = more cuts (default {d.scene_threshold})",
    )
    ap.add_argument(
        "--append",
        action="store_true",
        help="append to the existing output instead of starting fresh",
    )
    ap.add_argument(
        "--no-fixup", action="store_true", help="skip the ffmpeg -c copy pass"
    )

    tune = ap.add_argument_group(
        'tunables (see `python -c "import datamosh; datamosh.describe()"`)'
    )
    for f in float_fields():
        tune.add_argument(
            _flag(f.name),
            type=float,
            default=None,
            metavar="F",
            help=f"{f.metadata['help']} (default {f.default})",
        )
    for f in range_fields():
        lo, hi = f.default
        tune.add_argument(
            _flag(f.name),
            type=int,
            nargs=2,
            default=None,
            metavar=("LO", "HI"),
            help=f"{f.metadata['help']} (default {lo} {hi})",
        )


def config_from_args(args):
    """Build the MoshConfig: preset (if given) over defaults, explicit flags over both."""
    cfg = presets.preset_config(args.preset) if args.preset else MoshConfig()

    updates = {}
    for name in _PASSTHROUGH:
        val = getattr(args, name)
        if val is not None:
            updates[name] = val
    for f in tunable_fields():
        val = getattr(args, f.name)
        if val is not None:
            updates[f.name] = tuple(val) if f.metadata["kind"] == "range" else val
    if args.append:
        updates["reset"] = False
    if args.no_fixup:
        updates["fixup"] = False
    return replace(cfg, **updates)


def _cmd_mosh(argv):
    """The original mosh form: build a MoshConfig from flags and run the mosh."""
    from .pipeline import run_mosh

    ap = argparse.ArgumentParser(
        prog="datamosh",
        description="Shot-based datamosher (re-extracts shots from the source video).",
        epilog="other verbs: `datamosh prepare` pre-encodes a moshable AVI, "
        "`datamosh inspect` lists its keyframe sections (each has its own --help)",
    )
    add_config_args(ap)
    args = ap.parse_args(argv)
    cfg = config_from_args(args)
    run_mosh(cfg)


def _cmd_prepare(argv):
    """`datamosh prepare`: pre-encode a source into a moshable AVI once, so scripts
    and repeat runs skip the slow re-encode and address sections directly."""
    from .sections import make_moshable

    ap = argparse.ArgumentParser(
        prog="datamosh prepare",
        description="Re-encode a video into a moshable AVI with keyframes at random "
        "timestamps. MoshScript entries then address its sections by (avi, index) -- "
        "`datamosh inspect` lists the indices.",
    )
    ap.add_argument("src", help="source video (any format ffmpeg reads)")
    ap.add_argument(
        "-o",
        "--output",
        default=None,
        help="output AVI path (default output/<name>_moshable.avi)",
    )
    ap.add_argument(
        "--gap",
        type=float,
        nargs=2,
        default=(0.2, 10.0),
        metavar=("LO", "HI"),
        help="random keyframe gap range in seconds (default 0.2 10.0)",
    )
    ap.add_argument(
        "--duration",
        type=float,
        default=None,
        metavar="S",
        help="encode only the first S seconds",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help="seed the keyframe layout (same seed = same keyframe layout)",
    )
    args = ap.parse_args(argv)
    if args.seed is not None:
        random.seed(args.seed)
    out = args.output
    if out is None:
        paths.ensure_output_dirs()
        out = str(paths.OUTPUT_DIR / f"{Path(args.src).stem}_moshable.avi")
    make_moshable(args.src, out, gap_range=tuple(args.gap), duration=args.duration)


def _cmd_inspect(argv):
    """`datamosh inspect`: list a moshable AVI's keyframe sections -- the indices
    MoshScript entries address -- straight from the parsed bytes."""
    from .sections import describe_sections

    ap = argparse.ArgumentParser(
        prog="datamosh inspect",
        description="List a moshable AVI's keyframe sections (byte-level parse, no "
        "decode). MoshScript entries address these sections by (avi, index).",
    )
    ap.add_argument("file", help="AVI to inspect (typically from `datamosh prepare`)")
    ap.add_argument(
        "--json",
        action="store_true",
        help="emit the same data as machine-readable JSON",
    )
    args = ap.parse_args(argv)
    info = describe_sections(args.file)
    if args.json:
        print(json.dumps(info, indent=2))
        return
    print(
        f"{info['width']}x{info['height']} {info['codec']} {info['fps']:.2f}fps, "
        f"{info['frames']} frames / {info['duration']:.1f}s, "
        f"{len(info['sections'])} sections"
    )
    print(f"{'idx':>4} {'start':>8} {'dur':>7} {'frames':>6} {'audio':>5} {'keys':>4}")
    flagged = False
    for s in info["sections"]:
        odd = s["keyframes"] != 1
        flagged = flagged or odd
        print(
            f"{s['index']:>4} {s['start']:>8.3f} {s['duration']:>7.3f} "
            f"{s['frames']:>6} {s['audio_chunks']:>5} "
            f"{s['keyframes']:>3}{'!' if odd else ' '}"
        )
    if flagged:
        print("! keyframe count != 1; MoshScript entries auto-demote extra keyframes")


VERBS = {"mosh": _cmd_mosh, "prepare": _cmd_prepare, "inspect": _cmd_inspect}


def main(argv=None):
    """The `datamosh` command: dispatch on the first token (mosh / prepare /
    inspect); anything else is the flat mosh form, unchanged."""
    from .log import enable_console_logging

    enable_console_logging()
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in VERBS:
        verb, argv = VERBS[argv[0]], argv[1:]
    else:
        verb = _cmd_mosh
    try:
        verb(argv)
    except (RuntimeError, KeyError, ValueError) as e:
        sys.exit(str(e))
