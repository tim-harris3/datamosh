# Contributing

Bug reports, effect ideas, recipes, and pull requests are all welcome.

## Dev setup

```sh
git clone https://github.com/tim-harris3/moshing
cd moshing
python -m venv .venv                 # py -m venv .venv on Windows
source .venv/bin/activate            # .venv\Scripts\activate on Windows
pip install -e ".[ui,dev]"
python -m datamosh.sample            # a demo clip to mosh (media/sample.avi)
```

ffmpeg must be on your PATH (`apt install ffmpeg` / `brew install ffmpeg` /
`winget install ffmpeg`).

## Running the checks

```sh
pytest          # the whole suite runs in a few seconds; fixtures are generated
ruff check .    # lint (CI runs both on Linux, Windows, and macOS)
```

## The two ground rules

1. **Effects must be deterministic given an rng.** Every effect primitive takes
   an explicit `rng` (a `random.Random`) instead of touching the global random
   module, and every script op derives its rng from
   `(script seed, entry, op index)` via SHA-256. Same seed = same bytes is the
   project's core promise — `tests/test_script_determinism.py` enforces it, and
   any new effect or op must keep it.
2. **The moshable-AVI shape is a contract.** The byte engine assumes what
   `extract_shot` / `make_moshable` emit: native MPEG-4 ASP (no packed
   bitstream, no B-frames), CFR so one displayed frame = one `00dc` chunk, and
   AC3 audio whose fixed-size chunks survive byte mangling. Changes to
   `ffmpeg.VIDEO_ENCODE_FLAGS` / `AUDIO_ENCODE_FLAGS` ripple through everything
   downstream — treat them as load-bearing.

## Adding things

- **A new tunable**: add one field in `datamosh/config.py` with `tunable()`
  metadata — the CLI flag and UI slider generate themselves.
- **A new script op**: subclass `Op` in `datamosh/script.py` with the
  `@register_op` decorator and a dataclass body; it becomes JSON-serializable
  automatically. Add a round-trip case to `tests/test_ops.py` (the registry
  test picks it up for free) and, if it has randomness, take a `seed` field and
  use `ctx.rng_for(op_index, self.seed)`.
- **A plugin package**: ops can live outside this repo entirely — publish a
  package that declares an entry point in the `datamosh.ops` group, and its
  ops become usable in MoshScript JSON on any machine that has it installed.
  A complete minimal plugin:

  ```toml
  # datamosh-wobble/pyproject.toml
  [project]
  name = "datamosh-wobble"
  version = "0.1.0"
  dependencies = ["datamosh"]

  [project.entry-points."datamosh.ops"]
  wobble = "datamosh_wobble"
  ```

  ```python
  # datamosh_wobble.py
  from dataclasses import dataclass
  from datamosh import Op, register_op

  @register_op
  @dataclass
  class Wobble(Op):
      """Duplicate every nth frame once -- a stutter."""
      op = "wobble.stutter"     # dot-prefixed: required for plugins
      every: int = 4
      seed: int = None          # randomness must come from ctx.rng_for

      def apply(self, ctx, op_index):
          rng = ctx.rng_for(op_index, self.seed)
          ...
  ```

  Three rules: op names must be dot-prefixed (`prefix.name`; built-ins never
  are, so you can't shadow one — two plugins claiming the same name is a hard
  error), all randomness comes from `ctx.rng_for(op_index, self.seed)`, and
  every dataclass field must be JSON-serializable. See the `OpContext`
  docstring for which context fields are stable plugin API.
- **A new recipe**: follow the checklist at the end of
  [docs/recipes.md](docs/recipes.md); keep it runnable against
  `media/sample.avi` so anyone can try it.

## Style

- Python 3.10+, formatted with Black-compatible layout; `ruff check .` must
  pass.
- Docstrings explain *why* and *what for*, in the voice of the existing modules.
- No media files in the repo — test fixtures are generated with ffmpeg's
  pattern sources (see `tests/conftest.py`).
