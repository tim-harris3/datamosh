# Roadmap: formal effect plugin API

Goal: `pip install datamosh-someeffect` makes its ops usable in MoshScript JSON
automatically. `@register_op` + `OP_REGISTRY` in `datamosh/script.py` is already
90% of the mechanism; this formalizes the contract and adds discovery.
Fits in one PR.

## 1. Public API surface

- Export `register_op`, `Op`, `OpContext` (and the new `load_plugin_ops`) from
  `datamosh/__init__.py`. Do **not** export `OP_REGISTRY` (reachable as
  `datamosh.script.OP_REGISTRY` for tests, but outside the public promise).
- Docstring tiers on `OpContext`:
  - **Stable plugin API**: `frames`, `audio`, `entry_index`, `base_config`,
    `keep_keyframe_default`, and `rng_for(op_index, explicit_seed)`. All
    randomness must come from `ctx.rng_for`, never the global `random` module —
    byte-identical reruns are the contract, and a plugin that breaks it is buggy.
  - **Internal, may change**: `script_seed`, `entry_key` (use `rng_for`),
    `donors`, `prev_donor`, `donor_cache`, `last_v` (executor plumbing).
- `Op` docstring documents the subclass contract: class-level `op` name,
  JSON-serializable dataclass fields, `apply(ctx, op_index)` mutates
  `ctx.frames`/`ctx.audio` in place, optional `seed: int = None` field fed to
  `ctx.rng_for(op_index, self.seed)`.

## 2. Entry-point discovery (`datamosh/script.py`)

Keep the loader in `script.py` next to `OP_REGISTRY` (a separate module would
circular-import with `Op.from_dict`).

```python
PLUGIN_GROUP = "datamosh.ops"
_plugins_loaded = False
_plugin_errors = {}   # ep name -> error string, surfaced in unknown-op errors

def load_plugin_ops():  # public, idempotent
```

- Guard on `_plugins_loaded`; set it immediately so a crashing plugin isn't
  re-imported on every miss.
- `importlib.metadata.entry_points(group=PLUGIN_GROUP)`, **sorted by
  `(dist name, ep name)`** so registration order is deterministic across
  environments (guard `getattr(ep, "dist", None)` — it can be None on some
  importlib.metadata versions).
- Per entry point: snapshot `dict(OP_REGISTRY)` before, `ep.load()` (importing
  triggers the plugin's own `@register_op` calls), diff after. Validate new names:
  - must contain a `.` (namespace rule below) — else remove and record an error;
  - a key whose class object *changed* means two distributions claimed the same
    op name → **hard `RuntimeError` naming both**. Silent divergence of what an
    op name means across machines is the worst outcome for the determinism
    contract, so collisions fail loudly rather than first-wins.
- Wrap each `ep.load()` in try/except: a broken plugin must not take down
  scripts that never use it; record in `_plugin_errors` + one-line warning.

**Load lazily, on registry miss** in `Op.from_dict`:

```python
opcls = OP_REGISTRY.get(name)
if opcls is None:
    load_plugin_ops()
    opcls = OP_REGISTRY.get(name)
if opcls is None:
    raise KeyError(...)  # existing message + _plugin_errors contents
```

Built-in-only scripts pay zero import cost; determinism is unaffected because
rng derives from `(seed, entry_key, op_index)` strings, never registry state.
The unknown-op error appends plugin load failures so a typo vs. a broken
install are distinguishable. Eager enumeration stays available via the exported
`load_plugin_ops()` for UI/tooling.

## 3. Namespacing

**Require** dot-prefixed names for entry-point-discovered ops
(`"someeffect.wobble"`; recommend prefix = distribution name minus `datamosh-`).
Enforced in `load_plugin_ops()`'s post-import diff, not in `register_op` —
built-ins (never dotted, stated as a promise) and in-process experimentation
stay unrestricted. This makes builtin-vs-plugin collisions structurally
impossible; plugin-vs-plugin collisions hit the hard error.

## 4. Docs

- `CONTRIBUTING.md` "Adding things": a complete minimal plugin —

  ```toml
  # datamosh-wobble/pyproject.toml
  [project]
  name = "datamosh-wobble"
  version = "0.1.0"
  dependencies = ["datamosh"]

  [project.entry-points."datamosh.ops"]
  wobble = "datamosh_wobble"
  ```

  ```python
  # datamosh_wobble.py
  from dataclasses import dataclass
  from datamosh import Op, register_op

  @register_op
  @dataclass
  class Wobble(Op):
      """Duplicate every nth frame once -- a stutter."""
      op = "wobble.stutter"     # dot-prefixed: required for plugins
      every: int = 4
      seed: int = None          # randomness must come from ctx.rng_for

      def apply(self, ctx, op_index):
          rng = ctx.rng_for(op_index, self.seed)
          ...
  ```

  Three rules restated: dot-prefixed names, `ctx.rng_for` only,
  JSON-serializable fields.
- `docs/core.md` script.py section: a "Plugin ops" paragraph (group name, lazy
  load-on-miss, collision-is-an-error, stable OpContext fields).
- CHANGELOG entry.

## 5. Tests (`tests/test_plugins.py`, new)

Mock at the `importlib.metadata.entry_points` seam with fake EntryPoint-like
stubs (`.name`, `.dist`, `.load()`). Autouse fixture snapshots/restores
`OP_REGISTRY`, `_plugins_loaded`, `_plugin_errors` (the registry is
process-global and `test_ops.py` iterates it).

1. Discovery: fake ep registering `"fx.stub"` → `Op.from_dict` succeeds, and
   only lazily (registry lacks the name before the miss).
2. Idempotence: second miss does not re-invoke `entry_points`.
3. Collision: two eps, same dotted name, different classes → RuntimeError
   naming both.
4. Namespace enforcement: un-dotted plugin name rejected; error surfaces in the
   unknown-op KeyError.
5. Broken plugin: `load()` raises → other plugins still load; builtins
   unaffected.
6. JSON round-trip: script containing a stub plugin op saves/loads equal.
7. Determinism spot-check: `ctx.rng_for` draws identical across constructions
   with the same (script_seed, entry_key, op_index).

## Sequencing

1. `script.py` loader + `from_dict` hook + docstrings
2. `__init__.py` exports
3. `tests/test_plugins.py`
4. docs

No pyproject changes in this repo — the entry-point group is declared by
*plugins*. `register_op` still silently overwrites for direct in-process use
(deliberate power-user move; one docstring sentence).
