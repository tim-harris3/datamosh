"""Project directory layout. Everything is relative to the project root, so the
project can be moved or copied without editing any paths.

Any datamosh function that takes a file path accepts either an absolute path or a
path relative to the project root (e.g. "media/sample.avi"), regardless of the
directory you run your script from -- resolve() is what makes that work.

DATAMOSH_HOME relocates the whole tree: the packaged desktop app sets it to a
per-user folder (the install dir under Program Files is not writable), and it
lets pip users keep media/output outside site-packages. Unset means the classic
layout: the parent of this package, i.e. the repo root of a checkout.
"""

import json
import os
from pathlib import Path

_home = os.environ.get("DATAMOSH_HOME")
PROJECT_ROOT = Path(_home).expanduser().resolve() if _home else Path(__file__).resolve().parents[1]
MEDIA_DIR = PROJECT_ROOT / "media"  # source videos live here
OUTPUT_DIR = PROJECT_ROOT / "output"  # every render / preview / cache lands here
CACHE_DIR = OUTPUT_DIR / "cache"  # scene-cut cache etc.


def resolve(path):
    """Return `path` as an absolute string; relative paths anchor at the project root."""
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return str(p)


def ensure_output_dirs():
    # parents=True throughout: a relocated DATAMOSH_HOME may not exist yet, and
    # MEDIA_DIR is included because a fresh home has nowhere to receive uploads.
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path, default=None):
    """Read a JSON file; return `default` if it is missing or unparseable."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj, indent=None):
    """Best-effort JSON write for caches/history: an OSError is swallowed, since a
    failed write only costs a re-detect / lost history entry, never a bad render."""
    try:
        with open(path, "w") as f:
            json.dump(obj, f, indent=indent)
    except OSError:
        pass
