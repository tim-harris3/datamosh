#!/usr/bin/env python
"""mosh.py -- command-line front end for the datamosh package.

Thin shim kept for `python mosh.py`; installed users can just run the
`datamosh` command. Every MoshConfig setting is a flag (auto-generated from the
config dataclass); --preset starts from a named preset in presets.json, and any
explicit flag overrides it.

Run:  python mosh.py --n 10 --seed 5 --source media/sample.avi
      python mosh.py --preset "heavy bloom" --source media/sample.avi
"""

from datamosh.cli import main

if __name__ == "__main__":
    main()
