# Writing recipes

A recipe is a small Python script in [recipes/](../recipes/) that imports from the
`datamosh` package and renders something. The workflow is always copy → rename →
change the numbers → run:

```sh
python recipes/my_recipe.py     # from the project folder, venv active
```

This guide goes from the one-config recipe up to full multi-pass chunk surgery.
For what the functions do *internally*, see [how the core works](core.md).

## The three levels

| level | you write | good for |
|---|---|---|
| 1. config-only | a `MoshConfig` + `run_mosh(cfg)` | sources with real scene cuts (shows, edits, trailers) |
| 2. custom shot map | `make_moshable` + `run_mosh(cfg, shots=...)` | continuous footage, MJPEG camera files, controlling the cutting pace |
| 3. chunk surgery | `parse_avi` → transforms → `write_avi` | splicing between videos, per-section control, multi-pass moshes |

Every function used below is importable straight from the package root:
`from datamosh import MoshConfig, run_mosh, parse_avi, ...`

## Level 1: a config and one call

```python
from datamosh import MoshConfig, run_mosh

cfg = MoshConfig(
    source="media/sample.avi",
    output="output/my_mosh.avi",
    n=8,                        # glitch segments appended per run
    seed=7,                     # same seed = same result; change to reroll
    keyframe_delete_prob=0.8,   # higher = more melty transitions
    video_bounce_prob=0.9,      # ping-pong motion
)
run_mosh(cfg)
```

That's [recipes/basic_mosh.py](../recipes/basic_mosh.py).
[recipes/_template.py](../recipes/_template.py) is the same thing with **every**
knob listed and commented — copy it when you want to see all the dials at once.
To print every tunable with its bounds and help text:

```sh
python -c "import datamosh; datamosh.describe()"
```

Things worth knowing at this level:

- **Paths** are relative to the project root regardless of where you run from —
  `"media/x.avi"` and `"output/y.avi"` always mean the project's folders.
- **Seed discipline**: keep the seed fixed while tuning the other knobs so you can
  see what each change does; change the seed to reroll. `seed=None` rolls fresh
  every run.
- **First run on a new source is slow** (a one-time scene-detection pass, cached
  in `output/cache/`); after that it's fast.
- `reset=False` appends onto the existing output file instead of starting fresh —
  you can grow one reel across many runs (or many configs).
- Every render also gets a `*_fixed.avi` (unless `fixup=False`): a remux that
  seeks properly in more players. The raw file is the "true" mosh; the fixed one
  is for scrubbing.
- **Deriving variants**: `MoshConfig` is a dataclass, so
  `from dataclasses import replace` and `heavy = replace(cfg, keyframe_delete_prob=0.95)`
  gives you a tweaked copy without repeating everything.
- **Presets**: `from datamosh import presets` then
  `cfg = presets.preset_config("heavy bloom")` starts you from a named bundle
  (see `presets.json`); it never touches `source`/`output`, so set those yourself.

## Level 2: your own shot map

`run_mosh` normally picks shots between detected scene cuts. Continuous footage
(one long take — a phone clip, dashcam footage) has no cuts, so it reads as **one
giant shot** and every segment moshes the whole clip. The fix: re-encode the source
with keyframes at *random* timestamps, then use those keyframe sections as the shot
map:

```python
import random
from datamosh import MoshConfig, run_mosh, make_moshable, keyframe_shots

random.seed(5)                                   # one seed for the whole script

moshable = make_moshable("media/my_take.mp4", "output/my_take_moshable.avi",
                         gap_range=(0.2, 10.0))  # random seconds between keyframes
cfg = MoshConfig(source=moshable, output="output/my_take_mosh.avi", n=10)
run_mosh(cfg, shots=keyframe_shots(moshable))
```

- `gap_range` **is the cutting pace**: `(0.2, 10.0)` gives anything from flashes
  to long takes; `(0.3, 1.0)` gives relentless rapid cuts.
- This is also mandatory for sources with no P-frames — MJPEG camera footage is
  all keyframes, so there's no motion to mangle until `make_moshable` re-encodes
  it to MPEG-4 ASP.
- Note the seeding pattern: `random.seed(...)` **once at the top**, and no `seed=`
  on the config. `make_moshable`'s random keyframe times come from the same
  stream, so the whole script is one reproducible roll. (Setting `cfg.seed` would
  also work — `run_mosh` reseeds — but then the keyframe placement above it
  wouldn't be covered.)

## Level 3: chunk surgery

Everything in the toolkit operates on a parsed **chunk list** (see
[core.md](core.md#the-chunk-list--the-one-data-structure)), and you can hold that
list yourself between passes. The canonical example is
[recipes/splice_from_examples.py](../recipes/splice_from_examples.py) — the
"one video's motion blooms over another video's pixels" pipeline:

```python
import random
from datamosh import (MoshConfig, run_mosh, make_moshable, keyframe_shots,
                      parse_avi, write_avi, mosh_pass, audio_video_ratio,
                      example_section_pool, replace_sections,
                      delete_tagged_keyframes, ffmpeg)

random.seed(5)

# 1. moshable conversion with random keyframes
moshable = make_moshable("media/sample.avi", "output/splice_moshable.avi")

# 2. mosh pass 1, keyframe sections as the shot map
cfg = MoshConfig(source=moshable, output="output/splice_pass1.avi",
                 n=10, reset=True, fixup=False)
run_mosh(cfg, shots=keyframe_shots(moshable))
header, movi_start, chunks = parse_avi(cfg.output)

# 3. swap some sections for material from other AVIs, then delete most of the
#    spliced keyframes so the surrounding motion blooms over the new pixels
chunks = replace_sections(chunks, 0.25, example_section_pool("media/examples"))
chunks = delete_tagged_keyframes(chunks, 0.75)

# 4. mosh everything again over the merged sections
chunks = mosh_pass(cfg, chunks, "pass 2", av_ratio=audio_video_ratio(moshable))

write_avi("output/splice_moshed.avi", header, movi_start, chunks)
ffmpeg.fixup("output/splice_moshed.avi")
```

The building blocks compose freely; the invariants to respect:

- **The stream needs a valid start.** The first video keyframe in the file must
  survive, or playback opens on garbage (or nothing). `run_mosh`, `mosh_pass`,
  and `replace_sections` all protect section 0 for you — if you write your own
  loop over `mosh_segment`, pass `keep_keyframe=True` for the first section.
- **Only mix compatible AVIs.** Sections you splice together must share the
  moshable encode shape (same resolution helps; audio must match —
  `make_moshable` forces 48 kHz stereo AC3 for exactly this reason). Everything
  produced by `extract_shot` / `make_moshable` / `chroma_databend` / `pixel_sort`
  qualifies.
- **Pass `av_ratio` when you call `mosh_pass`/`mosh_segment` yourself** —
  `audio_video_ratio(the_moshable)` — or the stretched audio slowly drifts out of
  sync with the video.
- **Intermediate files are cheap.** `write_avi` at any point gives you a playable
  checkpoint of the pass you're on; keeping `output/..._pass1.avi` around makes
  tuning pass 2 much faster to iterate on.
- Set `fixup=False` on intermediate configs (no point remuxing files only the
  next pass will read) and run `ffmpeg.fixup()` once on the final output.

For a much bigger level-3 example — beat-grid timelines, section pools drawn
from a whole folder of clips, per-section moshing as sections are placed — read
[recipes/beat_mosh.py](../recipes/beat_mosh.py).

## Preprocessing with the decode-based effects

`chroma_databend` (colour-only corruption) and `pixel_sort` (ordered pixel
streaks) both emit the standard moshable AVI, so they slot in anywhere a source
goes — typically as a first stage:

```python
from datamosh import chroma_databend, pixel_sort, MoshConfig, run_mosh

stage1 = chroma_databend("media/sample.avi", "output/sample_chroma.avi",
                         mode="random", planes="uv", frac=0.30, seed=5)
run_mosh(MoshConfig(source=stage1, output="output/sample_final.avi", n=8, seed=5))
```

Both take `frac` (fraction of frames touched — the effect flickers in and out
rather than sitting on every frame), a `seed`, and `mode="random"` to reroll the
mode per frame. See [recipes/chroma_corrupt.py](../recipes/chroma_corrupt.py) and
the docstrings for the full parameter list.
[recipes/pixel_sort_sampler.py](../recipes/pixel_sort_sampler.py) renders every
pixel-sorting method against one short clip (`output/pixelsort_*.avi`) so you can
compare them like-for-like before committing one to a pipeline.

## Checklist for a new recipe

1. Start from the closest existing recipe (`basic_mosh.py`, `_template.py`,
   `splice_from_examples.py`) — copy and rename, don't edit in place.
2. Put a docstring at the top saying what the recipe produces and the run
   command; future-you copies recipes too.
3. Hoist the numbers you'll want to tweak into UPPERCASE constants at the top
   (see `splice_from_examples.py`) instead of burying them in calls.
4. Seed once: either `seed=` on a single-`run_mosh` recipe, or `random.seed(SEED)`
   at the top of a multi-step one.
5. Render into `output/` with a name matching the recipe.
6. Iterate with a small `n` / short `duration=` on `make_moshable` until the
   character is right, then scale up for the real render.
