# datamosh

A programmatic datamoshing toolkit. It mangles video at the raw AVI byte level —
duplicated P-frames, deleted keyframes, reordered motion, corrupted macroblocks,
loop-stretched audio — with no decoding in the mosh path, and it is built around
one idea most moshing tools don't have: **determinism**. Every render is seeded,
and a `MoshScript` is a JSON-serializable glitch timeline that reproduces
byte-identically — you can save, share, and re-run the exact same mosh.

Three ways to use it, from easiest to most flexible:

1. **The browser UI** — sliders, presets, a drag timeline, and a preview player.
2. **Recipes** — small scripts you copy and tweak (see [recipes/README.md](recipes/README.md)).
3. **The `datamosh` package** — import the core functions and build your own pipelines.

## Setup (once)

Requirements: Python 3.10+, and [ffmpeg](https://ffmpeg.org/download.html) on your PATH:

| OS | ffmpeg install |
| --- | --- |
| Windows | `winget install ffmpeg` (then reopen the terminal) |
| macOS | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |

Then, from a clone of this repo:

```sh
# Windows
py -m venv .venv
.venv\Scripts\pip install -e ".[ui]"

# macOS / Linux
python3 -m venv .venv
.venv/bin/pip install -e ".[ui]"
```

`[ui]` pulls in Gradio for the browser UI; leave it off for the library/CLI only.
No footage handy? Generate a copyright-free demo clip to mosh:

```sh
python -m datamosh.sample        # writes media/sample.avi
```

## Use

(Activate the venv first — `.venv\Scripts\activate` on Windows,
`source .venv/bin/activate` elsewhere — or prefix commands with the venv path.)

**Browser UI** — then open http://127.0.0.1:7860:

```sh
datamosh-ui        # or: python app.py
```

**Command line** (every knob is a flag; `--help` lists them all):

```sh
datamosh --source media/sample.avi --n 10 --seed 5
datamosh --preset "heavy bloom" --source media/sample.avi
```

Two extra verbs close the scripting loop: `prepare` pre-encodes a moshable AVI
once (skipping the slow re-encode on every run), and `inspect` lists its
keyframe sections — the `(avi, index)` entries a `MoshScript` addresses:

```sh
datamosh prepare media/sample.avi --gap 0.5 1.0 --seed 5
datamosh inspect output/sample_moshable.avi          # add --json for machines
```

**Sharing your mosh** — moshed AVIs confuse most players, so `export` turns one
into an mp4, webm, or gif. It decodes the corrupt bytes directly: the export is
a faithful recording of how ffmpeg plays the glitch.

```sh
datamosh export output/run.avi                       # mp4 next to the source
datamosh export output/run.avi --to gif --width 480  # looping gif
datamosh export output/run.avi clip.webm --start 4 --duration 6
```

**Recipe** (the copy-and-tweak workflow — start here if you want scripts):

```sh
python recipes/basic_mosh.py
```

**Scripted** (the deterministic timeline — exact sections, exact ops, same bytes
every run):

```sh
python recipes/scripted_mosh.py
```

## Project layout

```
datamosh/       the core package (parsing, effects, scene maps, pipeline, script, config)
datamosh/ui/    the Gradio UI package (needs the [ui] extra)
recipes/        copy-and-tweak example scripts
docs/           deeper docs: how the core works, how to write recipes
tests/          pytest suite (fixtures are generated -- no media in the repo)
app.py          `python app.py` shim for the UI (same as datamosh-ui)
mosh.py         `python mosh.py` shim for the CLI (same as datamosh)
presets.json    named settings bundles (used by the UI and --preset)
media/          put source videos here (gitignored)
output/         every render, preview, and cache lands here (gitignored)
```

Key ideas:

- **`MoshConfig` is the whole recipe.** One object holds everything a render needs —
  source, output, and every effect knob. `run_mosh(cfg)` is the entire call.
  List every knob: `python -c "import datamosh; datamosh.describe()"`
- **Same seed = same result.** Fix the seed to iterate on the other knobs; change it
  to reroll. `MoshScript` goes further: per-op randomness is derived by hashing, so a
  saved script reproduces byte-identically.
- **Any input format works.** Sources are re-encoded to a canonical moshable AVI
  (MPEG-4 ASP, single keyframe, AC3 audio) before the byte surgery; only the mosh
  path itself is AVI-specific.
- **First run on a new source is slow** — a one-time scene-detection pass, cached in
  `output/cache/` — then every later run is fast.
- Adding a tunable to `datamosh/config.py` automatically gives it a CLI flag and a
  UI slider; the bounds/help text live on the config field.
- **The library is silent by default.** It logs through the `logging` module
  (logger `"datamosh"`); scripts call `datamosh.enable_console_logging()` for the
  friendly per-clip render lines (every bundled recipe does), and embedders route
  `logging.getLogger("datamosh")` however they like. Long operations accept a
  `progress(frac, msg)` callback.

Going deeper:

- [docs/core.md](docs/core.md) — how the core actually works: the chunk-list data
  model, what every module does, and the seed-reproducibility rules.
- [docs/recipes.md](docs/recipes.md) — the recipe-writing guide, from a one-config
  script up to multi-pass splicing pipelines.

## Contributing

Bug reports, effect ideas, and pull requests are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup and the two ground rules
(effects must be rng-seedable, and the moshable-AVI shape is a contract).

## License

[MIT](LICENSE)
