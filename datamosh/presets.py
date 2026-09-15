"""Preset loading and saving.

Built-in presets ship as presets.json inside this package -- anchored at the
package (not PROJECT_ROOT) so they survive a DATAMOSH_HOME relocation and a
plain wheel install alike. Presets you save from the UI go to
output/user_presets.json so the shipped file is never touched. A preset is just
a dict of MoshConfig field names -> values; it never sets source or output, so
applying one won't clobber where you're rendering from or to.
"""

import json
from pathlib import Path

from . import paths
from .config import MoshConfig, from_mapping

BUILTIN_FILE = Path(__file__).with_name("presets.json")
USER_FILE = paths.OUTPUT_DIR / "user_presets.json"


def builtin_presets():
    return paths.load_json(BUILTIN_FILE, {})


def user_presets():
    return paths.load_json(USER_FILE, {})


def all_presets():
    """{name: mapping} for every preset; user presets shadow built-ins on name clash."""
    return {**builtin_presets(), **user_presets()}


def preset_config(name, base=None):
    """A MoshConfig with preset `name` applied over `base` (or the defaults)."""
    presets = all_presets()
    if name not in presets:
        raise KeyError(
            f"unknown preset {name!r}; available: {', '.join(sorted(presets))}"
        )
    return from_mapping(presets[name], base=base if base is not None else MoshConfig())


def save_user_preset(name, mapping):
    """Persist `mapping` (MoshConfig field names -> values) to output/user_presets.json."""
    users = user_presets()
    users[name] = mapping
    paths.ensure_output_dirs()
    with open(USER_FILE, "w") as f:
        json.dump(users, f, indent=2)
