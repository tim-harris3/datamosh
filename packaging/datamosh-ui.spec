# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the datamosh desktop app (onedir, windowed).

Build from the repo root:  python -m PyInstaller packaging/datamosh-ui.spec --noconfirm
(or just run packaging/build.ps1). Output lands in dist/datamosh/.

Gradio resolves its frontend assets and its own version through
importlib.metadata at runtime, which PyInstaller's import analysis cannot see
-- the collect_all + copy_metadata calls below are load-bearing, not
belt-and-braces. webview (pywebview) likewise carries runtime DLLs
(WebView2Loader) that only collect_all picks up.

Dropping ffmpeg.exe/ffprobe.exe into packaging/ffmpeg-bin/ before building
bundles them into the app (the launcher puts that folder on PATH); otherwise
the app expects ffmpeg on the user's PATH.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT = Path(SPECPATH).parent  # noqa: F821 -- SPECPATH is injected by PyInstaller

# One list, derived twice: every package that resolves its version through
# importlib.metadata needs BOTH collect_all and copy_metadata, and forgetting
# the latter only surfaces as PackageNotFoundError in the frozen app.
META_PKGS = ("gradio", "gradio_client", "safehttpx", "groovy")

datas, binaries, hiddenimports = [], [], []
for pkg in META_PKGS + ("webview",):  # webview: runtime DLLs only, no metadata lookup
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
for pkg in META_PKGS:
    datas += copy_metadata(pkg)

datas += [
    (str(ROOT / "datamosh" / "ui" / "static"), "datamosh/ui/static"),
    # builtin presets: package data next to presets.py (see presets.BUILTIN_FILE)
    (str(ROOT / "datamosh" / "presets.json"), "datamosh"),
]

ffmpeg_bin = ROOT / "packaging" / "ffmpeg-bin"
if ffmpeg_bin.is_dir():
    binaries += [(str(p), "ffmpeg") for p in ffmpeg_bin.glob("*.exe")]
    datas += [(str(p), "ffmpeg") for p in ffmpeg_bin.glob("*.txt")]  # ffmpeg's license

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["clr_loader", "pythonnet"],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="datamosh",
    icon=str(ROOT / "datamosh" / "ui" / "static" / "datamosh.ico"),
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="datamosh")
