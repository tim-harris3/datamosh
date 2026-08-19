# datamosh

A shot-based datamoshing toolkit: it re-extracts shots from a source video, mangles
them at the raw AVI byte level (duplicated P-frames, deleted keyframes, reordered
motion, corrupted macroblocks, loop-stretched audio), and appends them into a
growing glitch reel. Three ways to use it, from easiest to most flexible:

1. **The browser UI** — sliders, presets, and a preview player.
2. **Recipes** — small scripts you copy and tweak (see [recipes/README.md](recipes/README.md)).
3. **The `datamosh` package** — import the core functions and build your own pipelines.

## Setup (once)

Requirements: Python 3.10+, and [ffmpeg](https://ffmpeg.org/download.html) on your
PATH (`winget install ffmpeg`, then reopen the terminal).

```
py -m venv .venv
.venv\Scripts\pip install -e .
```

(`-e .` installs the `datamosh` package in "editable" mode, so `import datamosh`
works from anywhere and edits to the package take effect immediately.)

## Use

**Browser UI** — then open http://127.0.0.1:7860:

```
.venv\Scripts\python app.py
```

**Command line** (every knob is a flag; `--help` lists them all):

```
.venv\Scripts\python mosh.py --n 10 --seed 5
.venv\Scripts\python mosh.py --preset "heavy bloom" --source media/truck.AVI
```

**Recipe** (the copy-and-tweak workflow — start here if you want scripts):

```
.venv\Scripts\python recipes\basic_mosh.py
```

## Project layout

```
datamosh/       the core package (parsing, effects, scene maps, pipeline, config)
recipes/        copy-and-tweak example scripts
docs/           deeper docs: how the core works, how to write recipes
app.py          the Gradio browser UI entry point (implementation in ui/)
ui/             the UI package: config bridge, media browsing, render, layout
mosh.py         the command-line front end
presets.json    named settings bundles (used by the UI and --preset)
media/          put source videos here
output/         every render, preview, and cache lands here
```

Key ideas:

- **`MoshConfig` is the whole recipe.** One object holds everything a render needs —
  source, output, and every effect knob. `run_mosh(cfg)` is the entire call.
  List every knob: `.venv\Scripts\python -c "import datamosh; datamosh.describe()"`
- **Same seed = same result.** Fix the seed to iterate on the other knobs;
  change it to reroll.
- **First run on a new source is slow** — a one-time scene-detection pass, cached in
  `output/cache/` — then every later run is fast.
- Adding a tunable to `datamosh/config.py` automatically gives it a CLI flag and a
  UI slider; the bounds/help text live on the config field.

Going deeper:

- [docs/core.md](docs/core.md) — how the core actually works: the chunk-list data
  model, what every module does, and the seed-reproducibility rules.
- [docs/recipes.md](docs/recipes.md) — the recipe-writing guide, from a one-config
  script up to multi-pass splicing pipelines.
