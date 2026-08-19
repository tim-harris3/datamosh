"""Source discovery and the keyframe grid's data: which videos are in media/,
their keyframe sections, and the cached per-section thumbnails the drag timeline
shows. Also the source-management click handlers (upload / rescan / generate
keyframes).
"""

import hashlib
import os
import shutil
import subprocess
import urllib.parse

import gradio as gr

from datamosh import ffmpeg, paths, sections

THUMBS_ROOT = str(paths.CACHE_DIR / "thumbs")
GRID_CAP = 400  # most keyframe sections shown in the grid (thumbnails + timeline)
THUMB_W = 160

VIDEO_EXTS = (
    ".mkv",
    ".mp4",
    ".avi",
    ".mov",
    ".m4v",
    ".webm",
    ".ts",
    ".mpg",
    ".mpeg",
    ".wmv",
)


def find_videos():
    """Video files in media/ (drop a new source there and it shows up here)."""
    found = []
    try:
        for f in sorted(os.listdir(paths.MEDIA_DIR)):
            if f.lower().endswith(VIDEO_EXTS):
                found.append(str(paths.MEDIA_DIR / f))
    except OSError:
        pass
    return found


def resolve_sources(sources):
    """Resolved absolute paths of the source control's selections (existing files
    only, deduped, order kept). Tolerates a bare string from a pasted path."""
    if sources is None:
        sources = []
    elif not isinstance(sources, (list, tuple)):
        sources = [sources]
    out = []
    for s in sources:
        p = paths.resolve(str(s or "").strip('"').strip())
        if s and os.path.isfile(p) and p not in out:
            out.append(p)
    return out


def keyframe_info(sources):
    """Keyframe count per selected source, shown under the source dropdown."""
    parts = []
    for src in resolve_sources(sources):
        try:
            count = str(len(ffmpeg.keyframe_times(src)))
        except (subprocess.CalledProcessError, ValueError, OSError):
            count = "?"
        parts.append(f"**{os.path.basename(src)}**: {count} keyframes")
    return " · ".join(parts)


def strip_moshable(stem):
    """A filename stem without the make_moshable() `_moshable` suffix."""
    return stem[: -len("_moshable")] if stem.endswith("_moshable") else stem


def _thumb_dir(src):
    """Per-source thumbnail cache dir; the key changes whenever the file does."""
    st = os.stat(src)
    key = hashlib.md5(f"{src}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()
    return os.path.join(THUMBS_ROOT, key)


def _file_url(p):
    """URL under gradio's file route (allowed via launch(allowed_paths=...))."""
    return "/gradio_api/file=" + urllib.parse.quote(p.replace("\\", "/"), safe="/:")


def cached_spans(src):
    """Keyframe-section spans for src, from the thumbnail cache when possible (a
    fresh keyframe probe on a multi-GB file is slow)."""
    meta = paths.load_json(os.path.join(_thumb_dir(src), "meta.json"))
    if meta and "spans" in meta:
        return [tuple(s) for s in meta["spans"]]
    return [tuple(s) for s in sections.keyframe_shots(src)]


def section_payload(sources):
    """Value for the keyframe grid/timeline component: one entry per selected
    source, each with its keyframe sections (index, time span, thumbnail URL).
    Thumbnails are generated once per source file and cached under
    output/cache/thumbs/. The source `id` (its resolved path) is what timeline
    entries reference, so an arrangement survives adding/removing other sources."""
    out, warns = [], []
    for src in resolve_sources(sources):
        name = os.path.basename(src)
        try:
            out_dir = _thumb_dir(src)
            meta_path = os.path.join(out_dir, "meta.json")
            meta = paths.load_json(meta_path)
            if meta is None:
                spans = sections.keyframe_shots(src)
                ffmpeg.keyframe_thumbnails(src, out_dir, width=THUMB_W, cap=GRID_CAP)
                meta = {
                    "spans": [[round(a, 3), round(b, 3)] for a, b in spans[:GRID_CAP]],
                    "total_sections": len(spans),
                }
                paths.save_json(meta_path, meta, indent=2)
            secs = []
            for i, (a, b) in enumerate(meta["spans"]):
                thumb = os.path.join(out_dir, f"{i + 1:04d}.jpg")
                if os.path.exists(thumb):
                    secs.append({"i": i, "t0": a, "t1": b, "thumb": _file_url(thumb)})
            if meta["total_sections"] > GRID_CAP:
                warns.append(
                    f"{name}: showing first {GRID_CAP} of {meta['total_sections']} sections"
                )
            out.append({"id": src, "name": name, "sections": secs})
        except (subprocess.CalledProcessError, ValueError, OSError) as e:
            warns.append(f"{name}: couldn't read keyframe sections: {e}")
    return {"sources": out, "warning": " · ".join(warns)}


def _original_source(src):
    """The original file behind a *_moshable re-encode: a sibling with the same stem
    minus the suffix. Returns src unchanged for non-moshable files, None if the
    moshable's original is gone."""
    d = os.path.dirname(src)
    stem = os.path.splitext(os.path.basename(src))[0]
    if not stem.endswith("_moshable"):
        return src
    orig_stem = strip_moshable(stem)
    for f in sorted(os.listdir(d or ".")):
        s, ext = os.path.splitext(f)
        if s == orig_stem and ext.lower() in VIDEO_EXTS:
            return os.path.join(d, f)
    return None


def generate_keyframes(sources, gap_lo, gap_hi, progress=gr.Progress()):
    """Re-encode every selected source with make_moshable() (random keyframes) so it
    divides into draggable keyframe sections; results land in media/ and replace the
    selection, which refreshes the grid and timeline. Always reslices the original
    source: if a *_moshable file is selected, its original is re-encoded instead
    (re-slicing a re-encode would stack generation loss)."""
    srcs = resolve_sources(sources)
    if not srcs:
        gr.Warning("pick a source video first")
        return gr.update()
    lo = max(0.05, float(gap_lo or 0.2))
    hi = max(lo, float(gap_hi or 10.0))
    made = []
    for k, src in enumerate(srcs):
        orig = _original_source(src)
        if orig is None:
            gr.Warning(
                f"original for {os.path.basename(src)} not found - "
                "re-slicing the moshable itself"
            )
            orig = src
        src = orig
        stem = strip_moshable(os.path.splitext(os.path.basename(src))[0])
        dst = str(paths.MEDIA_DIR / f"{stem}_moshable.avi")
        progress(
            (k + 0.5) / len(srcs),
            desc=f"re-encoding {os.path.basename(src)} (random keyframes)",
        )
        try:
            sections.make_moshable(src, dst, gap_range=(lo, hi))
        except subprocess.CalledProcessError as e:
            gr.Warning(f"make_moshable failed for {os.path.basename(src)}: {e}")
            continue
        if dst not in made:
            made.append(dst)
    if not made:
        return gr.update()
    return gr.update(choices=find_videos(), value=made)


def upload_videos(files, current):
    """Copy videos picked in the browser's file dialog into media/ and add them to
    the selection (uploads land in a gradio temp dir, so the copy makes them
    permanent and keyframe-cache keys stable). Name clashes get a _1/_2 suffix."""
    added = []
    for f in files or []:
        src = getattr(f, "name", None) or str(f)
        base = os.path.basename(src)
        stem, ext = os.path.splitext(base)
        if ext.lower() not in VIDEO_EXTS:
            gr.Warning(f"skipped {base}: not a recognized video type")
            continue
        dst = paths.MEDIA_DIR / base
        k = 1
        while dst.exists():
            dst = paths.MEDIA_DIR / f"{stem}_{k}{ext}"
            k += 1
        try:
            shutil.copyfile(src, str(dst))
        except OSError as e:
            gr.Warning(f"couldn't copy {base} into media/: {e}")
            continue
        added.append(str(dst))
    keep = resolve_sources(current)
    return gr.update(
        choices=find_videos(), value=keep + [a for a in added if a not in keep]
    )


def rescan_media(current):
    """Re-read media/ into the dropdown (picks up files dropped there outside the
    app); keeps the current selection minus files that no longer exist."""
    return gr.update(choices=find_videos(), value=resolve_sources(current))
