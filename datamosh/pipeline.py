"""run_mosh(): the shared render loop behind the CLI, the Gradio UI, and recipes.

For each of cfg.n iterations it picks a shot from the source's scene map (biased
toward short shots), re-extracts it as a clean moshable AVI, moshes it with
mosh_segment(), and appends the result to a growing output AVI. See MoshConfig for
every knob it reads.
"""

import os
import random
import subprocess

from . import ffmpeg, paths
from .avi import parse_avi, write_avi
from .config import MoshConfig
from .effects import mosh_segment
from .scenes import audio_video_ratio, build_scene_map, extract_shot


def run_mosh(cfg=None, *, shots=None, sequence=None, progress=None):
    """Render a moshed AVI and return (output_path, fixed_path_or_None).

    cfg      -- a MoshConfig; None uses all defaults.
    shots    -- optional [(t0, t1), ...] list used as the shot map instead of running
                scene detection on the source (see sections.keyframe_shots).
    sequence -- optional ordered [(t0, t1), ...]; when given, clips render in exactly
                this order (duplicates allowed) and cfg.n, shot weights, and scene
                detection are all bypassed. cfg.seed still makes the effect
                randomness reproducible.
    progress -- optional progress(frac, msg) callback for a UI progress bar.
    """
    cfg = cfg if cfg is not None else MoshConfig()
    ffmpeg.require_ffmpeg()
    if cfg.seed is not None:
        random.seed(cfg.seed)

    source = paths.resolve(cfg.source)
    output = paths.resolve(cfg.output)
    if not os.path.exists(source):
        raise RuntimeError(f"source not found: {source}")
    av_ratio = audio_video_ratio(source)
    if sequence is not None:
        if not sequence:
            raise RuntimeError("empty sequence")
        total, weights = len(sequence), None
        print(f"source {os.path.basename(source)}: sequence mode, "
              f"{total} sections (user timeline)")
    else:
        total = cfg.n
        if shots is None:
            if progress:
                progress(0.0, "scene detection (one-time, cached)")
            shots, duration = build_scene_map(source, cfg.scene_threshold)
        elif shots:
            duration = max(t1 for _, t1 in shots)
        if not shots:
            raise RuntimeError("no shots detected in source")
        durs = [b - a for a, b in shots]
        weights = [1.0 / d**cfg.short_gop_bias if d >= cfg.min_shot else 0.0 for d in durs]
        if sum(weights) == 0:
            weights = None
        eligible = sum(1 for d in durs if d >= cfg.min_shot)
        print(
            f"source {os.path.basename(source)}: {len(shots)} shots over "
            f"{duration/60:.1f} min ({eligible} >= {cfg.min_shot}s, shortest {min(durs):.2f}s)"
        )

    if cfg.reset and os.path.exists(output):
        os.remove(output)

    template_header = template_movi_start = None
    if os.path.exists(output):
        template_header, template_movi_start, out_chunks = parse_avi(output)
    else:
        out_chunks = []

    temp = os.path.join(os.path.dirname(output) or ".", ".mosh_tmp.avi")
    prev_pframes = None  # previous clip's genuine P-frames, for the motion transplant

    for it in range(total):
        if sequence is not None:
            t0, t1 = sequence[it]
        else:
            t0, t1 = random.choices(shots, weights=weights, k=1)[0]
        dur = t1 - t0
        try:
            extract_shot(source, t0, dur, temp)
            _, _, seg = parse_avi(temp)
        except (subprocess.CalledProcessError, ValueError) as e:
            print(f"[{it}] extract {t0:.1f}s failed ({e}); skipped")
            continue
        if template_header is None:
            template_header, template_movi_start, _seg2 = parse_avi(temp)
        if not any(c["stream"] == "v" for c in seg):
            print(f"[{it}] {t0:.1f}s +{dur:.2f}s: no video frames; skipped")
            continue

        keep = len(out_chunks) == 0  # first-ever frames need a valid start
        intensity = 1 + cfg.escalate * (it / max(1, total - 1)) if cfg.escalate else 1.0
        moshed, prev_pframes = mosh_segment(
            cfg, seg, keep_keyframe=keep, donor_pframes=prev_pframes,
            intensity=intensity, av_ratio=av_ratio,
        )

        vin = sum(1 for c in seg if c["stream"] == "v")
        vout = sum(1 for c in moshed if c["stream"] == "v")
        kdel = sum(1 for c in seg if c["stream"] == "v" and c["key"]) > sum(
            1 for c in moshed if c["stream"] == "v" and c["key"]
        )
        print(
            f"[{it}] {t0/60:.1f}min +{dur:.2f}s: {vin}->{vout} vframes, "
            f"keyframe {'deleted' if kdel else 'kept'}"
        )
        if progress:
            progress((it + 1) / total, f"clip {it + 1}/{total}")

        out_chunks.extend(moshed)
        write_avi(output, template_header, template_movi_start, out_chunks)

    if os.path.exists(temp):
        try:
            os.remove(temp)
        except OSError:
            pass  # transient Windows lock (AV scan); next run overwrites it anyway

    print(
        f"wrote {output} ({len(out_chunks)} movi chunks, "
        f"{sum(1 for c in out_chunks if c['stream']=='v')} video frames)"
    )

    fixed = ffmpeg.fixup(output) if cfg.fixup else None
    return output, fixed
