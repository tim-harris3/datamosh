# Recipes

A recipe is a small script you copy, rename, and tweak. That's the whole workflow:

1. **Copy** a recipe file (start with `basic_mosh.py`, or `_template.py` to see every knob).
2. **Rename** the copy to whatever you like, e.g. `my_glitch.py`.
3. **Change the numbers** — the comments on each line tell you what the knob does.
4. **Run it** from the project folder (with the venv active):

   ```sh
   python recipes/my_glitch.py
   ```

Your render lands in the `output/` folder. Paths like `"media/sample.avi"` are relative
to the project folder, so drop new source videos into `media/` and reference them
that way. No footage yet? `python -m datamosh.sample` generates a demo clip at
`media/sample.avi` that every recipe defaults to.

Tips:

- Every recipe starts with `enable_console_logging()` — that's what prints the
  per-clip render lines. Keep it in your copies (the library is silent without it).
- **Same seed = same result.** Keep the seed fixed while you tweak the other numbers,
  then change the seed to reroll the dice.
- Every knob, its allowed range, and what it does:
  `python -c "import datamosh; datamosh.describe()"`
- The first run on a new source does a one-time scene-detection pass (a few minutes
  for a long file). It's cached — every run after that is fast.

The recipes in this folder:

| file | what it shows |
| --- | --- |
| `basic_mosh.py` | the minimal mosh — a config and one `run_mosh()` call |
| `_template.py` | the same, with **every** tunable listed and commented |
| `scripted_mosh.py` | a deterministic `MoshScript` timeline — exact sections, exact ops, byte-identical reruns, saved to JSON |
| `splice_from_examples.py` | multi-pass pipeline: random keyframes, mosh, splice in sections from other AVIs, mosh again |
| `chroma_corrupt.py` | colour-only corruption (chroma bleed) that luma passes through untouched |
| `pixel_sort_sampler.py` | one output per pixel-sorting method, compared like-for-like |
| `beat_mosh.py` | beat-synced cutting: sections snapped to a song's sixteenth-note grid |

Ready to go beyond copying? [docs/recipes.md](../docs/recipes.md) is the full
recipe-writing guide (custom shot maps, multi-pass chunk surgery), and
[docs/core.md](../docs/core.md) explains how the core functions work underneath.
