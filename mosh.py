#!/usr/bin/env python
"""mosh.py -- command-line front end for the datamosh package.

Every MoshConfig setting is a flag (auto-generated from the config dataclass);
--preset starts from a named preset in presets.json, and any explicit flag
overrides it.

Run:  .venv\\Scripts\\python mosh.py --n 10 --seed 5
      .venv\\Scripts\\python mosh.py --preset "heavy bloom" --source media/truck.AVI
"""

import argparse
import sys

from datamosh import cli, run_mosh


def main():
    ap = argparse.ArgumentParser(
        description="Shot-based datamosher (re-extracts shots from the source video)."
    )
    cli.add_config_args(ap)
    args = ap.parse_args()
    try:
        cfg = cli.config_from_args(args)
        run_mosh(cfg)
    except (RuntimeError, KeyError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
