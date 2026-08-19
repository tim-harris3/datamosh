# Roadmap plans

Implementation plans for the post-launch feature roadmap. Each file is
self-contained and sized for one PR (except the motion-vector R&D phases) —
they double as ready-to-file GitHub issue bodies.

High demand, low–medium cost (v0.2 targets):

| plan | one-liner | size |
| --- | --- | --- |
| [plugin-api.md](plugin-api.md) | `pip install datamosh-someeffect` → its ops usable in script JSON (entry-point discovery over `@register_op`) | 1 PR |
| [export-formats.md](export-formats.md) | `datamosh export --to mp4/webm/gif` with palettegen GIFs and moshed-AVI-safe seeking | 1 PR |
| [prepare-inspect-cli.md](prepare-inspect-cli.md) | `datamosh prepare` (make_moshable) + `datamosh inspect` (section table) verbs; CLI verb-dispatch architecture | 1 PR |
| [logging-progress.md](logging-progress.md) | `logging` module + `progress(frac, msg)` callbacks replace print() | 1 PR |

Bigger roadmap ("help wanted" material):

| plan | one-liner | size |
| --- | --- | --- |
| [xvid-support.md](xvid-support.md) | `encoder="xvid"` (libxvid) everywhere a moshable is produced — claims verified against a real ffmpeg build | 1 PR |
| [beat-sync-helpers.md](beat-sync-helpers.md) | `datamosh/beat.py`: BeatGrid + entries_from_beats; beat_mosh recipe becomes a thin policy layer | 1 PR |
| [frame-addressing.md](frame-addressing.md) | `avi + f0/f1` as a fourth MoshScript addressing mode (chunk-level slice, no re-encode) | 1 small PR |
| [motion-vector-effects.md](motion-vector-effects.md) | vector-field ops (scale/rotate/drift/bloom) — 4-phase R&D: ffedit backend → ffprobe analysis → pure-Python synthesizer → full editor | multi-phase |

Cross-plan notes:

- The **export** and **prepare/inspect** plans independently converged on the
  same CLI architecture: manual first-token verb dispatch in `cli.main(argv)`
  (not argparse subparsers), with the flat `datamosh --source ...` form
  falling through to the mosh verb. Whichever lands first establishes the
  `VERBS`/`COMMANDS` dict; the other adds entries.
- The **frame-addressing** error message ("has N video frames") and the
  **inspect** verb are complementary discovery aids — either can land first.
- **Xvid** and **motion-vector** both lean on the same scope-limiter: the
  library's canonical encode shape (no B-frames, no packed bitstream).
- The **logging** sweep touches most files other plans touch — land it early
  or rebase-late to keep conflicts trivial.
