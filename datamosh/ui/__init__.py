"""datamosh.ui -- the Gradio browser UI for the datamosh package, split by concern.

    values.py     config bridge: tunable-control ordering, pack/unpack, presets
    media.py      source discovery, uploads, keyframe thumbnails and sections
    rendering.py  the Mosh click: run_mosh + post-effects + preview + history
    layout.py     the gr.Blocks tree and event wiring
    static/       the keyframe-grid / drag-timeline frontend (html/css/js)
    app.py        the entry point (`datamosh-ui`): launch() in the browser, or
                  --window for a native pywebview window (the [window] extra)

Needs the [ui] extra (gradio):  pip install datamosh[ui]
"""
