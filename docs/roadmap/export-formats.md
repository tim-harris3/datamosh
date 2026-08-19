# Roadmap: export formats (`datamosh export` → mp4 / webm / gif)

Goal: one command turns a moshed AVI into something shareable. One PR, no new
dependencies.

Key finding: `ffmpeg.transcode()` (the UI preview) already decodes moshed AVIs
**directly** — no fixup remux needed; ffmpeg's mpeg4 decoder soldiers through
corrupt P-frames, which is what produces the moshed look. The real hazard is
*seeking*: moshed AVIs have lying indexes, so `--start`/`--duration` must be
**output-side** (`-ss`/`-t` after `-i`) — decode from the start and drop frames,
which is accurate and preserves the glitch smear at the cut point.

## 1. New module `datamosh/export.py`

```python
EXPORT_FORMATS = ("mp4", "webm", "gif")

def export(src, dst=None, fmt=None, *, fps=None, width=None,
           start=None, duration=None, loop=0, crf=None):
    """Transcode a (moshed) video to a shareable format. Returns dst."""
```

- `fmt` inferred from dst extension; both omitted → mp4 next to src (error if
  dst == src). Unknown format → ValueError naming EXPORT_FORMATS (fires before
  any subprocess). `require_ffmpeg()` up front; `paths.resolve()` both paths.
- Per-format arg builders in a dict:
  - **mp4**: `-c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p -c:a aac
    -b:a 192k -movflags +faststart`; vf `scale=trunc(iw/2)*2:trunc(ih/2)*2`
    (yuv420p needs even dims) or `scale={width}:-2`; `fps=` filter when set.
  - **webm**: `-c:v libvpx-vp9 -crf 32 -b:v 0 -row-mt 1 -cpu-used 2
    -pix_fmt yuv420p -c:a libopus -b:a 128k`. `crf` kwarg overrides for both.
  - **gif**: single-command two-pass palette filtergraph —
    `fps={fps or 15},scale={width or 480}:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle`
    plus `-loop {loop}` and `-an`. No temp palette file. fps/width caps are
    gif-only; mp4/webm stay native unless asked.
- Audio mapping `-map 0:v:0 -map 0:a:0?` so audio-less sources don't fail.
- Leave `transcode()` untouched (deliberately-ultrafast UI preview).
- Don't reuse `VIDEO_ENCODE_FLAGS`/`AUDIO_ENCODE_FLAGS` — those are the
  moshable encode shape, the opposite of what export wants.

## 2. Re-export from `datamosh/__init__.py`

`export`, `EXPORT_FORMATS` (+ `__all__`).

## 3. CLI: verb dispatch (shared with prepare/inspect plan)

Known-verb dispatch on the first token, **not** argparse subparsers (subparsers
can't express "no subcommand = mosh with flags"):

```python
COMMANDS = {"mosh": _mosh_command, "export": _export_command}

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in COMMANDS:
        return COMMANDS[argv[0]](argv[1:])
    return _mosh_command(argv)   # flat legacy form == mosh verb
```

- `datamosh export SRC [DST] --to {mp4,webm,gif} --fps F --width N
  --start T --duration T --loop N --crf N` (start/duration passed through as
  strings so ffmpeg time syntax works).
- Epilog on the mosh parser advertises the verbs in `datamosh --help`.
- Friendly errors: wrap verbs in the existing
  `except (RuntimeError, ValueError, CalledProcessError) → sys.exit(str(e))`.
- `mosh.py` shim unchanged.

## 4. Tests (`tests/test_export.py`, new; fixtures from conftest)

- mp4: exists, codec h264, pix_fmt yuv420p. webm: vp9. gif with fps=10,
  width=120: codec gif, probed width/fps match.
- Format inference from dst extension; ValueError on unknown fmt (no ffmpeg
  needed).
- start/duration: output duration ≈ 1.0s (±0.35 container rounding).
- CLI: `datamosh export --help` exits 0 and lists `--to`; flat-form back-compat
  already guarded by the existing help test.

## 5. Docs

README "Sharing your mosh" snippet (`datamosh export output/run.avi --to gif
--width 480`), CHANGELOG entry. Note: export decodes the corrupt AVI directly —
the export is a faithful recording of how ffmpeg decodes the glitch.

## Risks

- ffmpeg builds without libvpx-vp9/libopus exist — let ffmpeg's stderr through
  on CalledProcessError rather than swallowing it.
- Odd-dimension sources fail yuv420p without the even-scale filter (covered).
