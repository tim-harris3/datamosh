"""Keyframe-section tools: cut a chunk list at its keyframes, mosh section by
section, and splice sections between different videos.

These generalize the original truck experiment: make_moshable() plants random
keyframes so any footage divides into random-length sections, keyframe_shots()
turns those keyframes into a shot map for run_mosh(shots=...), and the
split/replace/delete helpers let you swap sections in from other AVIs so one
video's motion blooms over another's pixels.
"""

import glob
import logging
import os
import random

from . import ffmpeg, paths
from .avi import header_info, parse_avi
from .config import escalation_intensity
from .effects import mosh_segment
from .scenes import DEFAULT_AUDIO_VIDEO_RATIO, bounds_to_shots

logger = logging.getLogger(__name__)


def make_moshable(src, dst, gap_range=(0.2, 10.0), duration=None, progress=None):
    """Re-encode src to MPEG-4 ASP AVI with keyframes only at random timestamps.

    Same encode shape as extract_shot (native mpeg4, no B-frames, cfr, AC3) but instead
    of a single leading keyframe, `-force_key_frames` plants one at each randomly-spaced
    timestamp; `-g 999999 -sc_threshold 0` suppress all others. Audio is forced to 48 kHz
    stereo AC3 so its chunks match other moshable AVIs' audio and sections interleave
    into one decodable stream after splicing.

    progress(frac, msg) is called as the encode advances (via ffmpeg -progress).
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
    cmd += [
        *ffmpeg.VIDEO_ENCODE_FLAGS,
        "-force_key_frames",
        ",".join(f"{x:.3f}" for x in times),
        *ffmpeg.AUDIO_ENCODE_FLAGS,
        "-ar",
        "48000",
        "-ac",
        "2",
        "-f",
        "avi",
        dst,
    ]
    ffmpeg.run_encode(cmd, progress=progress, total_seconds=total)
    logger.info(f"moshable: {dst} ({len(times)} random keyframes over {total:.1f}s)")
    return dst


def keyframe_shots(path):
    """Read a file's keyframe timestamps (container-level probe, no decode) and return
    its keyframe sections as a run_mosh shot map [(t0, t1), ...] -- so run_mosh picks
    random-length sections instead of scene cuts."""
    path = paths.resolve(path)
    return bounds_to_shots(ffmpeg.keyframe_times(path) + [ffmpeg.duration(path)])


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


def slice_frames(chunks, f0, f1):
    """Slice an interleaved chunk list to exactly the video frames [f0, f1).

    Half-open and Python-style like list slicing: negative indices resolve
    against the total video frame count, and the result holds exactly f1-f0
    video chunks. Audio rides by interleave position, mirroring split_sections:
    everything from the f0-th video chunk up to (but excluding) the f1-th comes
    along, so boundary audio stays with the frames it was interleaved between.
    An empty or out-of-range range raises ValueError naming the total. The
    returned sub-list is uncopied -- callers that mutate must copy the chunks,
    as MoshScript's materializer does for cached sections.
    """
    v_pos = [i for i, c in enumerate(chunks) if c["stream"] == "v"]
    n = len(v_pos)
    a = f0 + n if f0 < 0 else f0
    b = f1 + n if f1 < 0 else f1
    if not 0 <= a < b <= n:
        raise ValueError(
            f"frame range [{f0}, {f1}) is empty or out of range "
            f"(has {n} video frames)"
        )
    return chunks[v_pos[a] : v_pos[b] if b < n else len(chunks)]


def describe_sections(path):
    """Inventory a moshable AVI: file summary + one entry per keyframe section.

    Byte-level truth for scripting: section indices are exactly the ones
    split_sections defines and MoshScript's `(avi, section)` entries address,
    and audio_chunks counts come straight from the parsed movi list. Start
    times are cumulative frames / fps -- exact, because the moshable encode is
    CFR. A section whose keyframe count != 1 usually means the mpeg4 encoder
    slipped an extra keyframe on a hard cut (MoshScript auto-demotes those).
    """
    path = paths.resolve(path)
    header_prefix, _, chunks = parse_avi(path)
    info = header_info(header_prefix)
    fps = info["fps"]

    sections, frame_cursor = [], 0
    for i, sec in enumerate(split_sections(chunks)):
        frames = sum(1 for c in sec if c["stream"] == "v")
        sections.append(
            {
                "index": i,
                "start": frame_cursor / fps,
                "duration": frames / fps,
                "frames": frames,
                "keyframes": sum(1 for c in sec if c["stream"] == "v" and c["key"]),
                "audio_chunks": sum(1 for c in sec if c["stream"] == "a"),
            }
        )
        frame_cursor += frames

    return {
        "path": path,
        **info,
        "frames": frame_cursor,
        "duration": frame_cursor / fps,
        "sections": sections,
    }


def mosh_pass(cfg, chunks, label="mosh pass", av_ratio=DEFAULT_AUDIO_VIDEO_RATIO):
    """Run mosh_segment over every keyframe section, like run_mosh's render loop:
    the first section keeps its keyframe (valid stream start), intensity escalates
    across the run, and each clip's genuine P-frames seed the next transplant."""
    sections = split_sections(chunks)
    out, prev_pframes = [], None
    n_sections = len(sections)
    for i, sec in enumerate(sections):
        moshed, prev_pframes = mosh_segment(
            cfg,
            sec,
            keep_keyframe=(i == 0),
            donor_pframes=prev_pframes,
            intensity=escalation_intensity(cfg.escalate, i, n_sections),
            av_ratio=av_ratio,
        )
        out.extend(moshed)
    v_in = sum(1 for c in chunks if c["stream"] == "v")
    v_out = sum(1 for c in out if c["stream"] == "v")
    logger.info(f"{label}: {n_sections} sections, {v_in}->{v_out} vframes")
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
                s for s in split_sections(chunks) if any(c["stream"] == "v" for c in s)
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
    logger.info(f"splice: replaced {replaced}/{len(sections)} sections")
    return out


def delete_tagged_keyframes(chunks, frac, tag="spliced"):
    """Delete `frac` of the video keyframes marked with `tag` (remove the chunk outright)
    so the surrounding motion blooms over the spliced pixels instead of them starting
    clean."""
    key_idx = [
        i
        for i, c in enumerate(chunks)
        if c["stream"] == "v" and c["key"] and c.get(tag)
    ]
    kill = set(random.sample(key_idx, round(len(key_idx) * frac)))
    logger.info(f"delete: {len(kill)}/{len(key_idx)} spliced keyframes removed")
    return [c for i, c in enumerate(chunks) if i not in kill]
