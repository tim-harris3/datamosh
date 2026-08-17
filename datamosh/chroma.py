"""Chroma isolation / corruption: glitch the colour while brightness stays sharp.

Unlike the rest of the toolkit, this can't work at the RIFF/AVI byte level: in the
compressed MPEG-4 bitstream the luma and chroma DCT coefficients are interleaved per
macroblock, so there are no "chroma bytes" to edit. Raw yuv420p is the only place
chroma is a separate, contiguous plane -- so chroma_databend() round-trips through a
decode/encode, touching the U/V planes while Y passes through untouched, then
re-emits a mosh-ready single-keyframe AVI that run_mosh / mosh_segment can still
mangle further.
"""

import random
import subprocess

from . import ffmpeg, paths

CHROMA_MODES = ["databend", "shift", "invert", "bias", "gray", "swap"]

_INVERT_TABLE = bytes(255 - i for i in range(256))  # chroma "invert" lookup


def _corrupt_plane(plane, mode, n_bytes=0, shift=0, bias=0):
    """Corrupt one chroma plane (a single frame's U or V, as raw bytes) and return it.

    databend XORs random bytes (digital speckle); shift rolls the plane (diagonal colour
    bleed); invert flips the channel (complementary hue); bias adds a constant (hue push);
    gray neutralises it to 128 (kills that colour axis). Unknown modes pass through.
    """
    if not plane:
        return plane
    if mode == "databend":
        ba = bytearray(plane)
        for _ in range(n_bytes):
            ba[random.randrange(len(ba))] ^= random.randint(1, 255)
        return bytes(ba)
    if mode == "shift":
        k = shift % len(plane)
        return plane[k:] + plane[:k]
    if mode == "invert":
        return plane.translate(_INVERT_TABLE)
    if mode == "bias":
        return plane.translate(bytes((i + bias) % 256 for i in range(256)))
    if mode == "gray":
        return b"\x80" * len(plane)
    return plane


def _corrupt_chroma_frame(frame, ysize, csize, mode, planes,
                          databend_bytes, shift_range, bias_range):
    """Return a planar yuv420p frame with only its selected chroma plane(s) corrupted.

    'swap' exchanges the U and V planes wholesale (needs both, ignores `planes`); every
    other mode rolls a fresh amount per touched plane so U and V drift independently.
    """
    y = frame[:ysize]
    u = frame[ysize:ysize + csize]
    v = frame[ysize + csize:ysize + 2 * csize]
    if mode == "swap":
        return y + v + u
    if "u" in planes:
        u = _corrupt_plane(u, mode, random.randint(*databend_bytes),
                           random.randint(*shift_range), random.randint(*bias_range))
    if "v" in planes:
        v = _corrupt_plane(v, mode, random.randint(*databend_bytes),
                           random.randint(*shift_range), random.randint(*bias_range))
    return y + u + v


def chroma_databend(src, dst, mode="databend", planes="uv", frac=0.10,
                    fps=None, seed=None, keep_audio=True,
                    databend_bytes=(64, 512), shift_range=(-4000, 4000),
                    bias_range=(-48, 48)):
    """Decode `src`, corrupt ONLY its chroma (U/V) planes, and write a mosh-ready AVI to `dst`.

    Luma (Y) passes through untouched, so brightness and structure stay razor-sharp while
    the colour detaches and drifts -- the ghostly "chroma-bleed" mosh. Frames are streamed
    decode -> transform -> encode through two ffmpeg pipes (no whole-clip buffer). The
    output matches extract_shot's single-keyframe MPEG-4 ASP settings, so it can be fed
    straight back into run_mosh / parse_avi / mosh_segment for further byte-level mangling.

    mode   -- one of CHROMA_MODES, or 'random' to reroll a fresh mode per corrupted frame
    planes -- chroma plane(s) to touch: 'u', 'v', or 'uv' (ignored by swap, which needs both)
    frac   -- fraction of frames affected; the rest pass clean, so the corruption flickers
              in and out instead of sitting on every frame.
    databend_bytes / shift_range / bias_range -- (lo, hi) amounts for the matching modes.
    fps -- output frame rate; None (default) probes the source's own rate. Overriding
           it desyncs the video from the kept audio track.
    Returns `dst`.
    """
    ffmpeg.require_ffmpeg()
    src, dst = paths.resolve(src), paths.resolve(dst)
    if fps is None:
        fps = ffmpeg.frame_rate(src)
    if seed is not None:
        random.seed(seed)
    if mode != "random" and mode not in CHROMA_MODES:
        raise ValueError(f"unknown chroma mode {mode!r}; expected 'random' or one of {CHROMA_MODES}")
    w, h = ffmpeg.dimensions(src)
    if w % 2 or h % 2:
        raise ValueError(f"chroma_databend needs even dimensions, got {w}x{h}")
    ysize, csize = w * h, (w // 2) * (h // 2)
    frame_size = ysize + 2 * csize

    dec = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", src,
         "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    enc_cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{w}x{h}",
               "-r", f"{fps:.6f}", "-i", "-"]
    if keep_audio:  # pull audio straight from the original (raw pipe carries video only)
        enc_cmd += ["-i", src, "-map", "0:v:0", "-map", "1:a:0?",
                    *ffmpeg.AUDIO_ENCODE_FLAGS]
    enc_cmd += [*ffmpeg.VIDEO_ENCODE_FLAGS, "-f", "avi", dst]
    enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    n_frames = n_hit = 0
    try:
        while True:
            frame = ffmpeg.read_exact(dec.stdout, frame_size)
            if len(frame) < frame_size:
                break  # last partial read = end of stream
            n_frames += 1
            if random.random() < frac:
                fmode = random.choice(CHROMA_MODES) if mode == "random" else mode
                frame = _corrupt_chroma_frame(frame, ysize, csize, fmode, planes,
                                              databend_bytes, shift_range, bias_range)
                n_hit += 1
            enc.stdin.write(frame)
    finally:
        if dec.stdout:
            dec.stdout.close()
        if enc.stdin:
            enc.stdin.close()
        dec.wait()
        enc.wait()
    if enc.returncode:
        raise subprocess.CalledProcessError(enc.returncode, enc_cmd)
    print(f"chroma {mode} on '{planes}': {n_hit}/{n_frames} frames -> {dst}")
    return dst
