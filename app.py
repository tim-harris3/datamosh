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
  - a rolling gallery of the last few renders for side-by-side comparison.

Run:  .venv\\Scripts\\python app.py    then open http://127.0.0.1:7860
"""

import json
import os
import random
import shutil
import subprocess

import gradio as gr

from datamosh import MoshConfig, ffmpeg, paths, presets, run_mosh
from datamosh.config import float_fields, range_fields

OUT_AVI = str(paths.OUTPUT_DIR / "ui_output.avi")
PREVIEW_MP4 = str(paths.OUTPUT_DIR / "ui_preview.mp4")
HIST_DIR = str(paths.OUTPUT_DIR / "ui_history")
HISTORY_JSON = os.path.join(HIST_DIR, "history.json")
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


def _record_render(seed, n):
    global _next_run
    rid, _next_run = _next_run, _next_run + 1
    dst = os.path.join(HIST_DIR, f"run_{rid:04d}.mp4")
    shutil.copyfile(PREVIEW_MP4, dst)
    HISTORY.insert(0, {"path": dst, "caption": f"#{rid} · seed {seed} · n{int(n)}"})
    while len(HISTORY) > GALLERY_N:
        old = HISTORY.pop()
        try:
            os.remove(old["path"])
        except OSError:
            pass
    _save_json(HISTORY_JSON, HISTORY)


def render(source, n, seed, min_shot, rand_seed, *values, progress=gr.Progress()):
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

    def cb(frac, msg):
        progress(frac, desc=msg)

    run_mosh(cfg, progress=cb)
    progress(0.99, desc="encoding preview")
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", OUT_AVI,
         "-t", str(PREVIEW_SECS), "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", PREVIEW_MP4],
        check=True,
    )
    _record_render(used_seed, n)
    return PREVIEW_MP4, OUT_AVI, used_seed, *_gallery_updates()


with gr.Blocks(title="datamosh") as demo:
    gr.Markdown("# datamosh\nPick a **preset** or **Randomize**, tweak, then **Mosh**. "
                "Preview plays in-browser; the raw glitch `.avi` is downloadable.")
    float_ctrls, range_ctrls = {}, {}

    def _float_slider(f):
        m = f.metadata
        float_ctrls[f.name] = gr.Slider(
            m["lo"], m["hi"], value=getattr(DEFAULTS, f.name), step=m["step"],
            label=_label(f.name), info=m["help"],
        )

    with gr.Row():
        with gr.Column(scale=1):
            default_src = paths.resolve(DEFAULTS.source)
            source = gr.Dropdown(
                choices=_find_videos(),
                value=default_src if os.path.exists(default_src) else None,
                label="source video", allow_custom_value=True,
                info="pick a file from media/ or paste any path; first use of a new source scene-detects once (cached)",
            )
            kf_info = gr.Markdown(_keyframe_info(source.value))
            with gr.Row():
                preset = gr.Dropdown(choices=_preset_names(), label="preset", value=None, scale=2)
                randomize = gr.Button("🎲 Randomize", scale=1)
            with gr.Row():
                preset_name = gr.Textbox(label="save current as", placeholder="preset name", scale=2)
                save_btn = gr.Button("💾 Save preset", scale=1)
            n = gr.Slider(1, 60, value=DEFAULTS.n, step=1, label="clips (n)")
            with gr.Row():
                seed = gr.Number(value=5, precision=0, label="seed", scale=2)
                rand_seed = gr.Checkbox(value=False, label="random seed", scale=1)
            min_shot = gr.Slider(0.05, 5, value=DEFAULTS.min_shot, step=0.05, label="min shot length (s)")

            with gr.Accordion("video mangles", open=True):
                for f in FLOATS:
                    if f.metadata["group"] == "video":
                        _float_slider(f)
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
            go = gr.Button("Mosh", variant="primary")
        with gr.Column(scale=2):
            preview = gr.Video(label=f"preview (first {PREVIEW_SECS}s)", autoplay=True)
            download = gr.File(label="raw .avi")

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

    source.change(_keyframe_info, inputs=[source], outputs=[kf_info])
    preset.change(_preset_values, inputs=[preset], outputs=[n, min_shot, *tunable_ctrls])
    randomize.click(_random_values, inputs=None, outputs=tunable_ctrls)
    save_btn.click(save_preset, inputs=[preset_name, n, min_shot, *tunable_ctrls], outputs=[preset])
    go.click(render, inputs=[source, n, seed, min_shot, rand_seed, *tunable_ctrls],
             outputs=[preview, download, seed, *gallery_videos])


if __name__ == "__main__":
    ffmpeg.require_ffmpeg()
    demo.launch()
