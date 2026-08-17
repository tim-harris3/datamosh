"""Project directory layout. Everything is relative to the project root, so the
project can be moved or copied without editing any paths.

Any datamosh function that takes a file path accepts either an absolute path or a
path relative to the project root (e.g. "media/truck.AVI"), regardless of the
directory you run your script from -- resolve() is what makes that work.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEDIA_DIR = PROJECT_ROOT / "media"       # source videos live here
OUTPUT_DIR = PROJECT_ROOT / "output"     # every render / preview / cache lands here
CACHE_DIR = OUTPUT_DIR / "cache"         # scene-cut cache etc.


def resolve(path):
    """Return `path` as an absolute string; relative paths anchor at the project root."""
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return str(p)


def ensure_output_dirs():
    OUTPUT_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
