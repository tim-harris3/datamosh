"""Pixel sorting: smear runs of pixels into ordered streaks (the Asendorf glitch).

Like chroma.py, this can't work at the RIFF/AVI byte level -- sorting needs actual
pixel values, which only exist after a decode. Frames are streamed through two
ffmpeg pipes as raw rgb24 (decode -> sort -> encode, no whole-clip buffer), and the
result is re-emitted as a mosh-ready single-keyframe AVI that run_mosh /
mosh_segment can still mangle further.

The algorithm family: pick intervals of each row (or column), then sort the pixels
inside each interval by some key. The interval rule is the `mode` (threshold /
bright / dark / bands / full) and the sort key is the `key` (luma / sat / hue /
red / green / blue); different combinations give anything from subtle streaking in
the highlights to full-frame venetian-blind melts.
"""

import logging
import random

import numpy as np

from . import ffmpeg, paths

logger = logging.getLogger(__name__)

PIXELSORT_MODES = ["threshold", "bright", "dark", "bands", "full"]
PIXELSORT_KEYS = ["luma", "sat", "hue", "red", "green", "blue"]


def _key_plane(rgb, key):
    """Per-pixel sort key for an HxWx3 uint8 frame, as an int32 HxW plane.

    luma is BT.601 scaled x1000 (0..255000); sat is max-min; hue is an integer
    degree approximation (ties collapse to 0). Exact scales don't matter -- only
    the ordering does.
    """
    r = rgb[..., 0].astype(np.int32)
    g = rgb[..., 1].astype(np.int32)
    b = rgb[..., 2].astype(np.int32)
    if key == "luma":
        return 299 * r + 587 * g + 114 * b
    if key == "red":
        return r
    if key == "green":
        return g
    if key == "blue":
        return b
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    if key == "sat":
        return mx - mn
    c = np.maximum(mx - mn, 1)  # hue; guard the gray divide-by-zero
    h = np.where(
        mx == r,
        (g - b) * 60 // c % 360,
        np.where(mx == g, (b - r) * 60 // c + 120, (r - g) * 60 // c + 240),
    )
    return np.where(mx == mn, 0, h)


def _sort_intervals(rgb, keyp, mask, breaks, reverse):
    """Sort pixels by key within each contiguous masked run of each row, in place.

    Fully vectorised: number the runs (a run starts where the mask switches on, or
    at a break point inside a masked stretch), then one stable lexsort by
    (run_id, key) reorders every run at once. Runs never cross row boundaries
    because each row's first masked pixel is always a run start.
    """
    prev = np.zeros_like(mask)
    prev[:, 1:] = mask[:, :-1]
    starts = mask & (~prev | breaks)
    run_id = np.cumsum(starts.ravel())
    idx = np.flatnonzero(mask.ravel())
    if idx.size == 0:
        return
    k = keyp.ravel()[idx]
    if reverse:
        k = -k
    order = np.lexsort((k, run_id[idx]))
    flat = rgb.reshape(-1, 3)
    flat[idx] = flat[idx[order]]


def _sort_frame(rgb, mode, key, direction, lo, hi, break_p, reverse, rng):
    """Return one HxWx3 frame pixel-sorted according to the rolled parameters."""
    if direction == "v":
        rgb = np.ascontiguousarray(rgb.transpose(1, 0, 2))
    luma = _key_plane(rgb, "luma")
    keyp = luma if key == "luma" else _key_plane(rgb, key)
    if mode == "threshold":
        mask = (luma >= lo * 1000) & (luma <= hi * 1000)
    elif mode == "bright":
        mask = luma >= hi * 1000
    elif mode == "dark":
        mask = luma <= lo * 1000
    else:  # bands / full: every pixel participates
        mask = np.ones(luma.shape, bool)
    if mode == "bands":
        breaks = rng.random(luma.shape) < break_p
    else:
        breaks = np.zeros(luma.shape, bool)
    _sort_intervals(rgb, keyp, mask, breaks, reverse)
    if direction == "v":
        rgb = np.ascontiguousarray(rgb.transpose(1, 0, 2))
    return rgb


def pixel_sort(
    src,
    dst,
    mode="threshold",
    key="luma",
    direction="h",
    frac=0.20,
    lo=64,
    hi=192,
    band_range=(8, 120),
    reverse=False,
    fps=None,
    seed=None,
    keep_audio=True,
    encoder="mpeg4",
    progress=None,
):
    """Decode `src`, pixel-sort a fraction of its frames, and write a mosh-ready AVI to `dst`.

    Each affected frame has runs of pixels reordered into monotone streaks -- the
    classic pixel-sorting glitch. The output matches extract_shot's single-keyframe
    MPEG-4 ASP settings, so it can be fed straight back into run_mosh / parse_avi /
    mosh_segment for further byte-level mangling.

    mode      -- interval rule, one of PIXELSORT_MODES, or 'random' to reroll per frame:
                 threshold sorts runs whose luma sits inside [lo, hi] (midtone streaks);
                 bright sorts runs >= hi; dark sorts runs <= lo; bands splits full rows
                 at random points (venetian-blind melt); full sorts whole rows.
    key       -- pixel value to sort by, one of PIXELSORT_KEYS, or 'random' per frame.
    direction -- 'h' (sort along rows), 'v' (along columns), or 'random' per frame.
    frac      -- fraction of frames affected; the rest pass clean, so the sorting
                 flickers in and out instead of sitting on every frame.
    lo / hi   -- luma bounds (0..255) for the threshold/bright/dark interval rules.
    band_range-- (lo, hi) mean band length in pixels for 'bands'; rerolled per frame.
    reverse   -- sort descending instead of ascending.
    fps       -- output frame rate; None (default) probes the source's own rate.
                 Overriding it desyncs the video from the kept audio track.
    encoder   -- moshable video encoder for the re-emit, 'mpeg4' or 'xvid'.
    Returns `dst`.
    """
    ffmpeg.require_ffmpeg()
    ffmpeg.require_encoder(encoder)
    src, dst = paths.resolve(src), paths.resolve(dst)
    if fps is None:
        fps = ffmpeg.frame_rate(src)
    if seed is not None:
        random.seed(seed)
    if mode != "random" and mode not in PIXELSORT_MODES:
        raise ValueError(
            f"unknown pixelsort mode {mode!r}; expected 'random' or one of {PIXELSORT_MODES}"
        )
    if key != "random" and key not in PIXELSORT_KEYS:
        raise ValueError(
            f"unknown pixelsort key {key!r}; expected 'random' or one of {PIXELSORT_KEYS}"
        )
    if direction not in ("h", "v", "random"):
        raise ValueError(
            f"unknown direction {direction!r}; expected 'h', 'v', or 'random'"
        )
    rng = np.random.default_rng(random.getrandbits(32))
    w, h = ffmpeg.dimensions(src)
    if w % 2 or h % 2:
        raise ValueError(f"pixel_sort needs even dimensions, got {w}x{h}")

    n_hit = 0

    def sort(frame):
        nonlocal n_hit
        if random.random() < frac:
            fmode = random.choice(PIXELSORT_MODES) if mode == "random" else mode
            fkey = random.choice(PIXELSORT_KEYS) if key == "random" else key
            fdir = random.choice(["h", "v"]) if direction == "random" else direction
            break_p = 1.0 / max(1, random.randint(*band_range))
            rgb = np.frombuffer(frame, np.uint8).reshape(h, w, 3).copy()
            rgb = _sort_frame(rgb, fmode, fkey, fdir, lo, hi, break_p, reverse, rng)
            frame = rgb.tobytes()
            n_hit += 1
        return frame

    n_frames = ffmpeg.stream_transform(
        src,
        dst,
        sort,
        pix_fmt="rgb24",
        width=w,
        height=h,
        frame_size=w * h * 3,
        fps=fps,
        keep_audio=keep_audio,
        extra_out_flags=("-pix_fmt", "yuv420p"),
        encoder=encoder,
        progress=progress,
        total_frames=max(1, round(ffmpeg.duration(src) * fps)),
    )
    logger.info(f"pixelsort {mode}/{key}/{direction}: {n_hit}/{n_frames} frames -> {dst}")
    return dst
