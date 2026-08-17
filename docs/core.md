# How the core works

This is the deep-dive companion to the top-level [README](../README.md). It explains
the data model everything shares, walks through what each core function actually
does, and ends with the rules that keep seeded renders reproducible. If you just
want to make videos, start with [writing recipes](recipes.md) instead.

## The big picture

Datamoshing here is **editing a list of compressed frames without ever decoding
them**. An AVI file is a container holding one bite-sized "chunk" per video frame
and per audio frame. In MPEG-4, most video chunks are **P-frames** — they don't
contain a picture, only *motion*: "take the previous frame and push these blocks
around". Only **keyframes** (I-frames) contain an actual picture. So if you delete
a keyframe, the following motion gets applied to whatever picture happens to be on
screen — the classic melt. If you duplicate a P-frame, its motion is applied twice —
the bloom. Everything the toolkit does is a variation on that idea, executed by
rearranging, duplicating, deleting, and byte-corrupting entries in a chunk list.

The standard flow (`run_mosh`) looks like this:

```
source video
   │  build_scene_map()          scene detection, cached          (scenes.py)
   ▼
[(t0, t1), ...]  shot map
   │  pick a shot, extract_shot()  re-encode to a clean moshable AVI
   ▼
temp AVI ── parse_avi() ──►  chunk list                           (avi.py)
   │  mosh_segment()           the actual mangling                (effects.py)
   ▼
moshed chunk list ── append ──► output chunk list
   │  write_avi()              rebuild index, patch header        (avi.py)
   ▼
output/*.avi  (+ optional *_fixed.avi via ffmpeg -c copy)
```

Every knob the loop reads comes from a single `MoshConfig` object (config.py).

## The chunk list — the one data structure

`parse_avi()` turns an AVI into an ordered `list[dict]`, one dict per movi chunk:

```python
{"data": b"00dc....",   # raw bytes, including the 8-byte RIFF header
 "stream": "v",         # "v" video / "a" audio
 "key": True}           # video: is this a keyframe?  audio: always True
```

That list **is** the video. Every effect in the toolkit is a plain-Python
transformation of it — no decoding, no numpy, no ffmpeg in the middle. Chunks are
copied shallowly (`dict(c)`) before mutation where needed, and `write_avi()` can
turn any such list back into a playable file at any point. This is why multi-pass
recipes are easy: parse → transform → transform → write.

Splice-oriented functions (sections.py) add extra keys to these dicts — e.g.
`replace_sections()` marks swapped-in chunks with `{"spliced": True}` so a later
step can find their keyframes. `write_avi()` only reads `data`/`stream`/`key`, so
extra keys are harmless.

## Why the encode shape matters

Byte-level moshing only works if the file has a strict, predictable shape, so every
moshable AVI in the project is produced with the same encoder settings
([ffmpeg.py](../datamosh/ffmpeg.py) `VIDEO_ENCODE_FLAGS` / `AUDIO_ENCODE_FLAGS`):

- **native `mpeg4` (MPEG-4 ASP)** — P-frames survive being reordered/duplicated and
  decoders keep playing through the damage instead of bailing. No Xvid
  packed-bitstream weirdness.
- **`-g 999999 -sc_threshold 0`** — exactly one keyframe (or only the ones
  `make_moshable` forces), so *we* control where the anchors are.
- **`-bf 0`** — no B-frames; B-frames reference future frames and break when
  reordered.
- **`-fps_mode cfr`** — every displayed frame is exactly one `00dc` chunk.
  Without it ffmpeg pads with N-VOPs to sync audio, and the demuxer later merges
  them, breaking the 1-chunk-per-frame mapping everything relies on.
- **AC3 audio** — fixed 1536-samples-per-chunk frames with a syncword, so audio
  chunks survive shuffling and byte corruption (they mangle instead of muting),
  and chunks from different files interleave into one decodable stream.

If you feed the toolkit an AVI that wasn't made by `extract_shot`, `make_moshable`,
`chroma_databend`, or `pixel_sort`, expect surprises.

## Module reference

### config.py — `MoshConfig`

One dataclass holds *everything* `run_mosh()` needs: what to mosh (`source`,
`output`, `n`, `seed`, …) and every effect tunable. Self-containment is a design
rule — a `MoshConfig` is a complete, reproducible recipe.

Tunables are declared with `tunable(default, lo, hi, step, group, help)`, which
attaches metadata to the dataclass field. That metadata is what `mosh.py` uses to
auto-generate CLI flags and `app.py` uses to auto-generate sliders — **adding a
field here is the only step needed to expose a new knob everywhere**. A tuple
default (e.g. `(1, 10)`) makes it a *range* field: the effect rolls a fresh value
inside the range each time it fires. `structural(default, help)` declares the
non-tunable fields (no slider).

Helpers:

- `describe()` — print every tunable with default, bounds, and help text.
- `from_mapping(dict, base=None)` — build a config from a plain dict (this is how
  presets are applied); unknown keys raise, lists become tuples for range fields.
- `tunable_fields()` / `float_fields()` / `range_fields()` — introspection used by
  the CLI and UI generators.
- `dataclasses.replace(cfg, **changes)` — the standard way to derive a variant
  config in a recipe.

### pipeline.py — `run_mosh(cfg, *, shots=None, progress=None)`

The shared render loop behind the CLI, the UI, and most recipes. Per iteration
(`cfg.n` total):

1. **Pick a shot** from the shot map with `random.choices`, weighted
   `1/duration**short_gop_bias` (0 = uniform); shots shorter than `min_shot` get
   weight 0. The shot map is `build_scene_map(source)` unless you pass your own
   `shots=[(t0, t1), ...]` (see `keyframe_shots`).
2. **Extract it** with `extract_shot()` into a temp AVI and `parse_avi()` it.
3. **Mosh it** with `mosh_segment()`. The very first segment of a fresh output
   keeps its keyframe (`keep_keyframe=True`) so the stream has a valid start;
   `intensity` ramps from 1 up to `1 + escalate` across the run; each clip's
   genuine P-frames are handed to the next iteration as the motion-transplant
   donor.
4. **Append** the moshed chunks and `write_avi()` the whole output. Writing every
   iteration means a crash still leaves a playable file.

The first extracted clip's header becomes the template header for the output file
(or the existing output's header when `reset=False`, which is what makes appending
across runs work). Returns `(output_path, fixed_path_or_None)`; the fixed copy is
`ffmpeg.fixup()`'s more-seekable `-c copy` remux.

### effects.py — `mosh_segment` and friends

`mosh_segment(cfg, segment, keep_keyframe, donor_pframes, intensity, av_ratio)`
is the heart. It processes one clip's chunk list in a **fixed order** (see the
reproducibility section for why the order is sacred):

1. **Capture** the clip's genuine P-frame payloads (returned as the next clip's
   transplant donor — captured *before* anything overwrites them).
2. **Motion transplant** (`video_transplant_prob`): overwrite this clip's P-frame
   payloads with the donor's, so the previous clip's motion plays out over this
   clip's keyframe picture.
3. **Duplicate P-frames**: pick `dup_frames_range` random P-frames, insert
   `dup_count_range` extra copies of each; `intensity` scales the copy count
   (that's what `escalate` ramps).
4. **Maybe delete the leading keyframe** (`keyframe_delete_prob`) — skipped when
   `keep_keyframe=True`.
5. **Reorder** (`_video_reorder`): keyframes stay anchored at the front; the
   P-frames get independent shuffle / reverse / bounce (forward-then-back) /
   skip (drop a fraction) rolls.
6. **Video databend** (`video_databend_prob`): XOR random bytes inside a fraction
   of P-frame payloads, skipping the first 16 bytes past the header so the VOP
   start code survives — corrupt macroblocks, not a dead frame.
7. **Stretch the audio to fit.** The video probably got longer (duplication,
   bounce), so `stretch_audio()` loops semi-random `audio_seg_range`-chunk windows
   `audio_loop_range` times until the audio chunk count matches the video
   *duration* — the target is `len(video) * av_ratio`, because an AC3 frame
   (~32 ms) is slightly shorter than a video frame (~33.4 ms) and a naive 1:1
   match would drift ~4% fast.
8. **Audio mangles** (`mangle_audio`): independent reverse / scramble-a-window /
   databend rolls. The audio databend skips each AC3 frame's first 8 payload
   bytes (syncword/CRC) so corrupted frames hiss and crackle instead of muting.
9. **Interleave**: audio chunks are spread evenly between the video frames, and
   the final `(chunks, this_clip_pframes)` is returned.

### avi.py — `parse_avi` / `write_avi`

`parse_avi(path)` returns `(header_prefix, movi_start, chunks)`:

- `header_prefix` — the raw bytes from file start through the `movi` FOURCC.
  Kept verbatim and reused as the template when writing, so stream formats,
  codec params, etc. are always self-consistent.
- `chunks` — the chunk list described above. Keyframe flags come from the
  file's `idx1` index, falling back to an ffprobe packet scan if there isn't one.

`write_avi(path, header_prefix, movi_start, chunks)` concatenates the chunk
bytes, rebuilds `idx1` from scratch, and patches the header sizes: the RIFF and
movi sizes, `avih dwTotalFrames`, and each stream's `strh dwLength` — video in
frames, audio in *chunks* (AC3 has `dwSampleSize 0`; a stale audio length makes
players cut the sound off early).

The pair round-trips: `write_avi(*parse_avi(f))` reproduces a playable file, and
any chunk-list transformation can sit in between.

### scenes.py — shot maps and extraction

- `build_scene_map(source, threshold)` → `(shots, duration)`. Runs ffmpeg's
  per-frame scene-score filter over the whole file (a full decode — minutes for a
  long source) and splits at scores above `threshold` (lower = more cuts). The
  result is cached in `output/cache/scene_cuts.json`, keyed by
  name+size+mtime+threshold, so only the first run pays.
- `extract_shot(src, t0, dur, temp)` — re-encode one time range into a clean
  single-keyframe moshable AVI (the encode shape above).
- `audio_video_ratio(source)` — audio-chunks-per-video-frame for the duration
  matching in step 7 above: `(1/fps) / (1536/sample_rate)`, probed from the real
  file, with an NTSC/48 kHz fallback (`DEFAULT_AUDIO_VIDEO_RATIO`).

Note: continuous footage with no cuts (e.g. `media/truck.AVI`) comes back from
scene detection as **one giant shot**, so every `run_mosh` segment would mosh the
whole clip. That's what sections.py exists for.

### sections.py — keyframe sections and splicing

These generalize shots from "where the scene cuts are" to "wherever we planted
keyframes", and let sections move between videos:

- `make_moshable(src, dst, gap_range, duration)` — re-encode *any* footage to the
  moshable shape, but with `-force_key_frames` at random `gap_range`-spaced
  timestamps (all other keyframes suppressed). The file now divides into
  random-length sections; the gap range directly sets the cutting pace. Audio is
  forced to 48 kHz stereo AC3 so sections from different files interleave into
  one decodable stream. Also the required first step for sources with no
  P-frames at all (MJPEG camera footage — every frame is a keyframe, nothing to
  mosh until re-encoded).
- `keyframe_shots(path)` — read a file's keyframe timestamps (container probe, no
  decode) and return them as a `run_mosh(shots=...)` shot map.
- `split_sections(chunks)` — cut a chunk list into sections, each starting at a
  video keyframe.
- `mosh_pass(cfg, chunks, label, av_ratio)` — `mosh_segment` over every section
  of an already-parsed file, with the same conventions as `run_mosh`'s loop
  (section 0 keeps its keyframe, escalation ramps, transplant donor threads
  through). This is how you mosh something a second time.
- `example_section_pool(dir)` — returns a `pick()` function yielding a random
  keyframe section from a random AVI in a folder (lazily parsed, cached,
  shallow-copied so later passes can't corrupt the cache).
- `replace_sections(chunks, prob, pick, tag)` — randomly swap sections for
  `pick()` output, tagging swapped-in chunks (default `"spliced"`). Section 0 is
  never replaced — its keyframe is the stream's valid start.
- `delete_tagged_keyframes(chunks, frac, tag)` — delete a fraction of the tagged
  keyframes outright, so the surrounding motion blooms over the spliced pixels
  instead of them starting clean. This one-two punch (replace, then delete) is
  the "one video's motion over another video's pixels" effect.

### chroma.py and pixelsort.py — the decode-based effects

Two effects can't work at the byte level, because what they touch only exists in
decoded pixels:

- `chroma_databend(src, dst, mode, planes, frac, ...)` — corrupt only the colour.
  In the compressed bitstream, luma and chroma coefficients are interleaved per
  macroblock, so there are no "chroma bytes" to edit; raw `yuv420p` is the only
  place U/V are separate contiguous planes. Frames are streamed
  decode → transform → encode through two ffmpeg pipes (no whole-clip buffering),
  Y passes through untouched, and modes (`CHROMA_MODES`) are: `databend` XOR
  speckle, `shift` rolled-plane bleed, `invert` complementary hue, `bias` hue
  push, `gray` neutralize an axis, `swap` exchange U↔V, or `"random"` per frame.
- `pixel_sort(src, dst, mode, key, direction, frac, ...)` — the Asendorf glitch:
  per row (or column), pick intervals by a rule (`PIXELSORT_MODES`: luma
  `threshold` / `bright` / `dark` / random-length `bands` / `full` rows) and sort
  the pixels inside each interval by a key (`PIXELSORT_KEYS`: luma / sat / hue /
  red / green / blue). Fully vectorised in numpy over an rgb24 stream.

Both hit a random `frac` of frames (the rest pass clean, so the effect flickers)
and both re-emit the standard moshable single-keyframe AVI — so their output can
feed straight back into `run_mosh` / `parse_avi` / `mosh_segment` for byte-level
mangling on top.

### The support modules

- **ffmpeg.py** — every ffmpeg/ffprobe invocation and shared encoder flag, in one
  place: `require_ffmpeg()` (friendly install error), probes (`duration`,
  `dimensions`, `frame_rate`, `sample_rate`, `video_keyflags`, `keyframe_times`),
  and `fixup()` (the `-c copy` remux to a `*_fixed.avi` that seeks properly in
  more players — moshed files have deliberately lying indexes).
- **paths.py** — `PROJECT_ROOT` / `MEDIA_DIR` / `OUTPUT_DIR` / `CACHE_DIR`, and
  `resolve()`, which anchors relative paths at the project root so
  `"media/truck.AVI"` works no matter which directory you run from.
- **presets.py** — presets are plain dicts of MoshConfig field names → values.
  Built-ins in `presets.json` (safe to hand-edit), user saves in
  `output/user_presets.json` (shadow built-ins on name clash). A preset never
  sets `source`/`output`. `preset_config(name)` applies one via `from_mapping`.
- **cli.py** — flag generation for `mosh.py`, driven by the tunable metadata.

Everything is re-exported from the package root
([datamosh/\_\_init\_\_.py](../datamosh/__init__.py)), so recipes just
`from datamosh import ...` and never need to know which module a function lives in.

## Reproducibility: the seed contract

**Same seed + same config + same source = byte-identical output.** This is a hard
guarantee the project verifies against, and it works because every random decision
— shot picks, effect rolls, amounts within ranges — comes from Python's global
`random` module after a single `random.seed(cfg.seed)`.

The consequence: reproducibility depends on the **exact sequence of `random.*`
calls**. Rules for anyone editing `effects.py` / `pipeline.py` (or writing recipes
that should rerender identically):

- Don't reorder, add, or remove `random.*` calls in the effect path casually —
  even an extra roll that "does nothing" shifts every subsequent decision and
  changes every seeded render from that point on. If a change is worth it,
  re-baseline deliberately and expect old seeds to produce new (equally valid)
  output.
- Short-circuiting matters too: `if random.random() < p and other_cond:` consumes
  a roll even when `other_cond` fails; reversing the operands changes the stream.
- In recipes, call `random.seed(SEED)` once at the top *before* any datamosh call,
  and leave `cfg.seed=None` on configs used mid-script — `run_mosh` reseeding in
  the middle of a multi-pass recipe would reset the stream (fine, but it means
  step order no longer matters, which is usually not what you want; the
  single-seed-at-the-top pattern makes the whole script one reproducible roll).
- Numpy randomness in `pixel_sort` is seeded *from* the global stream
  (`np.random.default_rng(random.getrandbits(32))`), so it inherits the contract.
