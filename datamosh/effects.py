"""The mosh itself: byte-level frame duplication, deletion, reordering, and
corruption over a parsed chunk list. No decoding happens here.

Two layers live here:

* Deterministic primitives (databend_blob, transplant_pframes, dup_frame,
  delete_keyframe, reorder_pframes, drop_frames, interleave) -- each takes
  explicit parameters, and where randomness is inherent (which bytes to XOR,
  shuffle order) an `rng` argument (a random.Random) controls it. These are
  the building blocks for script.py's explicit ops.
* Config-driven rolls (mosh_video, mangle_audio, _video_reorder) -- every
  probability and range comes from a MoshConfig and decisions are rolled from
  `rng`, which defaults to the global random module so run_mosh's single-seed
  contract keeps working unchanged.

mosh_segment() is the heart -- it takes one clip's chunks plus a MoshConfig and
returns the mangled, re-interleaved chunk list.
"""

import random
import struct

from .scenes import DEFAULT_AUDIO_VIDEO_RATIO


# ---------------------------------------------------------------------------
# deterministic primitives
# ---------------------------------------------------------------------------


def databend_blob(blob, n_bytes, skip=8, rng=random):
    """XOR n_bytes random bytes inside a chunk's payload, skipping the first `skip`
    payload bytes (syncword/CRC/bsi for AC3, VOP start code for video) so the frame
    keeps sync and mangles instead of fully muting.
    """
    ba = bytearray(blob)
    size = struct.unpack_from("<I", ba, 4)[0]
    start, end = 8 + skip, 8 + size
    if end - start <= 0:
        return blob
    for _ in range(n_bytes):
        ba[rng.randrange(start, end)] ^= rng.randint(1, 255)
    return bytes(ba)


_databend_blob = databend_blob  # back-compat alias


def transplant_pframes(video, donor_payloads):
    """Overwrite every non-key video chunk's payload by cycling `donor_payloads`
    (another clip's genuine P-frame bytes) over this clip's keyframe canvas.
    `video` is a chunk list ({"data", "stream", "key"}) or a {v, key} frame list.
    """
    if not donor_payloads:
        return
    di = 0
    for c in video:
        if c["key"]:
            continue
        if "v" in c:
            c["v"] = dict(c["v"])
            c["v"]["data"] = donor_payloads[di % len(donor_payloads)]
        else:
            c["data"] = donor_payloads[di % len(donor_payloads)]
        di += 1


def dup_frame(frames, index, count):
    """Insert `count` extra copies of frames[index] immediately after it, in place.
    Copies are marked key=False so a duplicated keyframe smears instead of resetting.
    """
    f = frames[index]
    if index < 0:
        index += len(frames)
    frames[index + 1 : index + 1] = [
        {"v": dict(f["v"]), "key": False} for _ in range(count)
    ]


def delete_keyframe(frames, which=0):
    """Remove the nth keyframe from a {v, key} frame list in place.
    Returns True if one was found and deleted."""
    seen = 0
    for k, f in enumerate(frames):
        if f["key"]:
            if seen == which:
                del frames[k]
                return True
            seen += 1
    return False


def reorder_pframes(frames, pattern, rng=random):
    """Reorder the non-key frames of a {v, key} frame list in place; keyframes stay
    anchored at the front. Patterns: "reverse", "shuffle" (order from rng), "bounce"
    (append a reversed copy, ping-pong)."""
    keyframes = [f for f in frames if f["key"]]
    pframes = [f for f in frames if not f["key"]]
    if len(pframes) < 2:
        return
    if pattern == "reverse":
        pframes.reverse()
    elif pattern == "shuffle":
        rng.shuffle(pframes)
    elif pattern == "bounce":
        pframes = pframes + [{"v": dict(f["v"]), "key": False} for f in reversed(pframes)]
    else:
        raise ValueError(f"unknown reorder pattern {pattern!r} (reverse, shuffle, bounce)")
    frames[:] = keyframes + pframes


def drop_frames(frames, indices):
    """Delete the frames at `indices` (positions in the current list) in place."""
    n = len(frames)
    norm = set()
    for i in indices:
        j = i + n if i < 0 else i
        if not 0 <= j < n:
            raise ValueError(f"frame index {i} out of range (section has {n} frames)")
        norm.add(j)
    frames[:] = [f for i, f in enumerate(frames) if i not in norm]


def interleave(vout, aout):
    """Interleave audio chunks evenly across video frames (any leftover audio
    trails at the end) and return the combined chunk list."""
    out = []
    na, nv, ai = len(aout), len(vout), 0
    for i, vc in enumerate(vout):
        out.append(vc)
        upto = round((i + 1) * na / nv) if nv else na
        while ai < upto:
            out.append(aout[ai])
            ai += 1
    while ai < na:
        out.append(aout[ai])
        ai += 1
    return out


# ---------------------------------------------------------------------------
# config-driven rolls
# ---------------------------------------------------------------------------


def scramble_audio(aud, start, length, rng=random):
    """Shuffle a window of audio chunks in place (glitchy skipping)."""
    window = aud[start : start + length]
    rng.shuffle(window)
    aud[start : start + length] = window


def mangle_audio(cfg, aud, rng=random):
    """Apply the optional audio mangles to a clip's audio chunk list in place.

    reverse: play the clip's audio backwards.  scramble: shuffle a random window of
    chunks (glitchy skipping).  databend: byte-corrupt a fraction of chunks (digital
    noise). Each is an independent per-clip roll against its config probability.
    """
    if rng.random() < cfg.audio_reverse_prob:
        aud.reverse()
    if rng.random() < cfg.audio_scramble_prob and len(aud) > 2:
        win_len = min(rng.randint(*cfg.audio_scramble_window), len(aud))
        start = rng.randrange(0, len(aud) - win_len + 1)
        scramble_audio(aud, start, win_len, rng=rng)
    if rng.random() < cfg.audio_databend_prob:
        for c in aud:
            if rng.random() < cfg.audio_databend_frac:
                c["data"] = databend_blob(
                    c["data"], rng.randint(*cfg.audio_databend_bytes), rng=rng
                )


def stretch_audio(audio, target, grain_range, rng=random):
    """Granular micro-loop stretch to exactly `target` chunks.

    Walks the source IN ORDER in small grains (grain_range chunks, ~32ms each),
    giving each grain a proportional share of the output: when stretching, each
    grain loops ~(target/len(audio)) times; when compressing, grains are evenly
    thinned. No random jumps -- the clip plays through start to end, slowed.
    """
    if not audio or target <= 0:
        return []
    out = []
    n_chunks = len(audio)
    pos = 0  # source chunks consumed
    while pos < n_chunks:
        grain_len = min(rng.randint(*grain_range), n_chunks - pos)
        grain = audio[pos : pos + grain_len]
        pos += grain_len
        end = target if pos >= n_chunks else round(pos * target / n_chunks)
        while len(out) < end:
            for c in grain:
                if len(out) >= end:
                    break
                out.append(dict(c))
    return out


def _video_reorder(cfg, frames, rng=random):
    """Roll the P-frame reorder/delete mangles in place.

    Keyframe(s) are anchored at the front; only the non-key P-frames are shuffled /
    reversed / bounced / skipped. Each `frame` is a video-only {v, key} pair.
    """
    keyframes = [f for f in frames if f["key"]]
    pframes = [f for f in frames if not f["key"]]
    if len(pframes) < 2:
        return
    if rng.random() < cfg.video_shuffle_prob:
        rng.shuffle(pframes)
    if rng.random() < cfg.video_reverse_prob:
        pframes.reverse()
    if rng.random() < cfg.video_bounce_prob:
        pframes = pframes + [{"v": dict(f["v"]), "key": False} for f in reversed(pframes)]
    if rng.random() < cfg.video_skip_prob:
        drop = {i for i in range(len(pframes)) if rng.random() < cfg.video_skip_frac}
        pframes = [f for i, f in enumerate(pframes) if i not in drop] or pframes[:1]
    frames[:] = keyframes + pframes


def mosh_video(
    cfg,
    frames,
    *,
    keep_keyframe=False,
    donor_pframes=None,
    intensity=1.0,
    rng=random,
):
    """Roll the classic video mangles over a {v, key} frame list and return the
    (possibly re-built) frame list: motion transplant, P-frame duplication,
    keyframe deletion, reorder/skip, and databend -- every decision rolled from
    `rng` against the config's probabilities and ranges.
    """
    # --- motion transplant: donor P-frame payloads onto this clip's keyframe canvas ---
    if donor_pframes and rng.random() < cfg.video_transplant_prob:
        transplant_pframes(frames, donor_pframes)

    # --- duplicate random P-frames (intensity scales the copy count) ---
    p_indices = [j for j, f in enumerate(frames) if not f["key"]]
    if p_indices:
        k = min(rng.randint(*cfg.dup_frames_range), len(p_indices))
        hi = max(2, int(round(cfg.dup_count_range[1] * intensity)))
        lo = min(cfg.dup_count_range[0], hi)
        copies = {j: rng.randint(lo, hi) for j in rng.sample(p_indices, k)}
        dup = []
        for j, f in enumerate(frames):
            dup.append(f)
            for _ in range(copies.get(j, 0)):
                dup.append({"v": dict(f["v"]), "key": False})
        frames = dup

    # --- maybe delete the leading keyframe ---
    if not keep_keyframe and rng.random() < cfg.keyframe_delete_prob:
        delete_keyframe(frames)

    # --- P-frame reorder/skip ---
    _video_reorder(cfg, frames, rng=rng)

    # --- video databend (corrupt macroblocks; skip past the VOP start code) ---
    if rng.random() < cfg.video_databend_prob:
        for f in frames:
            if not f["key"] and rng.random() < cfg.video_databend_frac:
                f["v"] = dict(f["v"])
                f["v"]["data"] = databend_blob(
                    f["v"]["data"],
                    rng.randint(*cfg.video_databend_bytes),
                    skip=16,
                    rng=rng,
                )

    return frames


def mosh_segment(
    cfg,
    segment,
    keep_keyframe=False,
    donor_pframes=None,
    intensity=1.0,
    av_ratio=DEFAULT_AUDIO_VIDEO_RATIO,
    rng=random,
):
    """Mosh one clip and return (interleaved_chunks, this_clip_pframes).

    Video: optional motion transplant, P-frame duplication, keyframe deletion, and the
    reorder/skip/databend rolls. Audio is granular-stretched (small in-order grains,
    each looped in proportion to the stretch ratio) to match the final video duration,
    then gets its own mangles, and is interleaved 1:1. `this_clip_pframes` (the clip's genuine P-frame payloads,
    captured before any transplant) seeds the next clip's transplant donor. `av_ratio`
    is the source's audio-chunks-per-video-frame ratio (scenes.audio_video_ratio).
    Every decision is rolled from `rng` (default: the global random module, so
    run_mosh's single-seed contract holds).
    """
    video = [c for c in segment if c["stream"] == "v"]
    audio = [c for c in segment if c["stream"] == "a"]

    # capture genuine motion for the NEXT clip's transplant donor (before we overwrite it)
    this_pframes = [c["data"] for c in video if not c["key"]]

    # --- video frames as {v, key} pairs ---
    frames = [{"v": c, "key": c["key"]} for c in video]
    frames = mosh_video(
        cfg,
        frames,
        keep_keyframe=keep_keyframe,
        donor_pframes=donor_pframes,
        intensity=intensity,
        rng=rng,
    )
    vout = [f["v"] for f in frames]

    # --- granular-stretch audio to match the video DURATION, then roll the audio mangles ---
    # An AC3 frame (~32ms) is shorter than a video frame (~33.4ms), so matching audio chunk
    # count to video frame count 1:1 makes the audio run ~4% fast and the drift accumulates
    # across the output. Scale the target by the duration ratio (av_ratio) so the stretched
    # audio spans the same wall-clock time as the stretched video.
    aout = []
    if audio:
        target = max(1, round(len(vout) * av_ratio))
        aout = stretch_audio(audio, target, cfg.audio_grain_range, rng=rng)
        if aout:
            mangle_audio(cfg, aout, rng=rng)

    # --- interleave, spreading the audio chunks evenly across the video frames ---
    return interleave(vout, aout), this_pframes
