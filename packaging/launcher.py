"""Entry point for the PyInstaller build only -- `datamosh-ui` (pip) runs
datamosh.ui.app:main directly. This wrapper adapts that entry point to a
double-clicked desktop app:

- anchors DATAMOSH_HOME at a writable per-user folder (the install dir under
  Program Files is not writable) and lets datamosh.paths build the tree there
- prepends a bundled ffmpeg/ folder to PATH when one was packed in, so the
  installer can ship ffmpeg without touching the user's system
- routes stdout/stderr to a log file: a windowed exe has no console, so
  gradio/enable_console_logging would otherwise write into a closed void
- surfaces startup failures (typically: ffmpeg missing) as a message box,
  because a windowed exe that dies at startup is otherwise silent
"""

import os
import sys
import traceback
from pathlib import Path


def main():
    app_dir = Path(sys.executable).resolve().parent
    bundle = Path(getattr(sys, "_MEIPASS", app_dir))  # onedir: the _internal folder

    os.environ.setdefault("DATAMOSH_HOME", str(Path.home() / "Videos" / "datamosh"))
    # datamosh.paths owns the layout: import it after the env var is set and let
    # it build the media/output tree, so the folder names (and how the home path
    # is resolved) live in exactly one place. Importing datamosh this early is
    # safe -- it is silent (NullHandler) and pulls in neither gradio nor ffmpeg.
    from datamosh import paths

    paths.ensure_output_dirs()
    logs = paths.PROJECT_ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    # A bundled ffmpeg wins over none, but a user's own PATH install still works.
    for ffmpeg_dir in (bundle / "ffmpeg", app_dir / "ffmpeg"):
        if (ffmpeg_dir / "ffmpeg.exe").exists():
            os.environ["PATH"] = str(ffmpeg_dir) + os.pathsep + os.environ.get("PATH", "")
            break

    log_path = logs / "datamosh-ui.txt"  # .txt: *.log is gitignored repo-wide, and
    try:  # notepad opens .txt on double-click
        if log_path.exists() and log_path.stat().st_size > 5_000_000:
            log_path.unlink()
    except OSError:
        pass
    log = open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")
    sys.stdout = sys.stderr = log

    sys.argv = [sys.argv[0], "--window"]
    try:
        from datamosh.ui.app import main as ui_main

        ui_main()
    except Exception as e:
        traceback.print_exc()
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                f"{e}\n\nFull log: {log_path}",
                "datamosh could not start",
                0x10,  # MB_ICONERROR
            )
        raise


if __name__ == "__main__":
    main()
