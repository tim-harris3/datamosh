"""datamosh.ui.app -- launches the Gradio UI (the `datamosh-ui` command).

Requires the [ui] extra:  pip install datamosh[ui]
Run:  datamosh-ui    (or: python app.py)    then open http://127.0.0.1:7860
Or:   datamosh-ui --window    native window via pywebview (the [window] extra)
"""

import argparse
import os
import sys
from pathlib import Path

from datamosh import enable_console_logging, ffmpeg

ICON = Path(__file__).parent / "static" / "datamosh.ico"


def main():
    parser = argparse.ArgumentParser(prog="datamosh-ui", description="Launch the datamosh UI.")
    parser.add_argument(
        "--window",
        action="store_true",
        help="open in a native window (pywebview) instead of the browser",
    )
    args = parser.parse_args()

    enable_console_logging()
    ffmpeg.require_ffmpeg()
    # A self-hosted tool shouldn't phone home: opt out of gradio's telemetry in
    # every mode (setdefault, so an explicit opt-in via the env var still wins).
    # Must run before the gradio import below reads it.
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
    from . import layout, media  # deferred: importing gradio is slow

    demo = layout.build_ui()
    if args.window:
        _run_windowed(demo, allowed_paths=[media.THUMBS_ROOT])
    else:
        demo.launch(allowed_paths=[media.THUMBS_ROOT])


def _run_windowed(demo, allowed_paths):
    """Serve gradio locally without a browser and show it in a pywebview window."""
    try:
        import webview
    except ImportError:
        raise SystemExit(
            "--window needs pywebview: pip install datamosh[window] "
            "(or run datamosh-ui without --window for the browser UI)"
        )

    if sys.platform == "win32":
        # Own taskbar identity -- otherwise Windows groups us under python.exe
        # and shows its icon. Must happen before the window exists.
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("datamosh.ui")

    webview.settings["ALLOW_DOWNLOADS"] = True  # gradio serves outputs as downloads
    _, local_url, _ = demo.launch(
        allowed_paths=allowed_paths, prevent_thread_lock=True, inbrowser=False
    )
    try:
        window = webview.create_window("datamosh", local_url, width=1280, height=860)
        # icon= covers the GTK/QT backends; Windows ignores it, so the callback
        # sets the WinForms icon directly once the window exists.
        webview.start(_set_windows_icon, window, icon=str(ICON))
    finally:
        demo.close()


def _set_windows_icon(window):
    """Queue the titlebar icon onto the WinForms UI thread.

    Assigning window.native.Icon directly from this background thread issues a
    cross-thread SendMessage while pythonnet holds the GIL; if the UI thread is
    concurrently calling back into Python (e.g. the Shown handler), the two
    deadlock and the window freezes as "Not Responding". BeginInvoke is
    fire-and-forget: the UI thread applies the icon itself when idle.
    """
    if sys.platform != "win32":
        return
    try:
        window.events.shown.wait(10)  # the WinForms form doesn't exist until shown
        from System import Action  # pythonnet, a pywebview dep on Windows
        from System.Drawing import Icon

        def apply():
            try:
                window.native.Icon = Icon(str(ICON))
            except Exception:
                pass  # cosmetic: never let the icon kill the UI thread

        window.native.BeginInvoke(Action(apply))
    except Exception as e:  # cosmetic: never let the icon kill the app
        import logging

        logging.getLogger("datamosh").debug(f"window icon not set: {e}")


if __name__ == "__main__":
    main()
