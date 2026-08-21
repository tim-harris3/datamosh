"""The logging + progress contract: silent as a library, friendly on the CLI."""

import logging
import random

import pytest

from datamosh import (
    Entry,
    MoshConfig,
    MoshScript,
    chroma_databend,
    disable_console_logging,
    enable_console_logging,
    run_mosh,
    run_script,
)

from .conftest import needs_ffmpeg


@pytest.fixture(autouse=True)
def _clean_console_handler():
    """Tests must not leak the console handler into each other (or into caplog)."""
    disable_console_logging()
    yield
    disable_console_logging()


# ---------------------------------------------------------------------------
# logger records
# ---------------------------------------------------------------------------


@needs_ffmpeg
def test_run_script_logs_entry_lines_at_info(moshable, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="datamosh")
    script = MoshScript(
        output=str(tmp_path / "a.avi"),
        fixup=False,
        entries=[Entry(avi=moshable, section=0, ops=[])],
    )
    run_script(script)
    messages = [r.message for r in caplog.records]
    assert any("vframes" in m for m in messages), "per-entry render line missing"
    assert any(m.startswith("wrote ") for m in messages)
    assert all(r.name.startswith("datamosh") for r in caplog.records)


@needs_ffmpeg
def test_run_script_warns_on_materialize_failure(moshable, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="datamosh")
    script = MoshScript(
        output=str(tmp_path / "a.avi"),
        fixup=False,
        entries=[
            Entry(avi=moshable, section=0, ops=[]),
            Entry(avi=moshable, section=999, ops=[]),  # out of range -> skipped
        ],
    )
    run_script(script)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("materialize failed" in r.message for r in warnings)


@needs_ffmpeg
def test_run_script_warns_on_keyframeless_stream_start(moshable, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="datamosh")
    script = MoshScript(
        output=str(tmp_path / "a.avi"),
        fixup=False,
        entries=[Entry(avi=moshable, section=0, ops=[{"op": "delete_keyframe"}])],
    )
    run_script(script)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("starts without a keyframe" in r.message for r in warnings)


# ---------------------------------------------------------------------------
# progress callbacks
# ---------------------------------------------------------------------------


@needs_ffmpeg
def test_run_script_progress_called_per_entry(moshable, tmp_path):
    calls = []
    script = MoshScript(
        output=str(tmp_path / "a.avi"),
        fixup=False,
        entries=[Entry(avi=moshable, section=s, ops=[]) for s in (0, 1)],
    )
    run_script(script, progress=lambda f, m: calls.append((f, m)))
    assert len(calls) == 2
    assert calls[-1][0] == 1.0


@needs_ffmpeg
def test_run_mosh_progress_reaches_one(tiny_src, tmp_path):
    calls = []
    cfg = MoshConfig(
        source=tiny_src, output=str(tmp_path / "a.avi"), fixup=False, min_shot=0.1
    )
    run_mosh(cfg, sequence=[(0.0, 0.5), (0.5, 1.0)], progress=lambda f, m: calls.append(f))
    assert calls[-1] == 1.0


@needs_ffmpeg
def test_chroma_progress_fracs_nondecreasing(tiny_src, tmp_path):
    fracs = []
    random.seed(0)
    chroma_databend(
        tiny_src,
        str(tmp_path / "c.avi"),
        frac=0.1,
        seed=1,
        progress=lambda f, m: fracs.append(f),
    )
    assert fracs, "expected at least one progress call"
    assert fracs == sorted(fracs)
    assert all(0.0 <= f <= 1.0 for f in fracs)


# ---------------------------------------------------------------------------
# console handler
# ---------------------------------------------------------------------------


def test_enable_console_logging_is_idempotent():
    h1 = enable_console_logging()
    h2 = enable_console_logging()
    assert h1 is h2
    logger = logging.getLogger("datamosh")
    tagged = [h for h in logger.handlers if getattr(h, "_datamosh_console_handler", False)]
    assert len(tagged) == 1


def test_console_format_bare_info_prefixed_warning(capsys):
    enable_console_logging()
    logger = logging.getLogger("datamosh.test_console")
    logger.info("plain line")
    logger.warning("something odd")
    out = capsys.readouterr().out
    assert "plain line\n" in out
    assert "WARNING: something odd\n" in out
    assert "INFO" not in out  # info renders bare, byte-identical to print()
