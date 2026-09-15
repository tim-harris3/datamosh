# Building the Windows desktop app

Freezes the Gradio UI (`datamosh-ui --window` mode) into a standalone folder
app with PyInstaller, then wraps it in a `Setup.exe` with Inno Setup.

```powershell
.\packaging\build.ps1              # dist\datamosh\datamosh.exe (onedir folder app)
.\packaging\build.ps1 -Installer   # + dist\installer\datamosh-setup-<version>.exe
.\packaging\build.ps1 -Clean       # wipe build\ + dist\datamosh\ first
```

Prerequisites: the repo venv with the `[ui]` and `[window]` extras installed
(build.ps1 pip-installs PyInstaller into it on first run), and for
`-Installer`: [Inno Setup 6](https://jrsoftware.org/isinfo.php)
(`winget install JRSoftware.InnoSetup`).

## What the pieces do

- **launcher.py** -- entry point of the frozen exe. Forces `--window` mode,
  anchors `DATAMOSH_HOME` at `%USERPROFILE%\Videos\datamosh` (media, renders,
  user presets, logs -- Program Files is not writable; builtin presets ship
  inside the bundled package), and reports startup failures in a message box.
- **datamosh-ui.spec** -- the PyInstaller recipe. The `collect_all` /
  `copy_metadata` calls for gradio and friends are required: gradio finds its
  frontend assets and version via `importlib.metadata`, which static import
  analysis misses. Onedir on purpose: onefile unpacks ~400 MB to temp on every
  launch and trips antivirus heuristics.
- **datamosh.iss** -- Inno Setup script. Per-user install by default (no UAC),
  Start Menu shortcut, optional desktop icon. `AppId` must never change, it is
  what makes a later install an upgrade.
- **build.ps1** -- glue: PyInstaller, then optionally ISCC with the version
  read from pyproject.toml.

## ffmpeg

The app needs ffmpeg + ffprobe. If `packaging\ffmpeg-bin\` (gitignored, not
in the repo) holds `ffmpeg.exe` + `ffprobe.exe`, the spec packs them and the
launcher prepends that folder to PATH -- the installer is then fully
self-contained. Without it, the build still works and the app expects ffmpeg
on the user's PATH (`winget install ffmpeg`), showing an actionable error
dialog if it's missing.

To (re)populate the folder, use the same pinned build CI uses (see the
`windows-installer` job in `.github/workflows/publish.yml` for the current
tag):

```powershell
curl.exe -L -o ffmpeg.zip https://github.com/BtbN/FFmpeg-Builds/releases/download/<tag>/<asset>-win64-gpl-<ver>.zip
# copy bin\ffmpeg.exe, bin\ffprobe.exe and LICENSE.txt (as FFMPEG_LICENSE.txt)
# from the zip into packaging\ffmpeg-bin\  (skip ffplay.exe -- dead weight)
```

The build must be a **full GPL** build, not LGPL and not an "essentials"
variant: export.py/ffmpeg.py encode previews with libx264 (missing from LGPL
builds), and the `xvid` encoder needs libxvid, which gyan.dev "essentials"
lacks -- BtbN's `win64-gpl` and gyan.dev's *full_build* carry both. Shipping a
GPL ffmpeg alongside an MIT app is mere aggregation and fine, but it obliges
you to point users at the ffmpeg sources on request -- the bundled
FFMPEG_LICENSE.txt (installed into the app's ffmpeg\ folder) covers
attribution.

## Runtime requirement on the target machine

The window is rendered by **WebView2** (pywebview's Edge Chromium backend),
preinstalled on Windows 11 and any Windows 10 with Edge updates. Machines
without it need the WebView2 Evergreen runtime from Microsoft.

## CI builds

The `windows-installer` job in `.github/workflows/publish.yml` runs this
whole pipeline on every published GitHub release: it downloads the pinned
GPL ffmpeg into `ffmpeg-bin\`, verifies its checksum and encoders, runs
`build.ps1 -Installer`, and attaches `datamosh-setup-<version>.exe` to the
release. A `workflow_dispatch` run does everything except the release upload
(the installer lands in the run's artifacts instead). Local `build.ps1`
builds remain for development.

## Other platforms

PyInstaller output is per-OS: build macOS (.app + dmg) and Linux variants on
those platforms. The CI test matrix already covers all three OSes if the
release workflow ever grows mac/Linux packaging jobs.
