# Changelog

## 0.1.0 — unreleased

First public release.

- Beat-sync helpers: `datamosh/beat.py` promotes the beat_mosh recipe's grid
  machinery into the library — `BeatGrid` (drift-free cumulative grid math:
  boundaries telescope through `frame_at`, so cuts stay within half a frame
  of true beat time over a whole song), `place_sections`, `SectionRef` /
  `load_section_refs` (video-only frame counts), `cycle_shuffled` /
  `section_pool`, and `entries_from_beats`, which turns a section pool into
  MoshScript entries with `FrameQuota` appended last (policy — melts,
  escalation, randomness — stays in the caller's `ops_for` and rng). Plus
  `ffmpeg.mux_audio()` (song in as the only audio track of a final H.264/AAC
  mp4) and `MoshScript.checkpoint=False` to skip the O(n²) per-entry output
  rewrites on hundred-entry grids (same bytes; serialized only when False).
  The recipe is now a thin policy layer that also saves its whole timeline as
  a re-runnable script JSON.
- Xvid encoder support: `encoder="xvid"` (or `--encoder xvid` on `datamosh` and
  `datamosh prepare`) encodes moshable AVIs with libxvid instead of native
  mpeg4 — the classic community-mosh encoder, same MPEG-4 ASP shape (`-bf 0`
  keeps the packed bitstream off), `xvid` fourcc. Threaded everywhere a
  moshable is produced: `MoshConfig.encoder`, `MoshScript.encoder` (serialized
  only when non-default, so old scripts load unchanged), `extract_shot`,
  `make_moshable`, `chroma_databend`, `pixel_sort`. libxvid ships in full
  ffmpeg builds only; `ffmpeg.require_encoder()` fails fast with an actionable
  error, and sections from both encoders mix freely in one spliced stream.
- Frame-number addressing: `Entry(avi=..., f0=..., f1=...)` selects exactly
  video frames `[f0, f1)` of a moshable AVI as a fourth MoshScript addressing
  mode — half-open like a Python slice, negatives allowed, pure list slicing
  of the parsed bytes (no re-encode). A range with no keyframe melts by
  design. New helper: `datamosh.slice_frames()`. Scripts stay version 1; old
  scripts load unchanged.
- Export: `datamosh export <src> [dst] [--to mp4|webm|gif]` (and
  `datamosh.export()`) transcodes a moshed AVI into something shareable —
  H.264/AAC mp4 with `+faststart`, VP9/Opus webm, or a palette-optimized gif
  (`--fps`/`--width` caps, `--loop`). The corrupt AVI is decoded directly, and
  `--start`/`--duration` trim output-side so the lying index of a moshed file
  is never fast-seeked. Format is inferred from the destination extension.
- CLI verbs: `datamosh prepare <src>` pre-encodes a moshable AVI (seeded
  keyframe layout, default `output/<name>_moshable.avi`) and
  `datamosh inspect <file.avi> [--json]` lists its keyframe sections — the
  `(avi, index)` entries `MoshScript` addresses — straight from the parsed
  bytes, flagging sections whose keyframe count != 1. The flat
  `datamosh --source ...` form is unchanged. New library helpers:
  `datamosh.describe_sections()` and `datamosh.avi.header_info()`.
- Logging: the library now logs through the standard `logging` module (logger
  `datamosh`, NullHandler attached) instead of printing. CLI, UI, and the
  bundled recipes opt into console output via the new
  `enable_console_logging()`; INFO lines render exactly like the old prints,
  warnings gain a `WARNING: ` prefix. **Breaking for third-party scripts**:
  add `datamosh.enable_console_logging()` to see render lines again.
- Progress: `chroma_databend`, `pixel_sort`, and `make_moshable` accept the
  same `progress(frac, msg)` callback as `run_mosh`/`run_script`; the UI shows
  sub-progress during post-effects.
- Plugin API: `pip install datamosh-someeffect` makes its ops usable in
  MoshScript JSON automatically via the `datamosh.ops` entry-point group,
  discovered lazily on the first unknown op name. `register_op`, `Op`,
  `OpContext`, and `load_plugin_ops` are now exported from `datamosh`; plugin
  op names must be dot-prefixed (`"wobble.stutter"`), and two distributions
  claiming the same name is a hard error. CONTRIBUTING.md has a complete
  minimal plugin.

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
