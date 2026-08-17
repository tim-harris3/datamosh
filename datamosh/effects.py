"""The mosh itself: byte-level frame duplication, deletion, reordering, and
corruption over a parsed chunk list. No decoding happens here.

mosh_segment() is the heart -- it takes one clip's chunks plus a MoshConfig and
returns the mangled, re-interleaved chunk list. Every probability and range it
rolls comes from the config, so two runs with the same seed and config are
byte-identical.
"""

import random
import struct

from .scenes import DEFAULT_AUDIO_VIDEO_RATIO


def _databend_blob(blob, n_bytes, skip=8):
    """XOR n_bytes random bytes inside an AC3 chunk's payload, skipping the first `skip`
    bytes (syncword/CRC/bsi) so the frame keeps sync and mangles instead of fully muting.
    """
    ba = bytearray(blob)
    size = struct.unpack_from("<I", ba, 4)[0]
    start, end = 8 + skip, 8 + size
    if end - start <= 0:
        return blob
    for _ in range(n_bytes):
        ba[random.randrange(start, end)] ^= random.randint(1, 255)
    return bytes(ba)


def mangle_audio(cfg, aud):
    """Apply the optional audio mangles to a clip's audio chunk list in place.

    reverse: play the clip's audio backwards.  scramble: shuffle a random window of
    chunks (glitchy skipping).  databend: byte-corrupt a fraction of chunks (digital
    noise). Each is an independent per-clip roll against its config probability.
    """
    if random.random() < cfg.audio_reverse_prob:
        aud.reverse()
    if random.random() < cfg.audio_scramble_prob and len(aud) > 2:
        w = min(random.randint(*cfg.audio_scramble_window), len(aud))
        i = random.randrange(0, len(aud) - w + 1)
        window = aud[i : i + w]
        random.shuffle(window)
        aud[i : i + w] = window
    if random.random() < cfg.audio_databend_prob:
        for c in aud:
            if random.random() < cfg.audio_databend_frac:
                c["data"] = _databend_blob(
                    c["data"], random.randint(*cfg.audio_databend_bytes)
                )
    return aud


def stretch_audio(audio, target, seg_range, loop_range):
    """Loop n-chunk windows of the clip's audio x times until exactly `target` chunks.

    Cycles through the base audio (wrapping), taking a semi-random n-chunk window and
    repeating it a semi-random x times; the final loop is truncated so the total lands
    exactly on `target` -- i.e. sum(n*x) == target at chunk granularity.
    """
    if not audio or target <= 0:
        return []
    out = []
    m = len(audio)
    pos = 0
    while len(out) < target:
        n = min(random.randint(*seg_range), m)
        seg = [audio[(pos + i) % m] for i in range(n)]
        pos = (pos + n) % m
        for _ in range(random.randint(*loop_range)):
            for c in seg:
                if len(out) >= target:
                    break
                out.append(dict(c))
            if len(out) >= target:
                break
    return out


def _video_reorder(cfg, frames):
    """Roll the P-frame reorder/delete mangles in place.

    Keyframe(s) are anchored at the front; only the non-key P-frames are shuffled /
    reversed / bounced / skipped. Each `frame` is a video-only {v, key} pair.
    """
    keys = [f for f in frames if f["key"]]
    nk = [f for f in frames if not f["key"]]
    if len(nk) < 2:
        return frames
    if random.random() < cfg.video_shuffle_prob:
        random.shuffle(nk)
    if random.random() < cfg.video_reverse_prob:
        nk.reverse()
    if random.random() < cfg.video_bounce_prob:
        nk = nk + [{"v": dict(f["v"]), "key": False} for f in reversed(nk)]
    if random.random() < cfg.video_skip_prob:
        drop = {i for i in range(len(nk)) if random.random() < cfg.video_skip_frac}
        nk = [f for i, f in enumerate(nk) if i not in drop] or nk[:1]
    frames[:] = keys + nk
    return frames


def mosh_segment(cfg, segment, keep_keyframe=False, donor_pframes=None, intensity=1.0,
                 av_ratio=DEFAULT_AUDIO_VIDEO_RATIO):
    """Mosh one clip and return (interleaved_chunks, this_clip_pframes).

    Video: optional motion transplant, P-frame duplication, keyframe deletion, and the
    reorder/skip/databend rolls. Audio is loop-stretched (semi-random n-chunk windows
    repeated x times) to match the final video frame count, then gets its own mangles,
    and is interleaved 1:1. `this_clip_pframes` (the clip's genuine P-frame payloads,
    captured before any transplant) seeds the next clip's transplant donor. `av_ratio`
    is the source's audio-chunks-per-video-frame ratio (scenes.audio_video_ratio).
    """
    video = [c for c in segment if c["stream"] == "v"]
    audio = [c for c in segment if c["stream"] == "a"]

    # capture genuine motion for the NEXT clip's transplant donor (before we overwrite it)
    this_pframes = [c["data"] for c in video if not c["key"]]

    # --- motion transplant: donor P-frame payloads onto this clip's keyframe canvas ---
    if donor_pframes and random.random() < cfg.video_transplant_prob:
        di = 0
        for c in video:
            if not c["key"]:
                c["data"] = donor_pframes[di % len(donor_pframes)]
                di += 1

    # --- video frames as {v, key} pairs ---
    frames = [{"v": c, "key": c["key"]} for c in video]

    # --- duplicate random P-frames (intensity scales the copy count) ---
    p_indices = [j for j, f in enumerate(frames) if not f["key"]]
    if p_indices:
        k = min(random.randint(*cfg.dup_frames_range), len(p_indices))
        hi = max(2, int(round(cfg.dup_count_range[1] * intensity)))
        lo = min(cfg.dup_count_range[0], hi)
        copies = {j: random.randint(lo, hi) for j in random.sample(p_indices, k)}
        dup = []
        for j, f in enumerate(frames):
            dup.append(f)
            for _ in range(copies.get(j, 0)):
                dup.append({"v": dict(f["v"]), "key": False})
        frames = dup

    # --- maybe delete the leading keyframe ---
    if not keep_keyframe and random.random() < cfg.keyframe_delete_prob:
        for k, f in enumerate(frames):
            if f["key"]:
                del frames[k]
                break

    # --- P-frame reorder/skip ---
    _video_reorder(cfg, frames)

    # --- video databend (corrupt macroblocks; skip past the VOP start code) ---
    if random.random() < cfg.video_databend_prob:
        for f in frames:
            if not f["key"] and random.random() < cfg.video_databend_frac:
                f["v"] = dict(f["v"])
                f["v"]["data"] = _databend_blob(
                    f["v"]["data"], random.randint(*cfg.video_databend_bytes), skip=16
                )

    vout = [f["v"] for f in frames]

    # --- loop-stretch audio to match the video DURATION, then roll the audio mangles ---
    # An AC3 frame (~32ms) is shorter than a video frame (~33.4ms), so matching audio chunk
    # count to video frame count 1:1 makes the audio run ~4% fast and the drift accumulates
    # across the output. Scale the target by the duration ratio (av_ratio) so the stretched
    # audio spans the same wall-clock time as the stretched video.
    aout = []
    if audio:
        target = max(1, round(len(vout) * av_ratio))
        aout = stretch_audio(audio, target, cfg.audio_seg_range, cfg.audio_loop_range)
        if aout:
            mangle_audio(cfg, aout)

    # --- interleave, spreading the audio chunks evenly across the video frames ---
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
    return out, this_pframes
