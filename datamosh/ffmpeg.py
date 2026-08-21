"""Thin wrappers around the ffmpeg / ffprobe command-line tools.

Every probe and every shared encoder setting lives here, in exactly one place.
ffmpeg and ffprobe must be installed and on PATH -- require_ffmpeg() gives a
friendly error instead of a traceback when they are not.
"""

import logging
import os
import shutil
import subprocess

from . import paths

logger = logging.getLogger(__name__)

# The one true encode shape for moshable AVIs: native mpeg4 (MPEG-4 ASP, no Xvid
# packed bitstream), a single keyframe unless forced otherwise, no B-frames, and
# `-fps_mode cfr` so every displayed frame is exactly one '00dc' chunk. Audio is
# AC3 so its fixed-size chunks survive byte-level mangling.
VIDEO_ENCODE_FLAGS = [
    "-c:v",
    "mpeg4",
    "-qscale:v",
    "4",
    "-g",
    "999999",
    "-bf",
    "0",
    "-sc_threshold",
    "0",
    "-fps_mode",
    "cfr",
]
AUDIO_ENCODE_FLAGS = ["-c:a", "ac3", "-b:a", "192k"]

DEFAULT_FPS = 30000 / 1001  # NTSC 29.97, the fallback frame rate


def require_ffmpeg():
    """Raise a clear, actionable error if ffmpeg/ffprobe are not installed."""
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        raise RuntimeError(
            f"{' and '.join(missing)} not found. datamosh needs ffmpeg installed and on "
            "your PATH -- download it from https://ffmpeg.org/download.html (or "
            "`winget install ffmpeg`), then reopen your terminal."
        )


def _probe(args):
    return subprocess.run(
        ["ffprobe", "-v", "error", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def duration(path):
    """Duration of the file in seconds."""
    return float(_probe(["-show_entries", "format=duration", "-of", "csv=p=0", path]))


def dimensions(path):
    """(width, height) of the first video stream."""
    out = _probe(
        [
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            path,
        ]
    )
    # streams with side data (e.g. iPhone rotation) get a trailing separator
    w, h = [v for v in out.splitlines()[0].split("x") if v]
    return int(w), int(h)


def frame_rate(path):
    """Average video frame rate (frames per second)."""
    fr = _probe(
        [
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate",
            "-of",
            "csv=p=0",
            path,
        ]
    )
    num, den = fr.splitlines()[0].strip(",").split("/")
    return float(num) / float(den)


def sample_rate(path):
    """Audio sample rate in Hz (48000 if the file has no audio stream)."""
    ar = _probe(
        [
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate",
            "-of",
            "csv=p=0",
            path,
        ]
    )
    return float(ar) if ar else 48000.0


def video_keyflags(path):
    """Per-video-packet keyframe booleans, via ffprobe packet flags."""
    out = _probe(
        [
            "-select_streams",
            "v:0",
            "-show_entries",
            "packet=flags",
            "-of",
            "csv=p=0",
            path,
        ]
    )
    return [("K" in line) for line in out.splitlines() if line.strip()]


def keyframe_times(path):
    """Timestamps (seconds) of every video keyframe (container-level probe, no decode)."""
    out = _probe(
        [
            "-select_streams",
            "v:0",
            "-show_entries",
            "packet=pts_time,flags",
            "-of",
            "csv=p=0",
            path,
        ]
    )
    times = set()
    for line in out.splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and "K" in parts[1] and parts[0] not in ("", "N/A"):
            times.add(float(parts[0]))
    return sorted(times)


def keyframe_thumbnails(path, out_dir, width=160, cap=400):
    """Write one JPEG per video keyframe to out_dir (0001.jpg, 0002.jpg, ...) in
    keyframe_times() order, so thumbnail N previews the Nth keyframe section.
    Returns the sorted list of written paths, at most `cap` of them.

    Single decode pass with `-skip_frame nokey`; if the decoder's keyframe count
    disagrees with the container's packet flags (open-GOP / odd containers), falls
    back to exact per-timestamp fast-seek extraction.
    """
    path = paths.resolve(path)
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.endswith(".jpg"):
            os.remove(os.path.join(out_dir, f))
    times = keyframe_times(path)
    expected = min(len(times), cap)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-skip_frame",
            "nokey",
            "-i",
            path,
            "-vf",
            f"scale={width}:-2",
            "-fps_mode",
            "vfr",
            "-frames:v",
            str(cap),
            "-q:v",
            "5",
            os.path.join(out_dir, "%04d.jpg"),
        ],
        check=True,
    )
    written = sorted(
        os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".jpg")
    )
    if len(written) != expected:
        for f in written:
            os.remove(f)
        for i, t in enumerate(times[:cap]):
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{t:.3f}",
                    "-i",
                    path,
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale={width}:-2",
                    "-q:v",
                    "5",
                    os.path.join(out_dir, f"{i + 1:04d}.jpg"),
                ],
                check=True,
            )
        written = sorted(
            os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".jpg")
        )
    return written


def fixup(path):
    """Write a more broadly-seekable *_fixed.avi copy via `ffmpeg -c copy`.

    Returns the fixed path, or None if the pass failed (the raw output is still fine).
    """
    path = paths.resolve(path)
    fixed = os.path.splitext(path)[0] + "_fixed.avi"
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                path,
                "-c",
                "copy",
                fixed,
            ],
            check=True,
        )
        logger.info(f"wrote {fixed}")
        return fixed
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"ffmpeg fixup skipped: {e}")
        return None


def run_encode(cmd, *, progress=None, total_seconds=None):
    """Run an ffmpeg encode command, optionally reporting progress.

    With progress and total_seconds set, `-nostats -progress pipe:1` is appended
    and out_time lines from ffmpeg's progress stream drive progress(frac, msg);
    otherwise this is plain subprocess.run. Either way a nonzero exit raises
    CalledProcessError (stderr passes through to the console).
    """
    if not (progress and total_seconds and total_seconds > 0):
        subprocess.run(cmd, check=True)
        return
    cmd = [*cmd[:-1], "-nostats", "-progress", "pipe:1", cmd[-1]]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    try:
        for line in proc.stdout:
            key, _, val = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and val.isdigit():
                secs = int(val) / 1e6  # both keys are microseconds in practice
                progress(
                    min(secs / total_seconds, 1.0),
                    f"encode {secs:.0f}s/{total_seconds:.0f}s",
                )
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.wait()
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, cmd)


def read_exact(stream, n):
    """Read exactly n bytes from a pipe (stream.read may return short); return what arrives."""
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


def stream_transform(
    src,
    dst,
    transform,
    *,
    pix_fmt,
    width,
    height,
    frame_size,
    fps,
    keep_audio=True,
    extra_out_flags=(),
    progress=None,
    total_frames=None,
):
    """Stream src through a decode -> transform -> encode ffmpeg pipe pair.

    Decodes src to raw `pix_fmt` frames, calls transform(frame_bytes) -> frame_bytes
    on each complete frame (a short final read ends the stream), and encodes the
    result to dst as a moshable '-f avi' with VIDEO_ENCODE_FLAGS + extra_out_flags.
    No whole-clip buffering. Shared scaffolding for chroma_databend / pixel_sort.

    keep_audio pulls the audio track straight from src (the raw pipe carries video
    only). Returns the frame count. Raises CalledProcessError if either ffmpeg
    process fails -- a dead decoder would otherwise look like a clean early
    end-of-stream and silently truncate the output.

    progress(frac, msg) is called every ~30 frames when total_frames is also
    given (an estimate is fine -- it only drives a progress bar).
    """
    dec_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        src,
        "-f",
        "rawvideo",
        "-pix_fmt",
        pix_fmt,
        "-",
    ]
    dec = subprocess.Popen(dec_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    enc_cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        pix_fmt,
        "-s",
        f"{width}x{height}",
        "-r",
        f"{fps:.6f}",
        "-i",
        "-",
    ]
    if keep_audio:
        enc_cmd += ["-i", src, "-map", "0:v:0", "-map", "1:a:0?", *AUDIO_ENCODE_FLAGS]
    enc_cmd += [*VIDEO_ENCODE_FLAGS, *extra_out_flags, "-f", "avi", dst]
    enc = subprocess.Popen(
        enc_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    n_frames = 0
    try:
        while True:
            frame = read_exact(dec.stdout, frame_size)
            if len(frame) < frame_size:
                break  # last partial read = end of stream
            n_frames += 1
            enc.stdin.write(transform(frame))
            if progress and total_frames and n_frames % 30 == 0:
                progress(
                    min(n_frames / total_frames, 1.0),
                    f"frame {n_frames}/{total_frames}",
                )
    finally:
        if dec.stdout:
            dec.stdout.close()
        if enc.stdin:
            enc.stdin.close()
        dec.wait()
        enc.wait()
    if dec.returncode:
        raise subprocess.CalledProcessError(dec.returncode, dec_cmd)
    if enc.returncode:
        raise subprocess.CalledProcessError(enc.returncode, enc_cmd)
    return n_frames


def transcode(src, dst, seconds=None):
    """Transcode to a browser-playable H.264/AAC MP4 (the UI preview encode).

    seconds caps the output length; None converts the whole file.
    """
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", paths.resolve(src)]
    if seconds is not None:
        cmd += ["-t", str(seconds)]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        paths.resolve(dst),
    ]
    subprocess.run(cmd, check=True)
    return dst
