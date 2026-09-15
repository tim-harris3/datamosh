"""Shared command-line plumbing: one flag per MoshConfig field, generated from the
dataclass, so every script's CLI stays in sync with the config automatically.

    ap = argparse.ArgumentParser(...)
    cli.add_config_args(ap)
    args = ap.parse_args()
    cfg = cli.config_from_args(args)   # preset (if any) + explicit flags over defaults

main() is the installed `datamosh` command (and what mosh.py at the project root
runs). It dispatches on the first token -- `datamosh prepare` pre-encodes a
moshable AVI, `datamosh inspect` lists its keyframe sections, `datamosh export`
transcodes a mosh into a shareable mp4/webm/gif, `datamosh mv-dump` summarizes
per-frame motion-vector fields -- and anything
else falls through to the original mosh form (every MoshConfig setting as a
flag, --preset to start from a builtin preset), so `datamosh --source ...` keeps
working unchanged.
"""

import argparse
import json
import random
import sys
from dataclasses import fields, replace
from pathlib import Path
from subprocess import CalledProcessError

from . import ffmpeg, paths, presets
from .config import MoshConfig, float_fields, range_fields, tunable_fields
from .ffmpeg import ENCODERS

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
        help="start from a named preset (see datamosh/presets.json); flags override it",
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
    # structural fields don't auto-generate flags (only tunables do), so this one
    # is by hand -- config_from_args reads every structural field off the args
    ap.add_argument(
        "--encoder",
        choices=ENCODERS,
        default=None,
        help=f"moshable video encoder (default {d.encoder}); "
        "xvid needs a full ffmpeg build with libxvid",
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
        "`datamosh inspect` lists its keyframe sections, `datamosh export` "
        "transcodes a mosh into a shareable mp4/webm/gif, `datamosh mv-dump` "
        "summarizes per-frame motion-vector fields (each has its own --help)",
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
    ap.add_argument(
        "--encoder",
        choices=ENCODERS,
        default="mpeg4",
        help="moshable video encoder (default mpeg4); "
        "xvid needs a full ffmpeg build with libxvid",
    )
    args = ap.parse_args(argv)
    ffmpeg.require_encoder(args.encoder)  # actionable error before the encode
    if args.seed is not None:
        random.seed(args.seed)
    out = args.output
    if out is None:
        paths.ensure_output_dirs()
        out = str(paths.OUTPUT_DIR / f"{Path(args.src).stem}_moshable.avi")
    make_moshable(
        args.src,
        out,
        gap_range=tuple(args.gap),
        duration=args.duration,
        encoder=args.encoder,
    )


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


def _cmd_export(argv):
    """`datamosh export`: transcode a moshed AVI into a shareable mp4/webm/gif.
    The corrupt bytes are decoded directly -- the export is a faithful recording
    of how ffmpeg plays the glitch."""
    from .export import EXPORT_FORMATS, export

    ap = argparse.ArgumentParser(
        prog="datamosh export",
        description="Transcode a (moshed) video into a shareable mp4, webm, or gif. "
        "Trims are applied output-side, so moshed AVIs' lying indexes are never "
        "fast-seeked and the glitch smear survives the cut.",
    )
    ap.add_argument("src", help="video to export (typically a moshed AVI)")
    ap.add_argument(
        "dst",
        nargs="?",
        default=None,
        help="output path (default: next to SRC with the format's extension)",
    )
    ap.add_argument(
        "--to",
        dest="fmt",
        choices=EXPORT_FORMATS,
        default=None,
        help="output format (default: inferred from DST's extension, else mp4)",
    )
    ap.add_argument(
        "--fps",
        type=float,
        default=None,
        metavar="F",
        help="output frame rate (gif defaults to 15; mp4/webm keep the source rate)",
    )
    ap.add_argument(
        "--width",
        type=int,
        default=None,
        metavar="N",
        help="output width in pixels, height follows (gif defaults to 480)",
    )
    ap.add_argument(
        "--start",
        default=None,
        metavar="T",
        help="start time -- seconds or ffmpeg time syntax like 0:12.5",
    )
    ap.add_argument(
        "--duration",
        default=None,
        metavar="T",
        help="length to keep -- seconds or ffmpeg time syntax",
    )
    ap.add_argument(
        "--loop",
        type=int,
        default=0,
        metavar="N",
        help="gif loop count: 0 = forever, -1 = play once (default 0)",
    )
    ap.add_argument(
        "--crf",
        type=int,
        default=None,
        metavar="N",
        help="quality override, lower = better (default: mp4 18, webm 32)",
    )
    args = ap.parse_args(argv)
    export(
        args.src,
        args.dst,
        args.fmt,
        fps=args.fps,
        width=args.width,
        start=args.start,
        duration=args.duration,
        loop=args.loop,
        crf=args.crf,
    )


def _cmd_mv_dump(argv):
    """`datamosh mv-dump`: per-frame motion-vector summary -- the roadmap's
    analysis toolkit as a CLI. A table row per frame (moving macroblocks,
    mean |v| in half-pels, dominant direction), the raw fields as JSON for
    scripting, and the codecview arrow overlay for eyeballing."""
    from . import mv

    ap = argparse.ArgumentParser(
        prog="datamosh mv-dump",
        description="Summarize a video's per-frame motion-vector fields (16x16 "
        "macroblock grid, half-pel units). Keyframes and static blocks read as "
        "zero motion; directions are screen-space compass points (+y down = S).",
    )
    ap.add_argument("file", help="video to analyze (typically a moshable AVI)")
    ap.add_argument(
        "--json",
        default=None,
        metavar="PATH",
        help="also dump the raw fields as JSON (nested lists, half-pel units)",
    )
    ap.add_argument(
        "--frame",
        type=int,
        default=None,
        metavar="N",
        help="limit the table (and --json) to this frame index",
    )
    ap.add_argument(
        "--overlay",
        default=None,
        metavar="PATH",
        help="also render the codecview motion-vector arrow overlay to this AVI",
    )
    ap.add_argument(
        "--backend",
        choices=("auto", "probe", "estimate"),
        default="auto",
        help="probe = the decoder's exported vectors (exact; most ffmpeg builds "
        "can't serialize them), estimate = phase correlation on decoded frames "
        "(works everywhere), auto = probe when supported, else estimate",
    )
    args = ap.parse_args(argv)
    fields = mv.extract_mv_fields(args.file, backend=args.backend)
    indices = range(len(fields))
    if args.frame is not None:
        if not 0 <= args.frame < len(fields):
            raise ValueError(
                f"--frame {args.frame} out of range: {args.file} has {len(fields)} frames"
            )
        indices = [args.frame]
    mb_h, mb_w = fields[0].shape[:2] if fields else (0, 0)
    print(f"{len(fields)} frames, {mb_w}x{mb_h} macroblocks (half-pel units)")
    print(f"{'frame':>5} {'moving':>6} {'mean|v|':>8} {'dir':>4}")
    for i in indices:
        s = mv.field_stats(fields[i])
        print(f"{i:>5} {s['moving']:>6} {s['mean_mag']:>8.2f} {s['direction']:>4}")
    if args.json:
        payload = {
            "file": args.file,
            "unit": "half-pel",
            "mb_size": mv.MB_SIZE,
            "shape": [mb_h, mb_w],
            "fields": [{"index": i, "field": fields[i].tolist()} for i in indices],
        }
        with open(args.json, "w") as fh:
            json.dump(payload, fh)
        print(f"wrote {args.json}")
    if args.overlay:
        mv.mv_overlay(args.file, args.overlay)


VERBS = {
    "mosh": _cmd_mosh,
    "prepare": _cmd_prepare,
    "inspect": _cmd_inspect,
    "export": _cmd_export,
    "mv-dump": _cmd_mv_dump,
}


def main(argv=None):
    """The `datamosh` command: dispatch on the first token (mosh / prepare /
    inspect / export / mv-dump); anything else is the flat mosh form, unchanged."""
    from .log import enable_console_logging

    enable_console_logging()
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in VERBS:
        verb, argv = VERBS[argv[0]], argv[1:]
    else:
        verb = _cmd_mosh
    try:
        verb(argv)
    except (RuntimeError, KeyError, ValueError, CalledProcessError) as e:
        sys.exit(str(e))
