# Roadmap: logging + progress-callback API

Goal: library embedders (including the Gradio UI) control output via the
`logging` module; CLI/recipe users keep today's friendly per-clip lines; long
operations report progress through the existing `progress(frac, msg)` callback
convention. One PR.

## Design

- New `datamosh/log.py`: `enable_console_logging(level=INFO, stream=sys.stdout)`
  — idempotent (tag the handler, skip if already attached), plus
  `disable_console_logging()`. Formatter renders INFO as the bare
  `%(message)s` (byte-identical to today's prints) and WARNING+ as
  `WARNING: %(message)s`.
- `datamosh/__init__.py`: attach `NullHandler()` to the `"datamosh"` logger
  (standard library etiquette — embedders get silence and full control);
  re-export `enable_console_logging`.
- Per-module `logger = logging.getLogger(__name__)` giving `datamosh.pipeline`
  etc. as children of `datamosh`.

**Console opt-in is explicit, not auto-detected**: call
`enable_console_logging()` at the top of `cli.main()`, `ui/app.py main()`,
`sample.main()`, and add one visible line to every recipe (including
`_template.py`, so every copied recipe teaches the API). Auto-configuring on
"no handler present" is what the logging HOWTO tells libraries not to do, and
is racy for apps that configure logging after import. Breaking-change note for
third-party scripts (silence until they add the line) goes in CHANGELOG —
0.1.0 alpha is the right time.

## Print inventory (complete sweep)

- `pipeline.py`: sequence-mode line, shot-map summary, per-clip render line,
  final "wrote" → INFO; extract-failed and no-video-frames skips → WARNING.
- `script.py`: demoted-keyframes, per-entry render line, final "wrote" → INFO;
  transplant-no-donor (keep its two-space indent), materialize-failed,
  no-video-frames, starts-without-keyframe (drop the hardcoded `WARNING:`
  prefix — the formatter re-adds it) → WARNING.
- `sections.py`: moshable/mosh_pass/splice/delete lines → INFO.
- `ffmpeg.py`: fixup "wrote" → INFO; "fixup skipped" → WARNING.
- `chroma.py`, `pixelsort.py`, `sample.generate_sample` → INFO.
- Keep as `print`: `config.describe()` (documented contract is printing),
  `sample.main()`'s CLI hint, all `recipes/*` output. `scenes.py` and
  `datamosh/ui/*` have no prints.

## Progress API (keyword-only, default None, backward compatible)

Convention stays `progress(frac, msg)` as in `run_mosh`/`run_script`:

- `ffmpeg.stream_transform(..., progress=None, total_frames=None)`: callback
  every ~30 frames in the read loop.
- `chroma_databend`/`pixel_sort` gain `progress=None`; estimate total frames
  via `duration(src) * fps` and thread both through to stream_transform.
- `make_moshable(..., progress=None)`: run ffmpeg with `-nostats -progress
  pipe:1`, parse `out_time_*` lines; plain `subprocess.run` when None; keep
  CalledProcessError semantics.
- Wire into `datamosh/ui/rendering.py`'s post-effects stage (replace the
  static 0.90/0.94 progress ticks with scaled sub-progress).
- No progress on `mosh_pass`/`replace_sections` (in-memory, fast).

## Tests (`tests/test_logging.py`)

NullHandler doesn't block propagation, so caplog works
(`caplog.set_level(INFO, logger="datamosh")`):

1. run_script emits per-entry INFO records from `datamosh.script` + final
   "wrote".
2. WARNING on materialize-failure and on keyframe-less stream start.
3. Progress callback counts: run_script called exactly len(entries) times,
   final frac 1.0; chroma_databend fracs nondecreasing in [0, 1].
4. enable_console_logging idempotence; capsys shows bare INFO vs
   `WARNING: `-prefixed warnings.

## Sequencing

Sweep + handler wiring must land together (or the CLI goes silent); progress
extensions are independent; tests last. Keep `log.py` stdlib-only to avoid
import cycles (`__init__` imports `cli` eagerly).
