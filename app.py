#!/usr/bin/env python
"""app.py -- browser UI for the datamosh package.

Every slider is auto-generated from MoshConfig's tunable fields (bounds, step, and
label come from the field metadata), so adding a tunable to config.py adds it here
automatically. Renders via run_mosh(), then transcodes to H.264/MP4 so it previews
in the browser (AVI/MPEG-4-ASP won't play in a <video> tag).

Extras:
  - preset dropdown (presets.json + your saved presets),
  - "Save preset" -> writes output/user_presets.json (reloaded on next start),
  - Randomize button, random-seed toggle (fills in the seed it rolled),
  - a rolling gallery of the last few renders for side-by-side comparison,
  - a keyframe-section grid (thumbnail per section, cached in output/cache/thumbs/)
    and a drag timeline: drag sections in to mosh exactly that order via
    run_mosh(sequence=...); empty timeline keeps the classic random behavior,
  - "generate keyframes" -> sections.make_moshable() re-encode with random
    keyframes, saved to media/ and auto-selected.

Run:  .venv\\Scripts\\python app.py    then open http://127.0.0.1:7860
"""

import hashlib
import json
import os
import random
import shutil
import subprocess
import urllib.parse

import gradio as gr

from datamosh import MoshConfig, ffmpeg, paths, presets, run_mosh, sections
from datamosh.config import float_fields, range_fields

OUT_AVI = str(paths.OUTPUT_DIR / "ui_output.avi")
PREVIEW_MP4 = str(paths.OUTPUT_DIR / "ui_preview.mp4")
HIST_DIR = str(paths.OUTPUT_DIR / "ui_history")
HISTORY_JSON = os.path.join(HIST_DIR, "history.json")
THUMBS_ROOT = str(paths.CACHE_DIR / "thumbs")
GRID_CAP = 400   # most keyframe sections shown in the grid (thumbnails + timeline)
THUMB_W = 160
GALLERY_N = 4
PREVIEW_SECS = 60  # cap the in-browser preview length (full-length glitch stays in the .avi)
paths.ensure_output_dirs()
os.makedirs(HIST_DIR, exist_ok=True)

DEFAULTS = MoshConfig()
FLOATS = float_fields()
RANGES = range_fields()


def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save_json(path, obj):
    try:
        with open(path, "w") as f:
            json.dump(obj, f, indent=2)
    except OSError:
        pass


HISTORY = [h for h in _load_json(HISTORY_JSON, []) if os.path.exists(h.get("path", ""))]
_next_run = 1 + max(
    [int(f[4:-4]) for f in os.listdir(HIST_DIR)
     if f.startswith("run_") and f.endswith(".mp4") and f[4:-4].isdigit()],
    default=0,
)

VIDEO_EXTS = (".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".ts", ".mpg", ".mpeg", ".wmv")


def _find_videos():
    """Video files in media/ (drop a new source there and it shows up here)."""
    found = []
    try:
        for f in sorted(os.listdir(paths.MEDIA_DIR)):
            if f.lower().endswith(VIDEO_EXTS):
                found.append(str(paths.MEDIA_DIR / f))
    except OSError:
        pass
    return found


def _keyframe_info(source):
    """Keyframe count of the selected source, shown under the source dropdown."""
    src = paths.resolve(str(source or "").strip('"').strip())
    if not source or not os.path.isfile(src):
        return ""
    try:
        return f"source keyframes: **{len(ffmpeg.keyframe_times(src))}**"
    except (subprocess.CalledProcessError, ValueError, OSError):
        return "source keyframes: ?"


def _resolve_source(source):
    """Resolved absolute path of the source control's value, or None if unusable."""
    src = paths.resolve(str(source or "").strip('"').strip())
    return src if source and os.path.isfile(src) else None


def _thumb_dir(src):
    """Per-source thumbnail cache dir; the key changes whenever the file does."""
    st = os.stat(src)
    key = hashlib.md5(f"{src}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()
    return os.path.join(THUMBS_ROOT, key)


def _file_url(p):
    """URL under gradio's file route (allowed via launch(allowed_paths=...))."""
    return "/gradio_api/file=" + urllib.parse.quote(p.replace("\\", "/"), safe="/:")


def _cached_spans(src):
    """Keyframe-section spans for src, from the thumbnail cache when possible (a
    fresh keyframe probe on a multi-GB file is slow)."""
    meta = _load_json(os.path.join(_thumb_dir(src), "meta.json"), None)
    if meta and "spans" in meta:
        return [tuple(s) for s in meta["spans"]]
    return [tuple(s) for s in sections.keyframe_shots(src)]


def _section_payload(source):
    """Value for the keyframe grid/timeline component: one entry per keyframe
    section (index, time span, thumbnail URL). Thumbnails are generated once per
    source file and cached under output/cache/thumbs/."""
    src = _resolve_source(source)
    if not src:
        return {"sections": [], "warning": ""}
    try:
        out_dir = _thumb_dir(src)
        meta_path = os.path.join(out_dir, "meta.json")
        meta = _load_json(meta_path, None)
        if meta is None:
            spans = sections.keyframe_shots(src)
            ffmpeg.keyframe_thumbnails(src, out_dir, width=THUMB_W, cap=GRID_CAP)
            meta = {"spans": [[round(a, 3), round(b, 3)] for a, b in spans[:GRID_CAP]],
                    "total_sections": len(spans)}
            _save_json(meta_path, meta)
        secs = []
        for i, (a, b) in enumerate(meta["spans"]):
            thumb = os.path.join(out_dir, f"{i + 1:04d}.jpg")
            if os.path.exists(thumb):
                secs.append({"i": i, "t0": a, "t1": b, "thumb": _file_url(thumb)})
        warning = (f"showing first {GRID_CAP} of {meta['total_sections']} sections"
                   if meta["total_sections"] > GRID_CAP else "")
        return {"sections": secs, "warning": warning}
    except (subprocess.CalledProcessError, ValueError, OSError) as e:
        return {"sections": [], "warning": f"couldn't read keyframe sections: {e}"}


def _label(name):
    return name.replace("_", " ")


def _preset_names():
    return list(presets.all_presets())


def _preset_values(name):
    """[n, min_shot, *floats, *range lo/hi pairs] for a preset (defaults fill gaps)."""
    p = presets.all_presets().get(name, {})
    out = [p.get("n", DEFAULTS.n), p.get("min_shot", DEFAULTS.min_shot)]
    out += [p.get(f.name, getattr(DEFAULTS, f.name)) for f in FLOATS]
    for f in RANGES:
        lo, hi = p.get(f.name, getattr(DEFAULTS, f.name))
        out += [lo, hi]
    return out


def _random_values():
    out = []
    for f in FLOATS:
        if f.name == "short_gop_bias":
            out.append(round(random.uniform(-1, 3), 2))
        elif f.name == "escalate":
            out.append(round(random.uniform(0, 3), 2))
        elif f.name.endswith("frac"):
            out.append(round(random.uniform(0, 0.5), 2))
        else:
            out.append(round(random.uniform(0, 1), 2))
    for f in RANGES:
        lo_b, hi_b = int(f.metadata["lo"]), int(f.metadata["hi"])
        a = random.randint(lo_b, max(lo_b, hi_b // 3))
        b = random.randint(a, min(hi_b, a + hi_b // 2 + 2))
        out += [a, b]
    return out


def save_preset(name, n, min_shot, *values):
    """Persist the current control values as a named preset in output/user_presets.json."""
    if not name or not name.strip():
        return gr.update()
    name = name.strip()
    p = {"n": int(n), "min_shot": float(min_shot)}
    i = 0
    for f in FLOATS:
        p[f.name] = float(values[i]); i += 1
    for f in RANGES:
        p[f.name] = [int(values[i]), int(values[i + 1])]; i += 2
    presets.save_user_preset(name, p)
    return gr.update(choices=_preset_names(), value=name)


def _gallery_updates():
    ups = []
    for i in range(GALLERY_N):
        if i < len(HISTORY):
            ups.append(gr.update(value=HISTORY[i]["path"], label=HISTORY[i]["caption"]))
        else:
            ups.append(gr.update(value=None, label=f"slot {i + 1}"))
    return ups


def _record_render(seed, n, seq_len=0):
    global _next_run
    rid, _next_run = _next_run, _next_run + 1
    dst = os.path.join(HIST_DIR, f"run_{rid:04d}.mp4")
    shutil.copyfile(PREVIEW_MP4, dst)
    tail = f"seq×{seq_len}" if seq_len else f"n{int(n)}"
    HISTORY.insert(0, {"path": dst, "caption": f"#{rid} · seed {seed} · {tail}"})
    while len(HISTORY) > GALLERY_N:
        old = HISTORY.pop()
        try:
            os.remove(old["path"])
        except OSError:
            pass
    _save_json(HISTORY_JSON, HISTORY)


def render(source, n, seed, min_shot, rand_seed, sequence_idx, *values,
           progress=gr.Progress()):
    used_seed = random.randrange(1, 2**31 - 1) if rand_seed else int(seed)

    kw = {"source": str(source).strip('"').strip(), "output": OUT_AVI,
          "n": int(n), "seed": used_seed, "min_shot": float(min_shot),
          "reset": True, "fixup": False}
    i = 0
    for f in FLOATS:
        kw[f.name] = float(values[i]); i += 1
    for f in RANGES:
        lo, hi = int(values[i]), int(values[i + 1]); i += 2
        kw[f.name] = (min(lo, hi), max(lo, hi))
    cfg = MoshConfig(**kw)

    # a non-empty timeline overrides random shot picking: mosh exactly those
    # keyframe sections, in the dragged order
    seq = None
    if sequence_idx:
        spans = _cached_spans(paths.resolve(kw["source"]))
        seq = [spans[i] for i in sequence_idx if 0 <= i < len(spans)] or None

    def cb(frac, msg):
        progress(frac, desc=msg)

    run_mosh(cfg, sequence=seq, progress=cb)
    progress(0.99, desc="encoding preview")
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", OUT_AVI,
         "-t", str(PREVIEW_SECS), "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", PREVIEW_MP4],
        check=True,
    )
    _record_render(used_seed, n, seq_len=len(seq) if seq else 0)
    return PREVIEW_MP4, OUT_AVI, used_seed, *_gallery_updates()


def generate_keyframes(source, gap_lo, gap_hi, progress=gr.Progress()):
    """Re-encode the selected source with make_moshable() (random keyframes) so it
    divides into draggable keyframe sections; the result lands in media/ and is
    auto-selected, which refreshes the grid and timeline."""
    src = _resolve_source(source)
    if not src:
        gr.Warning("pick a source video first")
        return gr.update()
    stem = os.path.splitext(os.path.basename(src))[0]
    if stem.endswith("_moshable"):
        stem = stem[: -len("_moshable")]
    dst = str(paths.MEDIA_DIR / f"{stem}_moshable.avi")
    lo = max(0.05, float(gap_lo or 0.2))
    hi = max(lo, float(gap_hi or 10.0))
    progress(0.1, desc="re-encoding moshable AVI (random keyframes)")
    try:
        sections.make_moshable(src, dst, gap_range=(lo, hi))
    except subprocess.CalledProcessError as e:
        gr.Warning(f"make_moshable failed: {e}")
        return gr.update()
    return gr.update(choices=_find_videos(), value=dst)


# --- keyframe grid + drag timeline component (one gr.HTML owns both regions) ---
# The grid previews every keyframe section of the selected source; sections are
# dragged into the timeline strip to define the exact mosh order (duplicates
# allowed -- dragging from the grid copies). All rendering happens in JS from the
# component value; every timeline mutation fires trigger('seq', {order}) which
# lands in Python as evt.order and is kept in a gr.State for the Mosh click.

TIMELINE_HTML = """
<div class="dm-root">
  <div class="dm-warn"></div>
  <div class="dm-grid"></div>
  <div class="dm-tl-head">
    <span>timeline &mdash; drag sections in (drag again to reorder; shift/ctrl-click
    selects groups; double-click removes; empty timeline = random mosh)</span>
    <button class="dm-clear">clear</button>
  </div>
  <div class="dm-timeline"></div>
</div>
"""

TIMELINE_CSS = """
.dm-root { display: flex; flex-direction: column; gap: 8px; }
.dm-warn { color: var(--color-accent, #f97316); font-size: 12px; }
.dm-warn:empty { display: none; }
.dm-grid { display: flex; flex-wrap: wrap; gap: 6px; max-height: 320px;
           overflow-y: auto; padding: 2px; }
.dm-cell, .dm-item { position: relative; border: 2px solid transparent;
                     border-radius: 6px; cursor: grab; flex: 0 0 auto; }
.dm-cell img, .dm-item img { display: block; width: 110px; border-radius: 4px;
                             pointer-events: none; }
.dm-cell.sel, .dm-item.sel { border-color: var(--color-accent, #f97316); }
.dm-cap { position: absolute; left: 0; right: 0; bottom: 0; font-size: 10px;
          line-height: 1.3; padding: 1px 4px; color: #fff;
          background: rgba(0,0,0,0.55); border-radius: 0 0 4px 4px;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dm-tl-head { display: flex; justify-content: space-between; align-items: center;
              gap: 8px; font-size: 12px; opacity: 0.85; }
.dm-clear { cursor: pointer; font-size: 12px; padding: 2px 10px;
            border: 1px solid var(--border-color-primary, #666);
            border-radius: 4px; background: transparent; color: inherit; }
.dm-timeline { display: flex; gap: 4px; align-items: center; min-height: 104px;
               overflow-x: auto; padding: 6px; border-radius: 6px;
               border: 1px dashed var(--border-color-primary, #666); }
.dm-item img { width: 96px; }
.dm-item.gapmark { box-shadow: -4px 0 0 0 var(--color-accent, #f97316); }
.dm-timeline.gap-end { box-shadow: inset -4px 0 0 0 var(--color-accent, #f97316); }
.dm-empty { opacity: 0.55; font-size: 13px; margin: auto; padding: 8px; }
"""

TIMELINE_JS = """
var root = element;
var order = [];      // timeline: section indices in mosh order (duplicates ok)
var gridSel = [];    // selected grid section indices
var tlSel = [];      // selected timeline positions
var gridAnchor = 0, tlAnchor = 0;
var drag = null;     // {from:'grid', secIdx:[...]} | {from:'tl', positions:[...]}

function secsList() { return (props.value && props.value.sections) || []; }
function fmtT(t) { return (Math.round(t * 100) / 100) + 's'; }
function el(tag, cls) { var d = document.createElement(tag); if (cls) d.className = cls; return d; }

function renderWarn() {
  root.querySelector('.dm-warn').textContent = (props.value && props.value.warning) || '';
}

function renderGrid() {
  var grid = root.querySelector('.dm-grid');
  grid.innerHTML = '';
  var list = secsList();
  if (!list.length) {
    var hint = el('div', 'dm-empty');
    hint.textContent = 'no keyframe sections - pick a source video (or click generate keyframes)';
    grid.appendChild(hint);
    return;
  }
  list.forEach(function (s, i) {
    var cell = el('div', 'dm-cell' + (gridSel.indexOf(i) >= 0 ? ' sel' : ''));
    cell.draggable = true;
    cell.dataset.i = i;
    var img = el('img'); img.src = s.thumb; img.draggable = false;
    var cap = el('div', 'dm-cap');
    cap.textContent = '#' + (i + 1) + ' ' + fmtT(s.t0) + '-' + fmtT(s.t1);
    cell.title = 'section ' + (i + 1) + ': ' + fmtT(s.t0) + ' - ' + fmtT(s.t1) + ' (drag onto the timeline)';
    cell.appendChild(img); cell.appendChild(cap);
    grid.appendChild(cell);
  });
}

function renderTimeline() {
  var tl = root.querySelector('.dm-timeline');
  tl.innerHTML = '';
  tl.classList.remove('gap-end');
  var list = secsList();
  if (!order.length) {
    var hint = el('div', 'dm-empty');
    hint.textContent = 'empty - drag keyframe sections here to set the mosh order';
    tl.appendChild(hint);
    return;
  }
  order.forEach(function (si, pos) {
    var s = list[si];
    var item = el('div', 'dm-item' + (tlSel.indexOf(pos) >= 0 ? ' sel' : ''));
    item.draggable = true;
    item.dataset.pos = pos;
    if (s) { var img = el('img'); img.src = s.thumb; img.draggable = false; item.appendChild(img); }
    var cap = el('div', 'dm-cap');
    cap.textContent = (pos + 1) + ': #' + (si + 1);
    item.appendChild(cap);
    tl.appendChild(item);
  });
}

function sync() { renderTimeline(); trigger('seq', { order: order.slice() }); }

function applySel(sel, anchor, idx, ev) {
  if (ev.shiftKey) {
    var a = Math.min(anchor.v, idx), b = Math.max(anchor.v, idx), out = [];
    for (var k = a; k <= b; k++) out.push(k);
    return out;
  }
  anchor.v = idx;
  if (ev.ctrlKey || ev.metaKey) {
    var copy = sel.slice(), p = copy.indexOf(idx);
    if (p >= 0) copy.splice(p, 1); else copy.push(idx);
    return copy;
  }
  return (sel.length === 1 && sel[0] === idx) ? [] : [idx];
}

root.addEventListener('click', function (e) {
  if (e.target.closest('.dm-clear')) { order = []; tlSel = []; sync(); return; }
  var cell = e.target.closest('.dm-cell');
  if (cell) {
    var a = { v: gridAnchor };
    gridSel = applySel(gridSel, a, +cell.dataset.i, e);
    gridAnchor = a.v;
    renderGrid();
    return;
  }
  var item = e.target.closest('.dm-item');
  if (item) {
    var b = { v: tlAnchor };
    tlSel = applySel(tlSel, b, +item.dataset.pos, e);
    tlAnchor = b.v;
    renderTimeline();
  }
});

root.addEventListener('dblclick', function (e) {
  var item = e.target.closest('.dm-item');
  if (!item) return;
  order.splice(+item.dataset.pos, 1);
  tlSel = [];
  sync();
});

root.addEventListener('dragstart', function (e) {
  var cell = e.target.closest('.dm-cell');
  var item = e.target.closest('.dm-item');
  if (cell) {
    var i = +cell.dataset.i;
    var sel = gridSel.indexOf(i) >= 0 ? gridSel.slice().sort(function (x, y) { return x - y; }) : [i];
    drag = { from: 'grid', secIdx: sel };
  } else if (item) {
    var pos = +item.dataset.pos;
    var psel = tlSel.indexOf(pos) >= 0 ? tlSel.slice().sort(function (x, y) { return x - y; }) : [pos];
    drag = { from: 'tl', positions: psel };
  } else { return; }
  e.dataTransfer.setData('text/plain', 'dm');
  e.dataTransfer.effectAllowed = 'copyMove';
});

function gapAt(e) {
  var items = root.querySelectorAll('.dm-timeline .dm-item');
  for (var k = 0; k < items.length; k++) {
    var r = items[k].getBoundingClientRect();
    if (e.clientX < r.left + r.width / 2) return k;
  }
  return order.length;
}

function markGap(g) {
  var tl = root.querySelector('.dm-timeline');
  tl.classList.toggle('gap-end', g >= order.length && order.length > 0);
  root.querySelectorAll('.dm-timeline .dm-item').forEach(function (it, k) {
    it.classList.toggle('gapmark', k === g);
  });
}

root.addEventListener('dragover', function (e) {
  if (!drag) return;
  if (!e.target.closest('.dm-timeline')) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = drag.from === 'grid' ? 'copy' : 'move';
  markGap(gapAt(e));
});

root.addEventListener('dragleave', function (e) {
  var tl = e.target.closest('.dm-timeline');
  if (tl && !(e.relatedTarget && tl.contains(e.relatedTarget))) markGap(-1);
});

root.addEventListener('drop', function (e) {
  var tl = e.target.closest('.dm-timeline');
  if (!tl || !drag) return;
  e.preventDefault();
  var g = gapAt(e);
  if (drag.from === 'grid') {
    Array.prototype.splice.apply(order, [g, 0].concat(drag.secIdx));
  } else {
    var moving = drag.positions.map(function (p) { return order[p]; });
    var adj = g;
    drag.positions.slice().reverse().forEach(function (p) {
      order.splice(p, 1);
      if (p < adj) adj--;
    });
    Array.prototype.splice.apply(order, [adj, 0].concat(moving));
  }
  tlSel = [];
  drag = null;
  sync();
});

root.addEventListener('dragend', function () { drag = null; markGap(-1); });

watch('value', function () {
  order = []; gridSel = []; tlSel = []; drag = null;
  renderWarn(); renderGrid(); renderTimeline();
});

renderWarn(); renderGrid(); renderTimeline();
"""


with gr.Blocks(title="datamosh") as demo:
    gr.Markdown("# datamosh\n**1** pick a source · **2** arrange keyframe sections "
                "(or leave the timeline empty for random) · **3** pick a preset / "
                "Randomize and tweak · **4** Mosh. Preview plays in-browser; the raw "
                "glitch `.avi` is downloadable.")
    float_ctrls, range_ctrls = {}, {}

    def _float_slider(f):
        m = f.metadata
        float_ctrls[f.name] = gr.Slider(
            m["lo"], m["hi"], value=getattr(DEFAULTS, f.name), step=m["step"],
            label=_label(f.name), info=m["help"],
        )

    gr.Markdown("## 1 · source")
    with gr.Row():
        with gr.Column(scale=3):
            default_src = paths.resolve(DEFAULTS.source)
            source = gr.Dropdown(
                choices=_find_videos(),
                value=default_src if os.path.exists(default_src) else None,
                label="source video", allow_custom_value=True,
                info="pick a file from media/ or paste any path; first use of a new source scene-detects once (cached)",
            )
            kf_info = gr.Markdown(_keyframe_info(source.value))
        with gr.Column(scale=1):
            gen_btn = gr.Button("⚡ generate keyframes")
            with gr.Row():
                gap_lo = gr.Number(value=0.2, minimum=0.05, label="gap lo (s)", scale=1)
                gap_hi = gr.Number(value=10.0, minimum=0.1, label="gap hi (s)", scale=1)

    with gr.Accordion("2 · sequence — drag keyframe sections into the timeline (empty = random)", open=True):
        timeline = gr.HTML(
            value=_section_payload(source.value),
            html_template=TIMELINE_HTML,
            css_template=TIMELINE_CSS,
            js_on_load=TIMELINE_JS,
            container=True,
            padding=True,
        )
    seq_state = gr.State([])

    gr.Markdown("## 3 · tune")
    with gr.Row():
        preset = gr.Dropdown(choices=_preset_names(), label="preset", value=None, scale=2)
        randomize = gr.Button("🎲 Randomize", scale=1)
        preset_name = gr.Textbox(label="save current as", placeholder="preset name", scale=2)
        save_btn = gr.Button("💾 Save preset", scale=1)
    with gr.Row():
        n = gr.Slider(1, 60, value=DEFAULTS.n, step=1, label="clips (n)", scale=2)
        min_shot = gr.Slider(0.05, 5, value=DEFAULTS.min_shot, step=0.05,
                             label="min shot length (s)", scale=2)
        seed = gr.Number(value=5, precision=0, label="seed", scale=1)
        rand_seed = gr.Checkbox(value=False, label="random seed", scale=1)
    with gr.Row():
        with gr.Column():
            with gr.Accordion("video mangles", open=True):
                for f in FLOATS:
                    if f.metadata["group"] == "video":
                        _float_slider(f)
        with gr.Column():
            with gr.Accordion("audio mangles", open=False):
                for f in FLOATS:
                    if f.metadata["group"] == "audio":
                        _float_slider(f)
            with gr.Accordion("ranges (lo / hi)", open=False):
                for f in RANGES:
                    lo_b, hi_b = f.metadata["lo"], f.metadata["hi"]
                    cur = getattr(DEFAULTS, f.name)
                    with gr.Row():
                        a = gr.Slider(lo_b, hi_b, value=cur[0], step=1, label=f"{_label(f.name)} lo")
                        b = gr.Slider(lo_b, hi_b, value=cur[1], step=1, label=f"{_label(f.name)} hi")
                    range_ctrls[f.name] = (a, b)

    gr.Markdown("## 4 · mosh")
    go = gr.Button("Mosh", variant="primary", size="lg")
    with gr.Row():
        preview = gr.Video(label=f"preview (first {PREVIEW_SECS}s)", autoplay=True, scale=3)
        download = gr.File(label="raw .avi", scale=1)
    with gr.Accordion("recent renders", open=True):
        with gr.Row():
            gallery_videos = []
            for i in range(GALLERY_N):
                h = HISTORY[i] if i < len(HISTORY) else None
                gallery_videos.append(gr.Video(
                    value=h["path"] if h else None,
                    label=h["caption"] if h else f"slot {i + 1}",
                    interactive=False,
                ))

    ordered_floats = [float_ctrls[f.name] for f in FLOATS]
    ordered_ranges = [s for f in RANGES for s in range_ctrls[f.name]]
    tunable_ctrls = ordered_floats + ordered_ranges

    def _on_source_change(source):
        return _keyframe_info(source), _section_payload(source), []

    def _timeline_seq(evt: gr.EventData):
        try:
            return [int(i) for i in evt.order]
        except (AttributeError, TypeError, ValueError):
            return []

    source.change(_on_source_change, inputs=[source],
                  outputs=[kf_info, timeline, seq_state])
    timeline.seq(_timeline_seq, inputs=None, outputs=[seq_state])
    gen_btn.click(generate_keyframes, inputs=[source, gap_lo, gap_hi], outputs=[source])
    preset.change(_preset_values, inputs=[preset], outputs=[n, min_shot, *tunable_ctrls])
    randomize.click(_random_values, inputs=None, outputs=tunable_ctrls)
    save_btn.click(save_preset, inputs=[preset_name, n, min_shot, *tunable_ctrls], outputs=[preset])
    go.click(render, inputs=[source, n, seed, min_shot, rand_seed, seq_state, *tunable_ctrls],
             outputs=[preview, download, seed, *gallery_videos])


if __name__ == "__main__":
    ffmpeg.require_ffmpeg()
    demo.launch(allowed_paths=[THUMBS_ROOT])
