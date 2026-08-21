"""Entry-point plugin discovery tests, mocked at the importlib.metadata seam.

No real plugin distributions are installed: fake EntryPoint-like stubs (.name,
.dist, .load()) stand in, and load() runs the @register_op calls a plugin's
import would. The registry is process-global (test_ops.py iterates it), so an
autouse fixture snapshots and restores it plus the loader's module state.
"""

import importlib.metadata
from dataclasses import dataclass

import pytest

from datamosh import script
from datamosh.script import Entry, MoshScript, Op, OpContext, register_op


class FakeDist:
    def __init__(self, name):
        self.name = name


class FakeEntryPoint:
    """The three attributes load_plugin_ops touches: name, dist, load()."""

    def __init__(self, name, loader, dist="datamosh-fake"):
        self.name = name
        self.dist = None if dist is None else FakeDist(dist)
        self._loader = loader
        self.load_calls = 0

    def load(self):
        self.load_calls += 1
        return self._loader()


def make_stub_op(op_name):
    """Register a fresh Op subclass under op_name, as a plugin's import would."""

    @register_op
    @dataclass
    class StubOp(Op):
        op = op_name
        every: int = 4

        def apply(self, ctx, op_index):
            pass

    return StubOp


def install_eps(monkeypatch, *eps):
    """Point importlib.metadata.entry_points at the fakes; return a call counter."""
    calls = []

    def fake_entry_points(*, group):
        calls.append(group)
        return list(eps) if group == script.PLUGIN_GROUP else []

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
    return calls


@pytest.fixture(autouse=True)
def clean_registry():
    """Snapshot/restore OP_REGISTRY and the loader's state around every test."""
    saved_registry = dict(script.OP_REGISTRY)
    saved_loaded = script._plugins_loaded
    saved_errors = dict(script._plugin_errors)
    script._plugins_loaded = False
    script._plugin_errors.clear()
    yield
    script.OP_REGISTRY.clear()
    script.OP_REGISTRY.update(saved_registry)
    script._plugins_loaded = saved_loaded
    script._plugin_errors.clear()
    script._plugin_errors.update(saved_errors)


def test_plugin_op_is_discovered_lazily(monkeypatch):
    ep = FakeEntryPoint("stub", lambda: make_stub_op("fx.stub"))
    calls = install_eps(monkeypatch, ep)
    assert "fx.stub" not in script.OP_REGISTRY
    assert calls == [], "no registry miss yet -- nothing should have loaded"
    op = Op.from_dict({"op": "fx.stub", "every": 2})
    assert op.every == 2
    assert type(op) is script.OP_REGISTRY["fx.stub"]
    assert ep.load_calls == 1


def test_load_is_idempotent_even_after_failure(monkeypatch):
    calls = install_eps(monkeypatch)  # no plugins installed
    with pytest.raises(KeyError):
        Op.from_dict({"op": "fx.nope"})
    with pytest.raises(KeyError):
        Op.from_dict({"op": "fx.nope"})
    assert len(calls) == 1, "entry_points must be enumerated exactly once"


def test_same_name_from_two_distributions_is_a_hard_error(monkeypatch):
    install_eps(
        monkeypatch,
        FakeEntryPoint("a", lambda: make_stub_op("fx.dup"), dist="datamosh-aaa"),
        FakeEntryPoint("b", lambda: make_stub_op("fx.dup"), dist="datamosh-bbb"),
    )
    with pytest.raises(RuntimeError, match="datamosh-aaa.*datamosh-bbb"):
        script.load_plugin_ops()


def test_undotted_plugin_name_is_rejected_and_surfaced(monkeypatch):
    install_eps(monkeypatch, FakeEntryPoint("stub", lambda: make_stub_op("stubby")))
    script.load_plugin_ops()
    assert "stubby" not in script.OP_REGISTRY
    with pytest.raises(KeyError, match="dot prefix"):
        Op.from_dict({"op": "stubby"})


def test_broken_plugin_does_not_take_down_the_rest(monkeypatch):
    def explode():
        raise ImportError("no module named wobble_deps")

    install_eps(
        monkeypatch,
        FakeEntryPoint("broken", explode, dist="datamosh-broken"),
        FakeEntryPoint("ok", lambda: make_stub_op("fx.ok"), dist="datamosh-ok"),
    )
    op = Op.from_dict({"op": "fx.ok"})  # the healthy plugin still loads
    assert op.op == "fx.ok"
    assert "reorder" in script.OP_REGISTRY, "builtins unaffected"
    assert "wobble_deps" in script._plugin_errors["broken"]
    # a typo'd name reports the broken install alongside the unknown-op error
    with pytest.raises(KeyError, match="wobble_deps"):
        Op.from_dict({"op": "fx.typo"})


def test_script_with_plugin_op_round_trips(monkeypatch, tmp_path):
    install_eps(monkeypatch, FakeEntryPoint("stub", lambda: make_stub_op("fx.stub")))
    original = MoshScript(
        output="output/x.avi",
        seed=9,
        entries=[Entry(avi="m.avi", section=0, ops=[{"op": "fx.stub", "every": 7}])],
    )
    path = tmp_path / "plugin_script.json"
    original.save(str(path))
    assert MoshScript.load(str(path)).to_dict() == original.to_dict()


def test_rng_for_is_stable_across_context_constructions():
    def draws():
        ctx = OpContext(
            frames=[],
            audio=[],
            entry_index=0,
            script_seed=42,
            entry_key=0,
            base_config=None,
            donors=[],
            prev_donor=None,
            donor_cache={},
            last_v=None,
            keep_keyframe_default=True,
        )
        return [ctx.rng_for(3).random() for _ in range(5)]

    assert draws() == draws()
