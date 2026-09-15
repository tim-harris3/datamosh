"""DATAMOSH_HOME relocation.

paths.py reads the env var once at import, so the relocation branch can only be
exercised in a fresh interpreter -- hence the subprocess (conftest.py strips the
var from this process for isolation).
"""

import json
import os
import subprocess
import sys


def _probe(env):
    code = (
        "import json\n"
        "import datamosh.paths as paths\n"
        "import datamosh.presets as presets\n"
        "paths.ensure_output_dirs()\n"
        "print(json.dumps({\n"
        "    'root': str(paths.PROJECT_ROOT),\n"
        "    'media': paths.MEDIA_DIR.is_dir(),\n"
        "    'cache': paths.CACHE_DIR.is_dir(),\n"
        "    'builtins': sorted(presets.builtin_presets()),\n"
        "}))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_fresh_datamosh_home_is_created_and_keeps_builtin_presets(tmp_path):
    # Two levels deep and not yet created: the case a pip user hits first.
    home = tmp_path / "not-yet-created" / "datamosh-home"
    info = _probe({"DATAMOSH_HOME": str(home)})
    assert info["root"] == str(home.resolve())
    assert info["media"] and info["cache"]
    # Builtin presets ship inside the package, so relocation must not lose them.
    assert info["builtins"]


def test_unset_datamosh_home_keeps_classic_layout_and_presets(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "DATAMOSH_HOME"}
    code = (
        "import json\n"
        "import datamosh.paths as paths\n"
        "import datamosh.presets as presets\n"
        "print(json.dumps({'root': str(paths.PROJECT_ROOT),"
        " 'builtins': sorted(presets.builtin_presets())}))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    )
    info = json.loads(out.stdout)
    import datamosh.paths as paths

    assert info["root"] == str(paths.PROJECT_ROOT)
    assert info["builtins"]
