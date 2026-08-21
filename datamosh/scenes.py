"""Shot map: where the scene cuts are in a source video, and shot extraction.

build_scene_map() runs ffmpeg scene detection once per source (a full decode pass,
a few minutes for a long file) and caches the result in output/cache/scene_cuts.json
keyed by the source's name+size+mtime+threshold, so every later run is instant.
extract_shot() re-encodes one time range into a clean single-keyframe moshable AVI.
"""

import os
import re
import subprocess

from . import ffmpeg, paths

# Audio chunks per video frame for a duration match at NTSC 29.97 fps / 48 kHz AC3;
# audio_video_ratio() recomputes this from the real source when possible.
DEFAULT_AUDIO_VIDEO_RATIO = (1001 / 30000) / (1536 / 48000)


def detect_scene_cuts(source, threshold):
    """Scene-cut timestamps (seconds) via ffmpeg's per-frame scene score. Full decode pass."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            source,
            "-filter:v",
            f"select='gt(scene,{threshold})',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    cuts = []
    for line in proc.stderr.splitlines():
        if "showinfo" in line:
            m = re.search(r"pts_time:([0-9.]+)", line)
            if m:
                cuts.append(float(m.group(1)))
    return sorted(cuts)


def audio_video_ratio(source):
    """Audio-chunks-per-video-frame for a duration match: (1/video_fps) / (1536/ac3_rate).

    AC3 always packs 1536 samples/frame; dividing by the real sample rate and the real video
    frame rate yields the exact ratio, so duration-matched stretching works for any source.
    Probe the extracted MOSHABLE AVI, not the original file -- the AC3 re-encode can silently
    resample (e.g. 16 kHz in -> 32 kHz AC3 out), which changes the ratio.
    """
    source = paths.resolve(source)
    try:
        vfps = ffmpeg.frame_rate(source)
        ar = ffmpeg.sample_rate(source)
        return (1.0 / vfps) / (1536.0 / ar)
    except (subprocess.SubprocessError, ValueError, ZeroDivisionError):
        return DEFAULT_AUDIO_VIDEO_RATIO


def bounds_to_shots(bounds):
    """Turn boundary timestamps [t0, t1, ...] into shots [(t0, t1), ...], dropping
    empty spans (duplicate or out-of-order boundaries)."""
    return [
        (bounds[i], bounds[i + 1])
        for i in range(len(bounds) - 1)
        if bounds[i + 1] > bounds[i]
    ]


def build_scene_map(source, threshold=0.30):
    """Return (shots, duration_sec); shots = [(t0, t1), ...] between detected scene cuts.

    Cached in output/cache/scene_cuts.json (one entry per source+threshold), so only the
    first call on a new source pays the decode pass.
    """
    source = paths.resolve(source)
    cache_path = paths.CACHE_DIR / "scene_cuts.json"
    st = os.stat(source)
    sig = f"{os.path.basename(source)}:{st.st_size}:{int(st.st_mtime)}:{threshold}"
    cache = paths.load_json(cache_path, {})
    entry = cache.get(sig)
    if entry:
        return [tuple(s) for s in entry["shots"]], entry["duration"]

    duration = ffmpeg.duration(source)
    cuts = [t for t in detect_scene_cuts(source, threshold) if 0.0 < t < duration]
    shots = bounds_to_shots([0.0] + cuts + [duration])
    try:
        paths.ensure_output_dirs()
        cache[sig] = {"duration": duration, "shots": shots}
        paths.save_json(cache_path, cache)
    except OSError:
        pass  # cache write is best-effort; worst case the next run re-detects
    return shots, duration


def extract_shot(src, t0, dur, temp, encoder="mpeg4"):
    """Re-extract [t0, t0+dur) from the source as a single-keyframe MPEG-4 ASP AVI.

    Uses the shared moshable encode shape (native mpeg4 by default, libxvid for
    encoder="xvid"; either way no packed bitstream) and `-fps_mode cfr` so every
    displayed frame is exactly one '00dc' chunk -- otherwise ffmpeg pads the video with
    N-VOPs to sync to the copied audio and the demuxer later merges them, breaking the
    1-chunk-per-frame mapping the byte moshing relies on. Audio is re-encoded to AC3 (kept
    interleaved so it gets mangled too).
    """
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{t0:.3f}",
            "-i",
            src,
            "-t",
            f"{dur:.3f}",
            *ffmpeg.video_encode_flags(encoder),
            *ffmpeg.AUDIO_ENCODE_FLAGS,
            "-f",
            "avi",
            temp,
        ],
        check=True,
    )
