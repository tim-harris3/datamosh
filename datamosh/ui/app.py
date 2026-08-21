"""datamosh.ui.app -- launches the Gradio browser UI (the `datamosh-ui` command).

Requires the [ui] extra:  pip install datamosh[ui]
Run:  datamosh-ui    (or: python app.py)    then open http://127.0.0.1:7860
"""

from datamosh import enable_console_logging, ffmpeg


def main():
    enable_console_logging()
    ffmpeg.require_ffmpeg()
    from . import layout, media  # deferred: importing gradio is slow

    layout.build_ui().launch(allowed_paths=[media.THUMBS_ROOT])


if __name__ == "__main__":
    main()
