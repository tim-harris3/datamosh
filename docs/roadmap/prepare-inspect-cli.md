# Roadmap: `datamosh prepare` and `datamosh inspect` CLI verbs

Goal: expose the fast-iteration workflow — pre-encode once with `prepare`, list
addressable sections with `inspect`, then script against `(avi, section)`
entries. One PR.

## CLI architecture: verb dispatch (shared with the export plan)

Manual first-token dispatch in `cli.main(argv=None)` with
`VERBS = {"mosh": ..., "prepare": ..., "inspect": ...}`; unknown/absent first
token falls through to `_cmd_mosh(argv)` so the flat
`datamosh --source ... --n ...` form is literally the same code path
(back-compat guaranteed). Each verb owns its `ArgumentParser(prog="datamosh
<verb>")`. `argv=None` parameter makes the CLI testable in-process. Top-level
epilog lists the verbs. The try/except → `sys.exit(str(e))` moves into `main`
so all verbs get friendly errors (add ValueError for inspect's "not an AVI").

## `datamosh prepare`

```
datamosh prepare <src> [-o OUT] [--gap LO HI] [--duration S] [--seed N]
```

- Wraps `sections.make_moshable`. Default output: `output/<stem>_moshable.avi`
  via `paths.OUTPUT_DIR` (output/ is the established landing zone; explicit
  `-o` accepts any path).
- `make_moshable` draws keyframe gaps from the global random module — the CLI
  seeds it (`random.seed(args.seed)`), exactly like the test fixture does.
  Help text: "same seed = same keyframe layout". Don't change make_moshable's
  signature — caller-level seeding is the existing library convention.

## `datamosh inspect`

```
datamosh inspect <file.avi> [--json]
```

**Data path decision: `parse_avi` + `split_sections`, not ffprobe.** MoshScript
addresses sections by the index `split_sections` defines — byte-level truth.
Parsing also gives exact per-section audio chunk counts, works on mangled AVIs
ffprobe chokes on, and reveals the mpeg4 double-keyframe slip: print a
`keys` column and flag sections whose keyframe count != 1 with `!` plus a
footnote that MoshScript entries auto-demote extra keyframes.

1. New `header_info(header_prefix)` in `datamosh/avi.py` → {width, height,
   fps, codec}, parsed from the header bytes parse_avi already returns
   (avih dwWidth/dwHeight; vids strh dwRate/dwScale with dwMicroSecPerFrame
   fallback; fccHandler for codec). Mirrors `_patch_header`'s
   struct.unpack_from/find style.
2. New `describe_sections(path)` in `datamosh/sections.py` returning a dict:
   file summary (width/height/fps/codec/frames/duration) + per-section
   {index, start, duration, frames, keyframes, audio_chunks}. Start time =
   cumulative frames / fps — exact because the moshable encode is CFR.
   Re-export from `datamosh/__init__.py`.
3. `_cmd_inspect` in cli.py: `--json` prints `json.dumps(describe_sections(...),
   indent=2)` only; default prints a summary line
   (`320x240 FMP4 29.97fps, 143 frames / 4.8s, 12 sections`) + fixed-width
   table `idx start dur frames audio keys`. No `require_ffmpeg()` up front —
   parse_avi only falls back to ffprobe when idx1 is missing.

## Tests (`tests/test_cli_verbs.py`, new — in-process `cli.main([...])`)

1. prepare → parseable multi-section AVI (`--gap 0.4 0.7 --seed 99`).
2. prepare with same `--seed` twice → identical section layout (compare
   keyframe_times/section frame counts, not file sha — encoder output may
   embed nondeterminism; section layout is the contract).
3. inspect table section count == `len(split_sections(parse_avi(...)))`.
4. inspect `--json` parses; per-section keys present.
5. Flat form + `datamosh mosh --help` both exit 0; epilog mentions the verbs.
6. `header_info` unit test in tests/test_avi.py (moshable fixture: 160x120,
   fps ≈ 15).

## Docs

README CLI section + CHANGELOG. In prepare's help: sections are addressed by
`(avi, index)` in MoshScript and `datamosh inspect` lists the indices — that
closes the workflow loop.

## Notes

- Flat form has no positionals today, so verb names can't collide.
- Merge conflict with the export-verb PR is one line (the VERBS dict + epilog).
