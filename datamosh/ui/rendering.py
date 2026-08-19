"""The Mosh click: build the config, run the mosh, chain post-effects, encode the
browser preview, and keep the rolling render history.

Renders are serialized by RENDER_LOCK (run_mosh uses a shared .mosh_tmp.avi scratch
file, so two at once would corrupt each other) and every run gets its own
output/ui_run_NNNN* filenames, so a queued second click can never clobber the files
the first one is still writing. Older runs' files are cleaned up after each
success; the history copies in output/ui_history/ are what the gallery replays.
"""

import glob
import os
import random
import re
import shutil
import subprocess
import threading
from typing import NamedTuple

import gradio as gr

from datamosh import chroma_databend, ffmpeg, paths, pixel_sort, run_mosh

from . import media, values

HIST_DIR = str(paths.OUTPUT_DIR / "ui_history")
HISTORY_JSON = os.path.join(HIST_DIR, "history.json")
GALLERY_N = 4
PREVIEW_SECS = (
    60  # cap the in-browser preview length (full-length glitch stays in the .avi)
)
# post-effect seeds derive from the render seed (offset so each stage rolls its own
# stream), keeping one seed reproducing the whole run
CHROMA_SEED_OFFSET = 1
SORT_SEED_OFFSET = 2

paths.ensure_output_dirs()
os.makedirs(HIST_DIR, exist_ok=True)

RENDER_LOCK = threading.Lock()  # one render at a time; extra clicks queue

HISTORY = [
    h
    for h in (paths.load_json(HISTORY_JSON, []))
    if os.path.exists(h.get("path", ""))
]

_RUN_FILE_RE = re.compile(r"(?:^run_|^ui_run_)(\d+)")


def _max_run_id(directory):
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    ids = [int(m.group(1)) for m in map(_RUN_FILE_RE.match, names) if m]
    return max(ids, default=0)


_run_counter = max(_max_run_id(HIST_DIR), _max_run_id(paths.OUTPUT_DIR))
_run_counter_lock = threading.Lock()


def _next_run_id():
    global _run_counter
    with _run_counter_lock:
        _run_counter += 1
        return _run_counter


class RunPaths(NamedTuple):
    """Per-run output filenames, so concurrent/queued renders never share files."""

    rid: int
    mosh_avi: str
    chroma_avi: str
    sort_avi: str
    preview_mp4: str


def _run_paths(rid):
    stem = str(paths.OUTPUT_DIR / f"ui_run_{rid:04d}")
    return RunPaths(
        rid=rid,
        mosh_avi=f"{stem}.avi",
        chroma_avi=f"{stem}_chroma.avi",
        sort_avi=f"{stem}_sorted.avi",
        preview_mp4=f"{stem}_preview.mp4",
    )


def _cleanup_old_runs(keep_rid):
    """Best-effort removal of previous runs' output files (the gallery replays the
    history copies, so only the newest run's raw files need to stay downloadable)."""
    for f in glob.glob(str(paths.OUTPUT_DIR / "ui_run_*")):
        m = _RUN_FILE_RE.match(os.path.basename(f))
        if m and int(m.group(1)) != keep_rid:
            try:
                os.remove(f)
            except OSError:
                pass


def gallery_updates():
    updates = []
    for i in range(GALLERY_N):
        if i < len(HISTORY):
            updates.append(
                gr.update(value=HISTORY[i]["path"], label=HISTORY[i]["caption"])
            )
        else:
            updates.append(gr.update(value=None, label=f"slot {i + 1}"))
    return updates


def record_render(rid, preview_mp4, seed, n, seq_len=0):
    dst = os.path.join(HIST_DIR, f"run_{rid:04d}.mp4")
    shutil.copyfile(preview_mp4, dst)
    tail = f"seq×{seq_len}" if seq_len else f"n{int(n)}"
    HISTORY.insert(0, {"path": dst, "caption": f"#{rid} · seed {seed} · {tail}"})
    while len(HISTORY) > GALLERY_N:
        old = HISTORY.pop()
        try:
            os.remove(old["path"])
        except OSError:
            pass
    paths.save_json(HISTORY_JSON, HISTORY, indent=2)


class ChromaOpts(NamedTuple):
    enable: bool
    mode: str
    planes: str
    frac: float


class SortOpts(NamedTuple):
    enable: bool
    mode: str
    key: str
    direction: str
    frac: float
    lo: int
    hi: int
    reverse: bool


def _apply_post_effects(run_paths, seed, chroma_opts, sort_opts, progress):
    """Chain the enabled post-effects onto the moshed output; returns the final AVI.

    Order is fixed chroma -> pixel sort (the docs' chaining order)."""
    cur = run_paths.mosh_avi
    stage = "chroma post-effect"
    try:
        if chroma_opts.enable:
            progress(0.90, desc="chroma post-effect (full decode)")
            chroma_databend(
                cur,
                run_paths.chroma_avi,
                mode=chroma_opts.mode,
                planes=chroma_opts.planes,
                frac=float(chroma_opts.frac),
                seed=seed + CHROMA_SEED_OFFSET,
            )
            cur = run_paths.chroma_avi
        stage = "pixel sort post-effect"
        if sort_opts.enable:
            progress(0.94, desc="pixel sort post-effect")
            pixel_sort(
                cur,
                run_paths.sort_avi,
                mode=sort_opts.mode,
                key=sort_opts.key,
                direction=sort_opts.direction,
                frac=float(sort_opts.frac),
                lo=int(sort_opts.lo),
                hi=int(sort_opts.hi),
                reverse=bool(sort_opts.reverse),
                seed=seed + SORT_SEED_OFFSET,
            )
            cur = run_paths.sort_avi
    except (subprocess.CalledProcessError, ValueError, OSError) as e:
        raise gr.Error(f"{stage} failed: {e}")
    return cur


def _build_sequence(sequence_sel, srcs):
    """The timeline's [(source, section idx), ...] entries as a run_mosh sequence
    [(source, t0, t1), ...], or None when the timeline is empty (= random mode)."""
    if not sequence_sel:
        return None
    spans_by_src, seq = {}, []
    for s, idx in sequence_sel:
        s = paths.resolve(str(s))
        if s not in srcs:
            continue
        spans = spans_by_src.setdefault(s, media.cached_spans(s))
        if 0 <= idx < len(spans):
            seq.append((s, *spans[idx]))
    return seq or None


def render(
    sources,
    n,
    seed,
    min_shot,
    rand_seed,
    sequence_sel,
    ch_enable,
    ch_mode,
    ch_planes,
    ch_frac,
    ps_enable,
    ps_mode,
    ps_key,
    ps_dir,
    ps_frac,
    ps_lo,
    ps_hi,
    ps_rev,
    *tunable_values,
    progress=gr.Progress(),
):
    srcs = media.resolve_sources(sources)
    if not srcs:
        raise gr.Error("pick at least one source video")
    used_seed = random.randrange(1, 2**31 - 1) if rand_seed else int(seed)

    with RENDER_LOCK:
        run_paths = _run_paths(_next_run_id())
        cfg = values.render_config(
            srcs[0], run_paths.mosh_avi, n, used_seed, min_shot, tunable_values
        )

        # a non-empty timeline overrides random shot picking: mosh exactly those
        # keyframe sections, in the dragged order; entries are (source, section idx)
        # pairs so sections from different selected sources can interleave
        seq = _build_sequence(sequence_sel, srcs)
        if seq is None and len(srcs) > 1:
            gr.Info(
                "timeline is empty: random mode moshes the first selected source only "
                "- drag sections into the timeline to mix sources"
            )

        run_mosh(cfg, sequence=seq, progress=lambda frac, msg: progress(frac, desc=msg))
        final = _apply_post_effects(
            run_paths,
            used_seed,
            ChromaOpts(ch_enable, ch_mode, ch_planes, ch_frac),
            SortOpts(ps_enable, ps_mode, ps_key, ps_dir, ps_frac, ps_lo, ps_hi, ps_rev),
            progress,
        )
        progress(0.99, desc="encoding preview")
        ffmpeg.transcode(final, run_paths.preview_mp4, seconds=PREVIEW_SECS)
        record_render(
            run_paths.rid, run_paths.preview_mp4, used_seed, n, seq_len=len(seq) if seq else 0
        )
        _cleanup_old_runs(run_paths.rid)
    return run_paths.preview_mp4, final, used_seed, *gallery_updates()
