# Roadmap: motion-vector-level effects (phased R&D)

The hardest roadmap item — vector-field manipulation (scale/rotate/drift,
accumulating "bloom", directional smears) requires going inside the MPEG-4 ASP
P-VOP bitstream, which is VLC-coded at the macroblock layer. Suitable as a
"help wanted" mega-issue; phases 1+2 are parallelizable across contributors.

## Why it's hard

1. **MVs and DCT coefficients are interleaved per macroblock** — you cannot
   rewrite MVs without at least *length-parsing* every coefficient VLC to find
   the next MVD. Bit-exact coefficient pass-through is possible but you must
   walk over them; this is the most underestimated cost.
2. **MVs are differentially coded against a median predictor** (left/above/
   above-right, edge rules, inter4v per-block rules, modulo wrap). Editing one
   MV changes up to three neighbors' predictors — the only sane design is
   decode-whole-field → transform in absolute space → re-derive every MVD and
   re-bit-pack. fcode clamping needed (v1 clamps; bumping vop_fcode_forward is
   a v2 nicety).
3. **VOP header field widths depend on the VOL header** (in each section's
   keyframe chunk) — parsers must thread VOL context.

The library's own encode contract is a huge scope-limiter: no B-frames, no
packed bitstream, no GMC/qpel/interlace/data-partitioning/resync markers. A
validator should detect and reject those features (Transplant(avi=...) admits
arbitrary external AVIs).

## Approaches

- **(a) Pure-Python parse/edit/repack**: bitio (~150 lines), VOL parser
  (~200), VOP header (~150), VLC tables (~400, tedious/error-prone), MB walk
  incl. coefficient length-parse (~500, the hard part), MV predictor (~150,
  subtle), repack (~200). **60–120 focused hours, high risk** (a one-bit error
  40 MBs in produces garbage 300 MBs later with no local symptom).
- **(b) ffglitch/ffedit external backend**: JSON round-trip mode
  (`ffedit -e mv.json` / `-a mv.json`) keeps ALL transform logic in
  Python/numpy seeded by `ctx.rng_for` — ffedit is a dumb bit-surgeon, no
  QuickJS, no randomness outside datamosh. Non-pip binary (docs-only extra),
  PATH detection, per-build variance. **15–25 h, low-medium risk.**
  *Spike task first (~2h): verify current ffedit CLI/JSON schema for `-f mv`
  on this library's mpeg4 AVIs, Windows + Linux.*
- **(c) Hybrid — recommended**, with two de-risking insights:
  - **Analysis needs no new bitstream code**: ffmpeg exports decoded MVs as
    side data (`-flags2 +export_mvs`, ffprobe `-show_frames`) → numpy fields
    for visualization, for driving transforms, and as a decode-side oracle.
  - **A P-frame *synthesizer* is far easier than an *editor***: emitting a
    P-VOP from scratch with a given MV field and cbp=0 everywhere needs no DCT
    coefficient tables at all (the hardest 40% of (a) vanishes). Pure-motion
    frames are exactly what bloom/smear/drift want aesthetically — dropping
    residuals is a feature in datamosh.

## Phases

| Phase | Deliverable | Hours | Risk |
| --- | --- | --- | --- |
| 1 | Op API + ffedit backend + docs (v1, shippable) | 15–25 | low-med (spike-gated) |
| 2 | ffprobe MV extraction + visualization (`datamosh mv-dump`) | 10–15 | low |
| 3 | Pure-Python P-VOP synthesizer → pip-only bloom/smear | 30–50 | medium |
| 4 | Full residual-preserving in-place editor | 60–120 | high (but de-risked by 2+3's oracles) |

New modules: `datamosh/mv.py` (field model: float32 `(mb_h, mb_w, 2)` in
half-pel units; transforms; backend dispatch), `datamosh/ffedit.py`
(`require_ffedit` with `DATAMOSH_FFEDIT` override, export/apply wrappers,
modeled on ffmpeg.py), `datamosh/mpeg4.py` (phases 3–4: bitio, VLC tables,
VOL/VOP, synthesizer then editor).

## v1 op API sketch (JSON-serializable)

```python
@register_op
@dataclass
class MVTransform(Op):
    op = "mv_transform"
    frames: list = None          # None = every P-frame
    scale: object = 1.0          # float or [sx, sy]
    rotate: float = 0.0          # degrees
    drift: tuple = (0.0, 0.0)    # constant half-pel offset
    drift_accum: tuple = (0.0, 0.0)  # k-th frame gets k * this (bloom ramp)
    jitter: float = 0.0          # seeded per-vector noise radius
    region: tuple = None         # (mbx0, mby0, mbx1, mby1) mask
    affect_skipped: bool = False
    clamp: str = "fcode"
    seed: int = None
    backend: str = "auto"        # "auto" | "ffedit" | "python"

@register_op
@dataclass
class MVBloom(Op):
    op = "mv_bloom"
    source_frame: int = -1
    count: int = 8
    drift: tuple = (0.0, 0.0)    # per repetition
    decay: float = 1.0
    seed: int = None
    backend: str = "auto"
```

Execution (phase 1): `ctx.write_temp_avi(frames)` (new OpContext helper —
run_script must pass header/movi_start through) → ffedit export → numpy
transform with `ctx.rng_for` → ffedit apply → parse_avi → splice payloads back.

## Determinism

Pure-Python paths: trivially deterministic (rng via ctx.rng_for; pick one
convention for feeding numpy). ffedit backend: deterministic per build — amend
the contract docstring to "…+ same ffedit build for MV ops", parallel to the
existing same-ffmpeg-build carve-out; optionally log `ffedit.version()`.

## Testing

`needs_ffedit` marker; generated fixtures only. **Known-motion fixture**:
`testsrc2` cropped with `x='n*2'` (2 px/frame pan) → median extracted vector
≈ (±4, 0) half-pels (assert magnitude+axis, not sign). Phase 1: identity
transform decodes cleanly + field round-trips + same-op-twice byte-identity;
scale=2 doubles the median vector. Phase 3: the closed loop — synthesize from
a chosen field, decode, re-extract, assert exact equality; predictor unit
tests against hand-computed medians; fuzz random fields/fcodes. Phase 4
acceptance bar: **identity transform ⇒ bit-identical payload**; parser walks
every P-frame to exact end-of-payload (catches most VLC typos); differential
vs ffedit where installed.

## Non-goals (forever)

B-VOPs, interlace, GMC, quarter-pel, data partitioning, short header, resync
markers — the validator rejects them; the library's own encoder never
produces them.
