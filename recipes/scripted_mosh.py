"""Deterministic scripting demo: say exactly what happens to every section.

Where basic_mosh.py rolls dice from config probabilities, this builds a
MoshScript -- an ordered timeline of keyframe sections, each with explicit
per-frame instructions -- saves it to JSON, reloads it, and renders. Run it
twice: the two outputs are byte-identical (the printed hash proves it).

    python recipes/scripted_mosh.py    (venv active; needs media/sample.avi --
                                        generate it with: python -m datamosh.sample)
"""

import hashlib

from datamosh import (
    AudioReverse,
    ClassicMosh,
    Databend,
    DeleteKeyframe,
    DupFrames,
    Entry,
    FrameQuota,
    MoshScript,
    Reorder,
    Transplant,
    run_script,
)

SOURCE = "media/sample.avi"

script = MoshScript(
    output="output/scripted_mosh.avi",
    seed=42,  # feeds every derived op seed; change it to reroll ONLY the seeded ops
    base_config={"video_databend_bytes": (2, 8)},  # ClassicMosh rolls start from this
)

# entry 0: a clean open -- classic config-driven mosh, keyframe kept automatically
script.add(
    Entry(
        source=SOURCE,
        t0=2.0,
        t1=4.5,
        ops=[ClassicMosh(config={"keyframe_delete_prob": 0.0}, intensity=1.0)],
    )
)

# entry 1: an explicit melt -- delete the keyframe so this section's motion smears
# over entry 0's pixels, stutter frame 5, then play the P-frames backwards
script.add(
    Entry(
        source=SOURCE,
        t0=10.0,
        t1=12.0,
        ops=[
            DeleteKeyframe(),
            DupFrames(at=5, count=4),
            Reorder(pattern="reverse"),
        ],
    )
)

# entry 2: motion transplant -- entry 1's genuine motion painted over this
# section's canvas, then a light seeded databend on two exact frames
script.add(
    Entry(
        source=SOURCE,
        t0=20.0,
        t1=22.5,
        ops=[
            DeleteKeyframe(),
            Transplant(donor="prev"),
            Databend(frames=[4, 7], nbytes=6, seed=12),
        ],
    )
)

# entry 3: snap to an exact length (beat-grid trick) with reversed audio
script.add(
    Entry(
        source=SOURCE,
        t0=13.0,
        t1=16.0,
        ops=[
            DeleteKeyframe(),
            Reorder(pattern="bounce"),
            FrameQuota(count=60),  # exactly 60 frames: trim, or freeze-pad
            AudioReverse(),
        ],
    )
)

# round-trip through JSON: the saved file IS the recipe -- share it, re-run it
json_path = script.save("output/scripted_mosh.json")
reloaded = MoshScript.load(json_path)
assert reloaded.to_dict() == script.to_dict(), "JSON round-trip drifted"
print(f"script saved + reloaded: {json_path}")

out, fixed = run_script(reloaded)

digest = hashlib.sha256(open(out, "rb").read()).hexdigest()
print(f"output: {out}")
print(f"sha256: {digest[:16]}...  (run again -- same script, same hash)")
