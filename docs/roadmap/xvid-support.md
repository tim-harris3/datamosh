# Roadmap: Xvid encoder support

Goal: `encoder="xvid"` everywhere a moshable is produced — the classic
community datamosh look. One PR. All key claims below were verified
empirically against a real ffmpeg build (2024-11 gyan.dev full).

## Verified findings

- **Availability**: `libxvid` is in "full" builds but absent from
  `-essentials` and many distro builds → runtime detection by parsing
  `ffmpeg -hide_banner -encoders` (note: `ffmpeg -h encoder=X` exits 0 even
  for unknown names, so it is NOT a reliable probe).
- **Flag mapping**: the existing flag list with only `mpeg4` → `libxvid`
  swapped is accepted as-is. `-qscale:v/-g/-bf` are generic options honored by
  the wrapper; `-sc_threshold` is inert for libxvid but kept for a uniform
  shape.
- **Packed bitstream**: the libxvid wrapper only enables packed mode when
  B-frames are in use — `-bf 0` (already in the flags) guarantees unpacked
  output. No extra flag; add a comment + an invariant test as the canary.
- **Keyframes**: `-g 999999` → exactly 1 keyframe; `-force_key_frames` honored
  → make_moshable semantics carry over. Xvid can still slip an I-frame on hard
  cuts; the existing `_demote_extra_keyframes` covers it.
- **Bitstream**: P-frames start with the standard `00 00 01 b6` VOP start code
  → `databend_blob(skip=16)` stays safe. 1-chunk-per-frame invariant holds.
- **fourcc** becomes `xvid` instead of `FMP4`; both decode with the same mpeg4
  decoder, so mixing encoders across spliced sections works (doc sentence).

## API decision

`ffmpeg.video_encode_flags(encoder)` + explicit `encoder="mpeg4"` params +
a structural `MoshConfig.encoder` field and a script-level `MoshScript.encoder`
field. **No process-global setting** — it would make saved scripts
non-self-describing, contradicting the determinism contract.

Caveat found in cli.py: structural fields do NOT get CLI flags for free —
`config_from_args` picks them up via `_PASSTHROUGH`, but `add_config_args`
adds structural flags by hand; forgetting `--encoder` there breaks every CLI
run with AttributeError. Presets DO get the field for free via `from_mapping`.

## Steps

1. **ffmpeg.py**: `ENCODERS = ("mpeg4", "xvid")`;
   `video_encode_flags(encoder)` (ValueError on unknown); keep
   `VIDEO_ENCODE_FLAGS = video_encode_flags("mpeg4")` as the documented alias;
   `@lru_cache have_libxvid()` parsing `-encoders`; `require_encoder(encoder)`
   with an actionable error ("your ffmpeg build lacks libxvid — install a full
   build or use encoder='mpeg4'"); `stream_transform(..., encoder="mpeg4")`.
2. **config.py**: `encoder: str = structural("mpeg4", help=...)` after
   `output`.
3. **cli.py**: manual `ap.add_argument("--encoder", choices=("mpeg4","xvid"))`
   — the pitfall step; the CLI help test catches omission.
4. **scenes.py**: `extract_shot(..., encoder="mpeg4")`.
5. **sections.py**: `make_moshable(..., encoder="mpeg4")`.
6. **pipeline.py**: `require_encoder(cfg.encoder)` right after
   `require_ffmpeg()` (fail fast, not on clip 1); thread to extract_shot.
7. **script.py**: `MoshScript.encoder = "mpeg4"`, serialized **only when
   non-default** (sparse pattern like `audio_grain`) so old scripts load and
   default scripts stay loadable by older builds; `require_encoder` in
   run_script; thread through `_materialize` → extract_shot. Per-entry encoder
   noted as future work. Docstring: same script + same encoder = same bytes
   *per ffmpeg/libxvidcore build*; `avi+section` entries never re-encode so
   they stay byte-exact regardless.
8. **chroma.py / pixelsort.py**: `encoder=` forwarded to stream_transform.
9. **Tests**: `HAVE_LIBXVID` + `needs_libxvid` marker in conftest. New
   test_xvid.py: xvid moshable parses with same section structure + video
   chunk count == keyflag count (the packed-bitstream canary); run_mosh
   end-to-end twice with same seed → equal SHA-256; script serialization
   (encoder key only when non-default, save/load round-trip — no libxvid
   needed); require_encoder error paths (monkeypatch have_libxvid); `--encoder`
   in CLI help. No golden hashes committed (bytes vary per libxvidcore build).
10. **Docs**: core.md encode-shape section, CONTRIBUTING flag mention,
    CHANGELOG, README. UI encoder dropdown is an optional follow-up
    (structural fields don't auto-render; UI keeps working with the default).

Sequencing: step 1 first; 2–8 independent after that.
