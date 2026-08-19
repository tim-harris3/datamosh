"""run_mosh() and the CLI: end-to-end against the tiny generated clip."""

import hashlib
import subprocess
import sys

import pytest

from datamosh import MoshConfig, parse_avi, run_mosh

from .conftest import needs_ffmpeg


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_run_mosh_without_source_gives_actionable_error():
    with pytest.raises(RuntimeError, match="no source video given"):
        run_mosh(MoshConfig(seed=1))


@needs_ffmpeg
def test_run_mosh_end_to_end_and_seeded_repeatability(tiny_src, tmp_path):
    def render(name):
        cfg = MoshConfig(
            source=tiny_src,
            output=str(tmp_path / name),
            n=3,
            seed=11,
            fixup=False,
            min_shot=0.1,
        )
        out, fixed = run_mosh(cfg)
        assert fixed is None
        return out

    out_a = render("a.avi")
    _, _, chunks = parse_avi(out_a)
    assert sum(1 for c in chunks if c["stream"] == "v") > 0
    assert sha(out_a) == sha(render("b.avi")), "same seed must reproduce the render"


@needs_ffmpeg
def test_sequence_mode_needs_a_source_for_bare_tuples(tmp_path):
    cfg = MoshConfig(output=str(tmp_path / "x.avi"), fixup=False)
    with pytest.raises(RuntimeError, match="no default source"):
        run_mosh(cfg, sequence=[(0.0, 1.0)])


def test_cli_help_lists_generated_flags():
    out = subprocess.run(
        [sys.executable, "-c", "from datamosh.cli import main; main()", "--help"],
        capture_output=True,
        text=True,
    )
    # argparse puts sys.argv[0] aside; flags come from the MoshConfig dataclass
    assert out.returncode == 0
    assert "--keyframe-delete-prob" in out.stdout
    assert "--preset" in out.stdout
