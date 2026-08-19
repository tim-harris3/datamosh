#!/usr/bin/env python
"""app.py -- launches the Gradio browser UI for the datamosh package.

Thin shim kept for `python app.py`; the implementation lives in datamosh/ui/
(see datamosh/ui/__init__.py for the module map). Installed users can just run
the `datamosh-ui` command. Features, briefly:

  - every slider auto-generated from MoshConfig's tunable fields; renders via
    run_mosh(), then transcodes to H.264/MP4 so it previews in the browser,
  - preset dropdown + "Save preset" (output/user_presets.json), Randomize,
    random-seed toggle (fills in the seed it rolled),
  - a rolling gallery of the last few renders (output/ui_history/),
  - multi-source picking, upload into media/, rescan,
  - a keyframe-section grid + drag timeline to mosh an exact section order via
    run_mosh(sequence=...) -- sections from different videos can interleave,
  - "generate keyframes" -> sections.make_moshable() re-encode per source,
  - optional chroma / pixel-sort post-effects baked into the output.

Each render writes its own output/ui_run_NNNN.avi (post-effect and preview files
alongside); renders are serialized, so a second Mosh click queues instead of
clobbering the first.

Run:  python app.py    then open http://127.0.0.1:7860
"""

from datamosh.ui.app import main

if __name__ == "__main__":
    main()
