"""Tighten the rendered 8-14 film to an exact target length.

Post-processes output/8-14_narrative.avi WITHOUT re-running the mosh pipeline,
so every section stays present and in its exact current order. Each keyframe
section is tail-trimmed to the same fraction of its length (largest-remainder
rounding lands the total on the exact target frame count), with its audio
trimmed proportionally -- the film keeps its pacing shape but every take is
shorter, so the whole thing cuts faster.

Run (defaults, or override source / destination / length):
    .venv\\Scripts\\python recipes\\tighten_8_14.py [src.avi dst.avi seconds]
"""

import math
import sys

from datamosh import ffmpeg, parse_avi, paths, split_sections, write_avi

SRC = sys.argv[1] if len(sys.argv) > 1 else "output/8-14_narrative.avi"
DST = sys.argv[2] if len(sys.argv) > 2 else "output/8-14_narrative_4min.avi"
TARGET_SECONDS = float(sys.argv[3]) if len(sys.argv) > 3 else 240.0

src = paths.resolve(SRC)
header, movi_start, chunks = parse_avi(src)
fps = ffmpeg.frame_rate(src)
sections = split_sections(chunks)
counts = [sum(1 for c in s if c["stream"] == "v") for s in sections]
total = sum(counts)
target = min(total, round(TARGET_SECONDS * fps))
print(f"{len(sections)} sections, {total} frames ({total / fps:.1f}s) "
      f"-> {target} frames ({target / fps:.1f}s)")

# proportional per-section quotas; largest-remainder rounding hits target exactly
raw = [n * target / total for n in counts]
quotas = [max(1, math.floor(x)) for x in raw]
by_remainder = sorted(range(len(raw)), key=lambda i: raw[i] - math.floor(raw[i]),
                      reverse=True)
i = 0
while sum(quotas) < target:
    j = by_remainder[i % len(by_remainder)]
    if quotas[j] < counts[j]:
        quotas[j] += 1
    i += 1
i = 0
while sum(quotas) > target:
    j = sorted(range(len(quotas)), key=lambda k: quotas[k])[-1 - (i % len(quotas))]
    if quotas[j] > 1:
        quotas[j] -= 1
    i += 1

# keep each section's first quota_v video frames and a matching share of audio
out = []
for sec, qv in zip(sections, quotas):
    nv = sum(1 for c in sec if c["stream"] == "v")
    na = sum(1 for c in sec if c["stream"] == "a")
    qa = round(na * qv / nv) if nv else na
    kv = ka = 0
    for c in sec:
        if c["stream"] == "v":
            if kv < qv:
                out.append(c)
                kv += 1
        elif ka < qa:
            out.append(c)
            ka += 1

dst = paths.resolve(DST)
write_avi(dst, header, movi_start, out)
kept = sum(1 for c in out if c["stream"] == "v")
print(f"wrote {dst} ({kept} frames, {kept / fps:.2f}s)")
ffmpeg.fixup(dst)
