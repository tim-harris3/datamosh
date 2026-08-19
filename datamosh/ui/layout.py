"""The gr.Blocks tree and event wiring.

Every slider is auto-generated from MoshConfig's tunable fields (bounds, step, and
label come from the field metadata), so adding a tunable to config.py adds it here
automatically. The keyframe grid / drag timeline frontend lives in static/ (one
gr.HTML component owns both regions; see static/timeline.js for the drag model:
every timeline mutation fires trigger('seq', {order}), which lands in Python as
evt.order and is kept in a gr.State for the Mosh click).
"""

import os
from pathlib import Path
from typing import NamedTuple

import gradio as gr

from datamosh import CHROMA_MODES, PIXELSORT_KEYS, PIXELSORT_MODES, paths

from . import media, rendering, values
from .values import DEFAULTS, FLOATS, RANGES

_STATIC = Path(__file__).parent / "static"


def _asset(name):
    return (_STATIC / name).read_text(encoding="utf-8")


class SourceSection(NamedTuple):
    source: object
    kf_info: object
    upload_btn: object
    rescan_btn: object
    gen_btn: object
    gap_lo: object
    gap_hi: object


def _source_section():
    gr.Markdown("## 1 · source")
    with gr.Row():
        with gr.Column(scale=3):
            default_src = (
                paths.resolve(DEFAULTS.source) if DEFAULTS.source is not None else None
            )
            source = gr.Dropdown(
                choices=media.find_videos(),
                value=(
                    [default_src]
                    if default_src is not None and os.path.exists(default_src)
                    else []
                ),
                label="source videos",
                multiselect=True,
                allow_custom_value=True,
                info="pick one or more from media/ (or paste any path) — every selection's "
                "sections join the grid below; first use of a new source "
                "scene-detects once (cached)",
            )
            kf_info = gr.Markdown(media.keyframe_info(source.value))
            with gr.Row():
                upload_btn = gr.UploadButton(
                    "📤 add video files…",
                    file_count="multiple",
                    file_types=list(media.VIDEO_EXTS),
                )
                rescan_btn = gr.Button("🔄 rescan media/")
        with gr.Column(scale=1):
            gen_btn = gr.Button("⚡ generate keyframes")
            with gr.Row():
                gap_lo = gr.Number(value=0.2, minimum=0.05, label="gap lo (s)", scale=1)
                gap_hi = gr.Number(value=10.0, minimum=0.1, label="gap hi (s)", scale=1)
    return SourceSection(source, kf_info, upload_btn, rescan_btn, gen_btn, gap_lo, gap_hi)


def _timeline_section(source):
    with gr.Accordion(
        "2 · sequence — drag keyframe sections from any source into the timeline (empty = random)",
        open=True,
    ):
        timeline = gr.HTML(
            value=media.section_payload(source.value),
            html_template=_asset("timeline.html"),
            css_template=_asset("timeline.css"),
            js_on_load=_asset("timeline.js"),
            container=True,
            padding=True,
        )
    seq_state = gr.State([])
    return timeline, seq_state


class TuneSection(NamedTuple):
    preset: object
    randomize: object
    preset_name: object
    save_btn: object
    n: object
    min_shot: object
    seed: object
    rand_seed: object
    post_ctrls: list
    tunable_ctrls: list


def _tuning_section():
    gr.Markdown("## 3 · tune")
    with gr.Row():
        preset = gr.Dropdown(
            choices=values.preset_names(), label="preset", value=None, scale=2
        )
        randomize = gr.Button("🎲 Randomize", scale=1)
        preset_name = gr.Textbox(
            label="save current as", placeholder="preset name", scale=2
        )
        save_btn = gr.Button("💾 Save preset", scale=1)
    with gr.Row():
        n = gr.Slider(1, 60, value=DEFAULTS.n, step=1, label="clips (n)", scale=2)
        min_shot = gr.Slider(
            0.05,
            5,
            value=DEFAULTS.min_shot,
            step=0.05,
            label="min shot length (s)",
            scale=2,
        )
        seed = gr.Number(value=5, precision=0, label="seed", scale=1)
        rand_seed = gr.Checkbox(value=False, label="random seed", scale=1)
    post_ctrls = _post_effects_row()
    tunable_ctrls = _tunable_sliders()
    return TuneSection(
        preset,
        randomize,
        preset_name,
        save_btn,
        n,
        min_shot,
        seed,
        rand_seed,
        post_ctrls,
        tunable_ctrls,
    )


def _post_effects_row():
    """The chroma / pixel-sort accordions; returns their controls in render() arg
    order. Deliberately outside presets/Randomize (preset dicts map MoshConfig
    fields only)."""
    with gr.Row():
        with gr.Accordion(
            "chroma post-effect — glitch the colour, keep luma sharp", open=False
        ):
            ch_enable = gr.Checkbox(value=False, label="enable chroma")
            ch_mode = gr.Dropdown(
                choices=["random"] + CHROMA_MODES,
                value="databend",
                label="mode",
                info="'random' rerolls per corrupted frame",
            )
            ch_planes = gr.Radio(
                choices=["u", "v", "uv"],
                value="uv",
                label="planes",
                info="chroma plane(s) to touch (swap ignores this)",
            )
            ch_frac = gr.Slider(
                0,
                1,
                value=0.10,
                step=0.01,
                label="frac",
                info="fraction of frames hit — the effect flickers in and out",
            )
        with gr.Accordion(
            "pixel sort post-effect — monotone pixel streaks", open=False
        ):
            ps_enable = gr.Checkbox(value=False, label="enable pixel sort")
            with gr.Row():
                ps_mode = gr.Dropdown(
                    choices=["random"] + PIXELSORT_MODES,
                    value="threshold",
                    label="mode",
                    info="interval rule; 'random' rerolls per frame",
                )
                ps_key = gr.Dropdown(
                    choices=["random"] + PIXELSORT_KEYS, value="luma", label="sort key"
                )
                ps_dir = gr.Radio(
                    choices=["h", "v", "random"], value="h", label="direction"
                )
            ps_frac = gr.Slider(
                0,
                1,
                value=0.20,
                step=0.01,
                label="frac",
                info="fraction of frames hit — the effect flickers in and out",
            )
            with gr.Row():
                ps_lo = gr.Slider(
                    0,
                    255,
                    value=64,
                    step=1,
                    label="luma lo",
                    info="bounds for threshold/bright/dark modes",
                )
                ps_hi = gr.Slider(0, 255, value=192, step=1, label="luma hi")
            ps_rev = gr.Checkbox(value=False, label="reverse (sort descending)")
    return [
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
    ]


def _tunable_sliders():
    """One slider per float tunable and a lo/hi pair per range tunable, grouped by
    the fields' group metadata. Returns them in values.py's packing order
    [*floats, *range lo/hi pairs] -- presets/Randomize/Mosh all rely on it."""
    float_ctrls, range_ctrls = {}, {}

    def float_slider(f):
        m = f.metadata
        float_ctrls[f.name] = gr.Slider(
            m["lo"],
            m["hi"],
            value=getattr(DEFAULTS, f.name),
            step=m["step"],
            label=values.label(f.name),
            info=m["help"],
        )

    with gr.Row():
        with gr.Column():
            with gr.Accordion("video mangles", open=True):
                for f in FLOATS:
                    if f.metadata["group"] == "video":
                        float_slider(f)
        with gr.Column():
            with gr.Accordion("audio mangles", open=False):
                for f in FLOATS:
                    if f.metadata["group"] == "audio":
                        float_slider(f)
            with gr.Accordion("ranges (lo / hi)", open=False):
                for f in RANGES:
                    lo_b, hi_b = f.metadata["lo"], f.metadata["hi"]
                    cur = getattr(DEFAULTS, f.name)
                    with gr.Row():
                        a = gr.Slider(
                            lo_b,
                            hi_b,
                            value=cur[0],
                            step=1,
                            label=f"{values.label(f.name)} lo",
                        )
                        b = gr.Slider(
                            lo_b,
                            hi_b,
                            value=cur[1],
                            step=1,
                            label=f"{values.label(f.name)} hi",
                        )
                    range_ctrls[f.name] = (a, b)

    ordered_floats = [float_ctrls[f.name] for f in FLOATS]
    ordered_ranges = [s for f in RANGES for s in range_ctrls[f.name]]
    return ordered_floats + ordered_ranges


class OutputSection(NamedTuple):
    go: object
    preview: object
    download: object
    gallery_videos: list


def _output_section():
    gr.Markdown("## 4 · mosh")
    go = gr.Button("Mosh", variant="primary", size="lg")
    with gr.Row():
        preview = gr.Video(
            label=f"preview (first {rendering.PREVIEW_SECS}s)", autoplay=True, scale=3
        )
        download = gr.File(label="raw .avi", scale=1)
    with gr.Accordion("recent renders", open=True):
        with gr.Row():
            gallery_videos = []
            for i in range(rendering.GALLERY_N):
                h = rendering.HISTORY[i] if i < len(rendering.HISTORY) else None
                gallery_videos.append(
                    gr.Video(
                        value=h["path"] if h else None,
                        label=h["caption"] if h else f"slot {i + 1}",
                        interactive=False,
                    )
                )
    return OutputSection(go, preview, download, gallery_videos)


def _save_preset(name, n, min_shot, *tunable_values):
    if not name or not name.strip():
        return gr.update()
    name = name.strip()
    values.save_preset_values(name, n, min_shot, tunable_values)
    return gr.update(choices=values.preset_names(), value=name)


def build_ui():
    with gr.Blocks(title="datamosh") as demo:
        gr.Markdown(
            "# datamosh\n**1** pick source videos (several is fine — upload or "
            "media/) · **2** arrange keyframe sections from any source on the "
            "timeline (or leave it empty for random) · **3** pick a preset / "
            "Randomize and tweak · **4** Mosh. Preview plays in-browser; the raw "
            "glitch `.avi` is downloadable."
        )
        src = _source_section()
        timeline, seq_state = _timeline_section(src.source)
        tune = _tuning_section()
        out = _output_section()

        def _on_source_change(source):
            return media.keyframe_info(source), media.section_payload(source), []

        def _timeline_seq(evt: gr.EventData):
            try:
                return [(str(e["s"]), int(e["i"])) for e in evt.order]
            except (AttributeError, TypeError, ValueError, KeyError):
                return []

        src.source.change(
            _on_source_change,
            inputs=[src.source],
            outputs=[src.kf_info, timeline, seq_state],
        )
        timeline.seq(_timeline_seq, inputs=None, outputs=[seq_state])
        src.gen_btn.click(
            media.generate_keyframes,
            inputs=[src.source, src.gap_lo, src.gap_hi],
            outputs=[src.source],
        )
        src.upload_btn.upload(
            media.upload_videos, inputs=[src.upload_btn, src.source], outputs=[src.source]
        )
        src.rescan_btn.click(media.rescan_media, inputs=[src.source], outputs=[src.source])
        tune.preset.change(
            values.preset_values,
            inputs=[tune.preset],
            outputs=[tune.n, tune.min_shot, *tune.tunable_ctrls],
        )
        tune.randomize.click(values.random_values, inputs=None, outputs=tune.tunable_ctrls)
        tune.save_btn.click(
            _save_preset,
            inputs=[tune.preset_name, tune.n, tune.min_shot, *tune.tunable_ctrls],
            outputs=[tune.preset],
        )
        out.go.click(
            rendering.render,
            inputs=[
                src.source,
                tune.n,
                tune.seed,
                tune.min_shot,
                tune.rand_seed,
                seq_state,
                *tune.post_ctrls,
                *tune.tunable_ctrls,
            ],
            outputs=[out.preview, out.download, tune.seed, *out.gallery_videos],
        )
    return demo
