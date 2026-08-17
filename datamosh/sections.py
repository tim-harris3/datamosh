"""Keyframe-section tools: cut a chunk list at its keyframes, mosh section by
section, and splice sections between different videos.

These generalize the original truck experiment: make_moshable() plants random
keyframes so any footage divides into random-length sections, keyframe_shots()
turns those keyframes into a shot map for run_mosh(shots=...), and the
split/replace/delete helpers let you swap sections in from other AVIs so one
video's motion blooms over another's pixels.
"""

import glob
import os
import random
import subprocess

from . import ffmpeg, paths
from .avi import parse_avi
from .effects import mosh_segment
from .scenes import DEFAULT_AUDIO_VIDEO_RATIO


def make_moshable(src, dst, gap_range=(0.2, 10.0), duration=None):
    """Re-encode src to MPEG-4 ASP AVI with keyframes only at random timestamps.

    Same encode shape as extract_shot (native mpeg4, no B-frames, cfr, AC3) but instead
    of a single leading keyframe, `-force_key_frames` plants one at each randomly-spaced
    timestamp; `-g 999999 -sc_threshold 0` suppress all others. Audio is forced to 48 kHz
    stereo AC3 so its chunks match other moshable AVIs' audio and sections interleave
    into one decodable stream after splicing.
    """
    src, dst = paths.resolve(src), paths.resolve(dst)
    total = ffmpeg.duration(src)
    if duration:
        total = min(total, duration)
    times, t = [], 0.0
    while t < total:
        times.append(t)
        t += random.uniform(*gap_range)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += [*ffmpeg.VIDEO_ENCODE_FLAGS,
            "-force_key_frames", ",".join(f"{x:.3f}" for x in times),
            *ffmpeg.AUDIO_ENCODE_FLAGS, "-ar", "48000", "-ac", "2",
            "-f", "avi", dst]
    subprocess.run(cmd, check=True)
    print(f"moshable: {dst} ({len(times)} random keyframes over {total:.1f}s)")
    return dst


def keyframe_shots(path):
    """Read a file's keyframe timestamps (container-level probe, no decode) and return
    its keyframe sections as a run_mosh shot map [(t0, t1), ...] -- so run_mosh picks
    random-length sections instead of scene cuts."""
    path = paths.resolve(path)
    bounds = ffmpeg.keyframe_times(path) + [ffmpeg.duration(path)]
    return [
        (bounds[i], bounds[i + 1])
        for i in range(len(bounds) - 1)
        if bounds[i + 1] > bounds[i]
    ]


def split_sections(chunks):
    """Split an interleaved chunk list into sections, each starting at a video
    keyframe (any leading key-less chunks form the first section)."""
    sections, cur = [], []
    for c in chunks:
        if c["stream"] == "v" and c["key"] and any(x["stream"] == "v" for x in cur):
            sections.append(cur)
            cur = []
        cur.append(c)
    if cur:
        sections.append(cur)
    return sections


def mosh_pass(cfg, chunks, label="mosh pass", av_ratio=DEFAULT_AUDIO_VIDEO_RATIO):
    """Run mosh_segment over every keyframe section, like run_mosh's render loop:
    the first section keeps its keyframe (valid stream start), intensity escalates
    across the run, and each clip's genuine P-frames seed the next transplant."""
    sections = split_sections(chunks)
    out, prev_pframes = [], None
    n = len(sections)
    for i, sec in enumerate(sections):
        intensity = 1 + cfg.escalate * (i / max(1, n - 1)) if cfg.escalate else 1.0
        moshed, prev_pframes = mosh_segment(
            cfg, sec, keep_keyframe=(i == 0), donor_pframes=prev_pframes,
            intensity=intensity, av_ratio=av_ratio,
        )
        out.extend(moshed)
    vin = sum(1 for c in chunks if c["stream"] == "v")
    vout = sum(1 for c in out if c["stream"] == "v")
    print(f"{label}: {n} sections, {vin}->{vout} vframes")
    return out


def example_section_pool(examples_dir):
    """Return a fn yielding a random keyframe section from a random AVI in `examples_dir`.
    Files are parsed lazily and cached; sections are shallow-copied so later mosh
    passes can't mutate the cache."""
    examples_dir = paths.resolve(examples_dir)
    files = sorted(glob.glob(os.path.join(glob.escape(examples_dir), "*.avi")))
    if not files:
        raise RuntimeError(f"no .avi files in {examples_dir}")
    cache = {}

    def pick():
        path = random.choice(files)
        if path not in cache:
            _, _, chunks = parse_avi(path)
            cache[path] = [
                s for s in split_sections(chunks)
                if any(c["stream"] == "v" for c in s)
            ]
        sections = cache[path]
        if not sections:
            return None
        return [dict(c) for c in random.choice(sections)]

    return pick


def replace_sections(chunks, prob, pick, tag="spliced"):
    """Randomly swap keyframe sections for sections yielded by `pick()`.

    Swapped-in chunks are marked with `tag` so delete_tagged_keyframes can target them.
    The first section is never replaced: its keyframe is the stream's valid start and
    must survive (mosh_pass keeps section 0's keyframe too)."""
    sections = split_sections(chunks)
    out, replaced = [], 0
    for i, sec in enumerate(sections):
        sub = pick() if i and random.random() < prob else None
        if sub:
            for c in sub:
                c[tag] = True
            out.extend(sub)
            replaced += 1
        else:
            out.extend(sec)
    print(f"splice: replaced {replaced}/{len(sections)} sections")
    return out


def delete_tagged_keyframes(chunks, frac, tag="spliced"):
    """Delete `frac` of the video keyframes marked with `tag` (remove the chunk outright)
    so the surrounding motion blooms over the spliced pixels instead of them starting
    clean."""
    key_idx = [
        i for i, c in enumerate(chunks)
        if c["stream"] == "v" and c["key"] and c.get(tag)
    ]
    kill = set(random.sample(key_idx, round(len(key_idx) * frac)))
    print(f"delete: {len(kill)}/{len(key_idx)} spliced keyframes removed")
    return [c for i, c in enumerate(chunks) if i not in kill]
