# Roadmap: frame-number addressing in MoshScript entries

Goal: address source material by exact frame number — `avi + f0/f1` as a
fourth Entry addressing mode. One small PR; strictly more reproducible than
the section mode (pure list slicing of parsed bytes, no re-encode).

## Decisions

1. **`avi + f0/f1` only; no `source + f0/f1`.** `extract_shot` uses input-side
   `-ss` with millisecond formatting — converting frames→seconds via probed fps
   would advertise frame accuracy the encode path can't guarantee (CFR
   assumption, 29.97 truncation, ffmpeg-build variance). The documented
   workflow for frame precision: `make_moshable()` once, then address with
   `f0/f1`. One doc sentence says exactly that.
2. **Half-open `[f0, f1)`, Python-style negatives, exact.** Entry yields
   exactly `f1-f0` video frames before ops. Negatives resolve against total
   video frame count at materialize time; empty/out-of-range raises ValueError
   naming the total ("has N video frames" — a discovery aid until `inspect`
   lands). run_script's existing except around `_materialize` turns that into
   "materialize failed; skipped", matching section behavior.
3. **Keep `SCRIPT_VERSION = 1`.** New optional fields defaulting to None: old
   scripts load unchanged; new scripts on old builds fail loudly via the
   existing unknown-field KeyError — the desired failure.
4. **Audio rides by interleave position**, mirroring `split_sections`: slice
   the flat chunk list from the f0-th `00dc` chunk up to (excluding) the f1-th.
   No av_ratio math in the slice — run_script already stretches audio at emit
   time; return `audio_video_ratio(path)` like the section path.
5. **No keyframe special-casing.** A range not starting on a keyframe has none
   — the existing stream-start warning covers the melt-by-design case; a range
   spanning keyframes gets extras demoted by `demote_extra_keyframes=True`
   (document that False keeps them).

## Steps

1. **`slice_frames(chunks, f0, f1)` in `datamosh/sections.py`** next to
   split_sections: build `v_pos` index list, resolve negatives, validate
   `0 <= f0 < f1 <= n` (ValueError names n), return the uncopied sub-list
   (`chunks[v_pos[f0] : v_pos[f1] if f1 < n else len(chunks)]`) — caller
   copies, as _materialize does for cached sections.
2. **Entry schema in `datamosh/script.py`**: fields `f0: int = None`,
   `f1: int = None`. Validation matrix: exactly one of time-mode / avi-mode /
   chunks; within avi-mode require `avi` plus exactly one of `section` vs
   `f0+f1` (both required; `f1 > f0` when both non-negative). Error text
   becomes "source+t0+t1, avi+section, avi+f0+f1, or chunks" (existing test
   regex still matches). `to_dict` emits the right pair; `from_dict` needs no
   change (dataclass fields auto-picked-up). Update Entry + module docstrings.
3. **`_materialize`**: cache becomes
   `avi_cache[path] = (header, movi_start, chunks, sections)` — one parse_avi
   + split_sections serves both modes. Frame branch: `slice_frames`, prefix
   ValueError with `entry {i}:`, copy chunks, same av_ratio caching. No
   run_script loop changes — demotion, donor capture, warning, stretch,
   interleave already operate on whatever _materialize returns.
4. **Export** `slice_frames` from `datamosh/__init__.py` (parity with
   split_sections).
5. **Tests**: units in test_ops.py (synthetic chunk lists: exact counts,
   boundary audio, negatives, validation raises; Entry matrix extensions;
   round-trip with f0/f1 and no `section` key). Integration in
   test_script_determinism.py: add a frame-range entry to `build_script` so
   the existing byte-identity/seed/save-load tests cover the mode for free;
   plus exact-count test (no-op entry → f1-f0 video chunks), keyframe-less
   range emits the stream-start warning, overrange f1 → entry skipped → the
   existing "no entry produced any frames" RuntimeError when it's the only one.
6. **Docs**: core.md "three ways" → four; melt-by-design note; the
   "make_moshable first" sentence. CHANGELOG.

## Risk

The only refactor of existing behavior is the section_cache tuple shape —
one construction site, one read site.
