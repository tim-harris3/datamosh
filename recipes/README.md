# Recipes

A recipe is a small script you copy, rename, and tweak. That's the whole workflow:

1. **Copy** a recipe file (start with `basic_mosh.py`, or `_template.py` to see every knob).
2. **Rename** the copy to whatever you like, e.g. `my_glitch.py`.
3. **Change the numbers** — the comments on each line tell you what the knob does.
4. **Run it** from the project folder:

   ```
   .venv\Scripts\python recipes\my_glitch.py
   ```

Your render lands in the `output/` folder. Paths like `"media/truck.AVI"` are relative
to the project folder, so drop new source videos into `media/` and reference them
that way.

Tips:

- **Same seed = same result.** Keep the seed fixed while you tweak the other numbers,
  then change the seed to reroll the dice.
- Every knob, its allowed range, and what it does:
  `.venv\Scripts\python -c "import datamosh; datamosh.describe()"`
- The first run on a new source does a one-time scene-detection pass (a few minutes
  for a long file). It's cached — every run after that is fast.

The recipes in this folder:

| file | what it shows |
|---|---|
| `basic_mosh.py` | the minimal mosh — a config and one `run_mosh()` call |
| `_template.py` | the same, with **every** tunable listed and commented |
| `splice_from_examples.py` | multi-pass pipeline: random keyframes, mosh, splice in sections from other AVIs, mosh again |
| `chroma_corrupt.py` | colour-only corruption (chroma bleed) that luma passes through untouched |

Ready to go beyond copying? [docs/recipes.md](../docs/recipes.md) is the full
recipe-writing guide (custom shot maps, multi-pass chunk surgery), and
[docs/core.md](../docs/core.md) explains how the core functions work underneath.
