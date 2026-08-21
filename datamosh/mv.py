"""Motion-vector analysis: per-frame MV fields as numpy arrays, plus the
codecview arrow-overlay renderer (roadmap phase 2 -- analysis only, no
bitstream edits).

The field model is the one the whole motion-vector roadmap shares: one float32
array of shape (mb_h, mb_w, 2) per video frame -- a (vx, vy) entry per 16x16
macroblock, in half-pel units (vx=4 means the block references two pixels to
the right in the previous frame; MPEG-4's own resolution). Frames with no
vectors -- keyframes, skipped/static blocks -- are zero. Finer-than-16x16
vectors (inter4v 8x8 blocks) collapse into their macroblock cell by mean.

Two ways to fill that model:

- "probe": the decoder's own vectors, decoded with `-flags2 +export_mvs` and
  read from ffprobe's `-show_frames` side data. This is the exact decode-side
  oracle later phases want -- but stock ffmpeg builds to date report the
  side-data *type* without serializing the vectors themselves, so this backend
  raises a clear error on builds that can't, and the JSON parsing is
  deliberately defensive about per-build schema drift.
- "estimate": per-macroblock phase correlation over the decoded luma planes --
  no side data needed, works on every ffmpeg build. Integer-pel resolution
  (reported in half-pels), search radius +-8 px, flat blocks report zero just
  like the encoder skips them. An approximation of the coded vectors, not a
  readback -- good for eyeballing fields and driving analysis, not for
  bit-exact oracles.

extract_mv_fields() defaults to trying the probe and falling back to the
estimator, so scripts written against it start returning exact vectors the day
the ffprobe build underneath learns to serialize them.
"""

import json
import logging
import math
import subprocess

import numpy as np

from . import paths
from .ffmpeg import dimensions, read_exact, require_ffmpeg, video_keyflags

logger = logging.getLogger(__name__)

MB_SIZE = 16  # MPEG-4 macroblock edge, the field's cell size, in pixels

_BACKENDS = ("auto", "probe", "estimate")
_WIN = 32  # estimator window: one macroblock + _SEARCH px of context each side
_SEARCH = 8  # largest displacement the estimator can see, in pixels
_FLAT_STD = 2.0  # luma std below which a block is too flat to correlate


class _ProbeUnsupported(RuntimeError):
    """This ffprobe build reports MV side data but cannot serialize the vectors."""


def _mb_grid(width, height):
    return math.ceil(width / MB_SIZE), math.ceil(height / MB_SIZE)


def extract_mv_fields(path, backend="auto"):
    """Decode `path`'s video and return one motion-vector field per frame.

    Each field is float32 of shape (mb_h, mb_w, 2) -- `field[my, mx]` is the
    (vx, vy) motion of the macroblock at pixel (16*mx, 16*my), in half-pel
    units, `mb_w/mb_h = ceil(width/16)/ceil(height/16)`. Keyframes and blocks
    with no motion are zero. `backend` is "probe" (the decoder's exported
    vectors via ffprobe -- exact, but most ffmpeg builds can't serialize them
    and raise here), "estimate" (numpy phase correlation on decoded luma --
    works everywhere, approximate), or "auto" (probe when the build supports
    it, otherwise estimate; the fallback is logged). See the module docstring
    for the trade-off.
    """
    if backend not in _BACKENDS:
        raise ValueError(f"unknown backend {backend!r}; expected one of {', '.join(_BACKENDS)}")
    require_ffmpeg()
    path = paths.resolve(path)
    if backend in ("auto", "probe"):
        try:
            return _probe_fields(path)
        except _ProbeUnsupported as e:
            if backend == "probe":
                raise
            # routine on today's stock builds, so debug -- not per-run noise
            logger.debug(f"{e} -- estimating from decoded frames instead")
    return _estimate_fields(path)


def mv_overlay(src, dst, mv="pf", qscale=4):
    """Render `src` with ffmpeg's codecview motion-vector arrows drawn on every
    frame -- the cheap visual check that a field (or a mosh built from one)
    does what you think. `mv` picks which vectors to draw: "pf" is P-frame
    forward motion (the only kind the moshable contract produces; "bf"/"bb"
    exist for foreign footage with B-frames, combinable as "pf+bf"). The
    overlay is a plain mpeg4 AVI re-encode, video only -- a debug artifact,
    not a moshable. Returns dst.
    """
    require_ffmpeg()
    src, dst = paths.resolve(src), paths.resolve(dst)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-flags2",
            "+export_mvs",
            "-i",
            src,
            "-vf",
            f"codecview=mv={mv}",
            "-c:v",
            "mpeg4",
            "-qscale:v",
            str(qscale),
            "-an",
            dst,
        ],
        check=True,
    )
    logger.info(f"wrote {dst}")
    return dst


def field_stats(field):
    """Summarize one field for tables: dict with `moving` (macroblocks with
    nonzero motion), `mean_mag` (mean |v| over those, half-pels), and
    `direction` (8-way compass of the mean vector in screen coordinates,
    +y = down = S; "-" when nothing moves)."""
    mag = np.hypot(field[..., 0], field[..., 1])
    moving = mag > 0
    n = int(moving.sum())
    if not n:
        return {"moving": 0, "mean_mag": 0.0, "direction": "-"}
    vx = float(field[..., 0][moving].mean())
    vy = float(field[..., 1][moving].mean())
    return {
        "moving": n,
        "mean_mag": float(mag[moving].mean()),
        "direction": _compass(vx, vy),
    }


def _compass(vx, vy):
    """8-way compass name for a screen-space vector (+x = E, +y = down = S)."""
    if math.hypot(vx, vy) < 1e-6:
        return "-"
    ang = math.degrees(math.atan2(-vy, vx)) % 360.0
    return ("E", "NE", "N", "NW", "W", "SW", "S", "SE")[int((ang + 22.5) // 45) % 8]


# --- the probe backend: ffprobe side-data JSON -------------------------------


def _probe_fields(path):
    """Fields from the decoder's exported vectors, via ffprobe -show_frames."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-flags2",
            "+export_mvs",
            "-select_streams",
            "v:0",
            "-show_frames",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    try:
        frames = json.loads(out).get("frames", [])
    except json.JSONDecodeError as e:
        raise RuntimeError(f"unparseable ffprobe -show_frames JSON for {path}: {e}") from e
    if not frames:
        raise RuntimeError(f"ffprobe found no video frames in {path}")
    width, height = _probed_dimensions(frames, path)
    return _fields_from_probe(frames, width, height, path)


def _probed_dimensions(frames, path):
    """(width, height) from the probed frames, falling back to a stream probe."""
    for f in frames:
        if isinstance(f, dict) and f.get("width") and f.get("height"):
            return int(f["width"]), int(f["height"])
    return dimensions(path)


def _fields_from_probe(frames, width, height, path):
    """The pure parsing step, split out so tests can pin the schema handling.

    Raises _ProbeUnsupported when frames carry motion-vector side data but no
    build serialized the vectors themselves (every stock ffmpeg to date), and
    RuntimeError on schemas we don't recognize -- silently wrong fields would
    poison everything built on top.
    """
    mb_w, mb_h = _mb_grid(width, height)
    fields, saw_side_data, saw_vectors = [], False, False
    for frame in frames:
        vectors = None
        side_data = frame.get("side_data_list") if isinstance(frame, dict) else None
        for sd in side_data or []:
            if not isinstance(sd, dict):
                continue
            if "motion vector" not in str(sd.get("side_data_type", "")).lower():
                continue
            saw_side_data = True
            vectors = _side_data_vectors(sd)
            if vectors is not None:
                saw_vectors = True
            break
        fields.append(_field_from_vectors(vectors or [], mb_w, mb_h, path))
    if saw_side_data and not saw_vectors:
        raise _ProbeUnsupported(
            f"this ffprobe build reports motion-vector side data for {path} but does "
            "not serialize the vectors themselves (no stock ffmpeg build does, as of "
            "the 2024/2025 releases)"
        )
    return fields


def _side_data_vectors(sd):
    """The per-vector list inside one side-data dict, or None if this build
    printed only the side-data type. The key isn't pinned by any spec, so
    accept the plausible spellings."""
    for key in ("motion_vectors", "mvs", "vectors"):
        val = sd.get(key)
        if isinstance(val, list):
            return val
    return None


def _field_from_vectors(vectors, mb_w, mb_h, path):
    """Collapse one frame's vector dicts into its (mb_h, mb_w, 2) field.

    Each AVMotionVector carries the block's *center* (dst_x/dst_y), the raw
    motion (motion_x/motion_y over motion_scale, full-pel), and the redundant
    absolute src position. Finer-than-MB blocks land in the same cell and
    average. Prefers motion_x/motion_scale (sub-pel exact); falls back to
    src-dst (full-pel) when a build omits them; raises on anything else.
    """
    field = np.zeros((mb_h, mb_w, 2), np.float32)
    if not vectors:
        return field
    sums = np.zeros((mb_h, mb_w, 2))
    counts = np.zeros((mb_h, mb_w))
    for v in vectors:
        if not isinstance(v, dict):
            raise RuntimeError(
                f"unexpected motion-vector entry {v!r} in ffprobe output for {path}"
            )
        try:
            dst_x, dst_y = float(v["dst_x"]), float(v["dst_y"])
            scale = float(v.get("motion_scale") or 0)
            if scale and "motion_x" in v and "motion_y" in v:
                vx = 2.0 * float(v["motion_x"]) / scale
                vy = 2.0 * float(v["motion_y"]) / scale
            else:
                vx = 2.0 * (float(v["src_x"]) - dst_x)
                vy = 2.0 * (float(v["src_y"]) - dst_y)
        except (KeyError, TypeError, ValueError) as e:
            raise RuntimeError(
                f"motion-vector side data for {path} has an unrecognized shape "
                f"(fields {sorted(v)}): {e!r} -- this ffprobe build's schema isn't "
                "supported yet"
            ) from e
        mx = min(max(int(dst_x) // MB_SIZE, 0), mb_w - 1)
        my = min(max(int(dst_y) // MB_SIZE, 0), mb_h - 1)
        sums[my, mx, 0] += vx
        sums[my, mx, 1] += vy
        counts[my, mx] += 1
    filled = counts > 0
    field[filled] = (sums[filled] / counts[filled][:, None]).astype(np.float32)
    return field


# --- the estimate backend: phase correlation on decoded luma -----------------


def _estimate_fields(path):
    """Fields estimated from the decoded frames, one streaming gray-plane pass."""
    width, height = dimensions(path)
    keyflags = video_keyflags(path)
    frame_size = width * height
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        path,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "-",
    ]
    dec = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    mb_w, mb_h = _mb_grid(width, height)
    fields, prev = [], None
    try:
        while True:
            buf = read_exact(dec.stdout, frame_size)
            if len(buf) < frame_size:
                break
            cur = np.frombuffer(buf, np.uint8).reshape(height, width)
            i = len(fields)
            is_key = keyflags[i] if i < len(keyflags) else False
            if prev is None or is_key:
                fields.append(np.zeros((mb_h, mb_w, 2), np.float32))
            else:
                fields.append(_phase_correlate(prev, cur, mb_w, mb_h))
            prev = cur
    finally:
        if dec.stdout:
            dec.stdout.close()
        dec.wait()
    if dec.returncode:
        raise subprocess.CalledProcessError(dec.returncode, cmd)
    return fields


def _phase_correlate(prev, cur, mb_w, mb_h):
    """One field by per-macroblock phase correlation against the previous frame.

    Every macroblock gets a 32x32 window (its 16x16 plus 8 px of context on
    each side, edge-padded at the borders), and the normalized cross-power
    spectrum's peak is the block's shift between the frames. The sign is
    flipped into MV convention -- the vector points at where the block *came
    from* -- and doubled into half-pels. Displacements beyond the 8 px context
    can't genuinely be in the window, so the peak search is masked to kill
    wrap-around outliers, and blocks too flat to correlate (or matched against
    one) report zero, like the skipped macroblocks they would encode as.
    """
    pad_y, pad_x = _SEARCH + mb_h * MB_SIZE - prev.shape[0], _SEARCH + mb_w * MB_SIZE - prev.shape[1]
    spec = ((_SEARCH, pad_y), (_SEARCH, pad_x))
    a = np.pad(prev.astype(np.float32), spec, mode="edge")
    b = np.pad(cur.astype(np.float32), spec, mode="edge")
    win_a = np.lib.stride_tricks.sliding_window_view(a, (_WIN, _WIN))[::MB_SIZE, ::MB_SIZE]
    win_b = np.lib.stride_tricks.sliding_window_view(b, (_WIN, _WIN))[::MB_SIZE, ::MB_SIZE]
    spectrum = np.fft.rfft2(win_b) * np.conj(np.fft.rfft2(win_a))
    spectrum /= np.abs(spectrum) + 1e-6
    corr = np.fft.irfft2(spectrum, s=(_WIN, _WIN))
    shifts = np.fft.fftfreq(_WIN, 1 / _WIN).astype(int)  # index -> signed shift
    valid = (np.abs(shifts)[:, None] <= _SEARCH) & (np.abs(shifts)[None, :] <= _SEARCH)
    peak = np.where(valid, corr, -np.inf).reshape(mb_h, mb_w, -1).argmax(axis=-1)
    tx, ty = shifts[peak % _WIN], shifts[peak // _WIN]
    textured = (win_a.std(axis=(-2, -1)) > _FLAT_STD) & (win_b.std(axis=(-2, -1)) > _FLAT_STD)
    field = np.zeros((mb_h, mb_w, 2), np.float32)
    field[..., 0] = np.where(textured, -2.0 * tx, 0.0)
    field[..., 1] = np.where(textured, -2.0 * ty, 0.0)
    return field
