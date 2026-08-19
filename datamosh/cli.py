"""Shared command-line plumbing: one flag per MoshConfig field, generated from the
dataclass, so every script's CLI stays in sync with the config automatically.

    ap = argparse.ArgumentParser(...)
    cli.add_config_args(ap)
    args = ap.parse_args()
    cfg = cli.config_from_args(args)   # preset (if any) + explicit flags over defaults

main() is the installed `datamosh` command (and what mosh.py at the project root
runs): every MoshConfig setting as a flag, --preset to start from presets.json.
"""

import argparse
import sys
from dataclasses import fields, replace

from . import presets
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


def main():
    """The `datamosh` command: build a MoshConfig from flags and run the mosh."""
    from .pipeline import run_mosh

    ap = argparse.ArgumentParser(
        prog="datamosh",
        description="Shot-based datamosher (re-extracts shots from the source video).",
    )
    add_config_args(ap)
    args = ap.parse_args()
    try:
        cfg = config_from_args(args)
        run_mosh(cfg)
    except (RuntimeError, KeyError) as e:
        sys.exit(str(e))
