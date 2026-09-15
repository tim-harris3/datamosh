# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A programmatic datamoshing toolkit: it mangles video at the raw AVI byte level (no decoding in the mosh path), with determinism as the core promise — every render is seeded, and a `MoshScript` JSON reproduces byte-identically. Published as the `datamosh` package (MIT).

## Commands

```sh
# Setup (ffmpeg + ffprobe must be on PATH)
py -m venv .venv                      # Windows; python3 -m venv .venv elsewhere
.venv\Scripts\pip install -e ".[ui,dev]"
python -m datamosh.sample             # generate media/sample.avi to mosh

# Checks (both run in CI on Linux/Windows/macOS, py3.10 and 3.13)
pytest                                # whole suite, a few seconds; fixtures generated, no media in repo
pytest tests/test_ops.py -k roundtrip # single test
ruff check .                          # lint (line-length 100)

# Run
datamosh --source media/sample.avi --n 10 --seed 5    # CLI (or: python mosh.py)
datamosh-ui                                           # Gradio UI (or: python app.py)
python recipes/basic_mosh.py                          # recipe workflow
```

Tests skip cleanly when ffmpeg is missing; fixtures are built in `tests/conftest.py` from ffmpeg pattern sources.

## Architecture

Read [docs/core.md](docs/core.md) before touching the core — it documents every module and the reproducibility rules. The short version:

- **The chunk list is the one data structure.** `parse_avi()` (avi.py) turns an AVI into `list[dict]` of `{"data": bytes, "stream": "v"|"a", "key": bool}`; every effect is a plain-Python transformation of that list; `write_avi()` rebuilds a playable file (index + header sizes) from any such list. Extra dict keys (e.g. `"spliced"`) are allowed and ignored by `write_avi`.
- **Two render paths.** `run_mosh(cfg)` (pipeline.py) is the random pipeline: scene-map → pick shot → `extract_shot` → `mosh_segment` (effects.py) → append → write. `run_script(script)` (script.py) is the deterministic counterpart: a JSON-serializable `MoshScript` of keyframe sections with explicit ops, each op's rng derived via SHA-256 from `(script seed, entry, op index)`.
- **effects.py is two layers**: deterministic primitives that take explicit params + an `rng` (`databend_blob`, `dup_frame`, `reorder_pframes`, …), and config-driven rolls (`mosh_video`, `mangle_audio`, `stretch_audio`) on top. Script ops call the primitives directly.
- **`MoshConfig` (config.py) is the whole recipe** — declaring a field with `tunable()` metadata auto-generates its CLI flag (cli.py) and UI slider; that's the *only* step to expose a new knob. `structural()` declares non-tunable fields.
- **Decode-based effects** (chroma.py, pixelsort.py) stream frames through ffmpeg pipes via `ffmpeg.stream_transform()` and re-emit a moshable AVI, so their output feeds back into the byte path.
- **sections.py** generalizes shots to planted keyframes (`make_moshable`, `mosh_pass`, `replace_sections`) — required for footage with no scene cuts.
- Everything is re-exported from `datamosh/__init__.py`; recipes just `from datamosh import ...`.
- **datamosh/ui/** is the Gradio app (needs the `[ui]` extra); it drives `run_mosh`'s `sequence=` mode from a drag timeline.

## The two ground rules (from CONTRIBUTING.md)

1. **Effects must be deterministic given an rng.** Primitives take an explicit `rng: random.Random`; script ops derive theirs from the script seed. `tests/test_script_determinism.py` enforces same-seed = same-bytes.
2. **The moshable-AVI shape is a contract**: native MPEG-4 ASP, no B-frames, CFR (one displayed frame = one `00dc` chunk), single/planted keyframes, AC3 audio. `ffmpeg.VIDEO_ENCODE_FLAGS` / `AUDIO_ENCODE_FLAGS` are load-bearing — changes ripple through everything.

## The seed contract — read before editing effects.py or pipeline.py

`run_mosh` reproducibility depends on the **exact sequence of global `random.*` calls**. Don't reorder, add, or remove rolls in the effect path casually — even an unused roll shifts every later decision and re-baselines all seeded renders. Short-circuit order in conditions consumes rolls too. `run_script` is the opposite regime: it never touches global `random`. Verify determinism changes by rendering the same seed twice and comparing file hashes.

## Conventions

- Adding a script op: subclass `Op` in script.py with `@register_op` + dataclass body; add a round-trip case to `tests/test_ops.py`; if random, take a `seed` field and use `ctx.rng_for(op_index, self.seed)`.
- The library is silent by default — it logs via `logging.getLogger("datamosh")`; recipes/CLI call `datamosh.enable_console_logging()`. Long operations accept a `progress(frac, msg)` callback.
- Paths: use `paths.resolve()` / `MEDIA_DIR` / `OUTPUT_DIR` — relative paths anchor at the project root (or `DATAMOSH_HOME` when set). `media/` and `output/` are gitignored.
- No media files in the repo; test fixtures are generated.
- Style: Black-compatible formatting, ruff must pass, docstrings explain *why* in the voice of the existing modules.
- Do not add Claude attribution (Co-Authored-By / "Generated with Claude Code") to commits or PRs in this repo.
