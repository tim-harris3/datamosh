# Changelog

## 0.1.0 — unreleased

First public release.

- Logging: the library now logs through the standard `logging` module (logger
  `datamosh`, NullHandler attached) instead of printing. CLI, UI, and the
  bundled recipes opt into console output via the new
  `enable_console_logging()`; INFO lines render exactly like the old prints,
  warnings gain a `WARNING: ` prefix. **Breaking for third-party scripts**:
  add `datamosh.enable_console_logging()` to see render lines again.
- Progress: `chroma_databend`, `pixel_sort`, and `make_moshable` accept the
  same `progress(frac, msg)` callback as `run_mosh`/`run_script`; the UI shows
  sub-progress during post-effects.

- Byte-level mosh engine over RIFF/AVI chunk lists (`parse_avi` / `write_avi`):
  keyframe deletion, P-frame duplication and reordering, motion transplant,
  databending, audio scramble/reverse/databend with granular time-stretch.
- `run_mosh()`: shot-based rendering with scene detection, weighted shot picks,
  sequence timelines, and seeded reproducibility.
- `MoshScript` / `run_script()`: deterministic, JSON-serializable glitch
  timelines — same script + same sources = byte-identical output.
- Decode-based effects: chroma corruption (6 modes) and pixel sorting
  (5 interval modes × 6 sort keys).
- Section tooling: `make_moshable` random-keyframe re-encode, section splicing
  between videos, keyframe-section shot maps.
- `datamosh` CLI (every config knob auto-generated as a flag) and `datamosh-ui`
  Gradio browser UI with keyframe grid + drag timeline (`pip install
  datamosh[ui]`).
- `python -m datamosh.sample`: generated demo clip, no footage needed.
