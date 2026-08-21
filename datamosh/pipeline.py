"""run_mosh(): the shared render loop behind the CLI, the Gradio UI, and recipes.

For each of cfg.n iterations it picks a shot from the source's scene map (biased
toward short shots), re-extracts it as a clean moshable AVI, moshes it with
mosh_segment(), and appends the result to a growing output AVI. See MoshConfig for
every knob it reads.
"""

import logging
import os
import random
import subprocess

from . import ffmpeg, paths
from .avi import parse_avi, write_avi
from .config import MoshConfig, escalation_intensity
from .effects import mosh_segment
from .scenes import audio_video_ratio, build_scene_map, extract_shot

logger = logging.getLogger(__name__)


def _normalize_sequence(sequence, source):
    """Resolve a user timeline into [(source, t0, t1), ...]: 2-tuples get the default
    source, every path is resolved and checked."""
    if not sequence:
        raise RuntimeError("empty sequence")
    if source is None and any(len(e) != 3 for e in sequence):
        raise RuntimeError(
            "sequence has (t0, t1) entries but no default source: set cfg.source "
            "or use (source, t0, t1) entries"
        )
    sequence = [e if len(e) == 3 else (source, e[0], e[1]) for e in sequence]
    sequence = [(paths.resolve(s), t0, t1) for s, t0, t1 in sequence]
    for s in {s for s, _, _ in sequence}:
        if not os.path.exists(s):
            raise RuntimeError(f"sequence source not found: {s}")
    n_sources = len({s for s, _, _ in sequence})
    logger.info(
        f"sequence mode: {len(sequence)} sections from {n_sources} source(s) (user timeline)"
    )
    return sequence


def _weighted_shot_map(cfg, source, shots, progress):
    """Resolve the shot map for random-pick mode; returns (shots, pick_weights).

    Runs (cached) scene detection when no shot map is given; weights bias the pick
    toward short shots via cfg.short_gop_bias and zero out shots under cfg.min_shot
    (weights of None means uniform)."""
    if shots is None:
        if progress:
            progress(0.0, "scene detection (one-time, cached)")
        shots, duration = build_scene_map(source, cfg.scene_threshold)
    elif shots:
        duration = max(t1 for _, t1 in shots)
    if not shots:
        raise RuntimeError("no shots detected in source")
    durations = [b - a for a, b in shots]
    weights = [
        1.0 / d**cfg.short_gop_bias if d >= cfg.min_shot else 0.0 for d in durations
    ]
    if sum(weights) == 0:
        weights = None
    eligible = sum(1 for d in durations if d >= cfg.min_shot)
    logger.info(
        f"source {os.path.basename(source)}: {len(shots)} shots over "
        f"{duration/60:.1f} min ({eligible} >= {cfg.min_shot}s, shortest {min(durations):.2f}s)"
    )
    return shots, weights


def run_mosh(cfg=None, *, shots=None, sequence=None, progress=None):
    """Render a moshed AVI and return (output_path, fixed_path_or_None).

    cfg      -- a MoshConfig; None uses all defaults.
    shots    -- optional [(t0, t1), ...] list used as the shot map instead of running
                scene detection on the source (see sections.keyframe_shots).
    sequence -- optional ordered [(t0, t1), ...] or [(source, t0, t1), ...]; when
                given, clips render in exactly this order (duplicates allowed) and
                cfg.n, shot weights, and scene detection are all bypassed. A 3-tuple
                overrides the clip's source file, so one timeline can splice sections
                from several videos; 2-tuples use cfg.source. cfg.seed still makes
                the effect randomness reproducible.
    progress -- optional progress(frac, msg) callback for a UI progress bar.
    """
    cfg = cfg if cfg is not None else MoshConfig()
    if cfg.source is None and sequence is None:
        raise RuntimeError(
            "no source video given: pass --source on the CLI, set cfg.source in a "
            "recipe, or generate a demo clip with `python -m datamosh.sample`"
        )
    ffmpeg.require_ffmpeg()
    ffmpeg.require_encoder(cfg.encoder)  # fail fast, not on clip 1's encode
    if cfg.seed is not None:
        random.seed(cfg.seed)

    source = paths.resolve(cfg.source) if cfg.source is not None else None
    output = paths.resolve(cfg.output)
    if source is not None and not os.path.exists(source):
        raise RuntimeError(f"source not found: {source}")
    # av_ratio per source, probed lazily from each source's first extracted moshable AVI
    # (not the original file): extract_shot re-encodes audio to AC3, which can silently
    # resample (e.g. a 16 kHz source becomes 32 kHz AC3), changing the chunks-per-frame math.
    av_ratios = {}
    if sequence is not None:
        sequence = _normalize_sequence(sequence, source)
        total, weights = len(sequence), None
    else:
        total = cfg.n
        shots, weights = _weighted_shot_map(cfg, source, shots, progress)

    if cfg.reset and os.path.exists(output):
        os.remove(output)

    template_header = template_movi_start = None
    if os.path.exists(output):
        template_header, template_movi_start, out_chunks = parse_avi(output)
    else:
        out_chunks = []

    temp = os.path.join(os.path.dirname(output) or ".", ".mosh_tmp.avi")
    prev_pframes = None  # previous clip's genuine P-frames, for the motion transplant

    for i in range(total):
        if sequence is not None:
            clip_src, t0, t1 = sequence[i]
        else:
            clip_src = source
            t0, t1 = random.choices(shots, weights=weights, k=1)[0]
        dur = t1 - t0
        try:
            extract_shot(clip_src, t0, dur, temp, encoder=cfg.encoder)
            clip_header, clip_movi_start, seg = parse_avi(temp)
        except (subprocess.CalledProcessError, ValueError) as e:
            logger.warning(f"[{i}] extract {t0:.1f}s failed ({e}); skipped")
            continue
        if template_header is None:
            template_header, template_movi_start = clip_header, clip_movi_start
        if not any(c["stream"] == "v" for c in seg):
            logger.warning(f"[{i}] {t0:.1f}s +{dur:.2f}s: no video frames; skipped")
            continue

        keep = len(out_chunks) == 0  # first-ever frames need a valid start
        if clip_src not in av_ratios:
            av_ratios[clip_src] = audio_video_ratio(temp)
        moshed, prev_pframes = mosh_segment(
            cfg,
            seg,
            keep_keyframe=keep,
            donor_pframes=prev_pframes,
            intensity=escalation_intensity(cfg.escalate, i, total),
            av_ratio=av_ratios[clip_src],
        )

        v_in = sum(1 for c in seg if c["stream"] == "v")
        v_out = sum(1 for c in moshed if c["stream"] == "v")
        key_deleted = sum(1 for c in seg if c["stream"] == "v" and c["key"]) > sum(
            1 for c in moshed if c["stream"] == "v" and c["key"]
        )
        logger.info(
            f"[{i}] {t0/60:.1f}min +{dur:.2f}s: {v_in}->{v_out} vframes, "
            f"keyframe {'deleted' if key_deleted else 'kept'}"
        )
        if progress:
            progress((i + 1) / total, f"clip {i + 1}/{total}")

        out_chunks.extend(moshed)
        write_avi(output, template_header, template_movi_start, out_chunks)

    if os.path.exists(temp):
        try:
            os.remove(temp)
        except OSError:
            pass  # transient Windows lock (AV scan); next run overwrites it anyway

    logger.info(
        f"wrote {output} ({len(out_chunks)} movi chunks, "
        f"{sum(1 for c in out_chunks if c['stream']=='v')} video frames)"
    )

    fixed = ffmpeg.fixup(output) if cfg.fixup else None
    return output, fixed
