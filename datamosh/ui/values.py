"""Config bridge: how the UI's flat list of tunable-control values maps onto
MoshConfig fields.

The tunable controls are always ordered [*float sliders, *range lo/hi slider
pairs] in field declaration order (layout.py builds them from the same FLOATS /
RANGES lists). values_to_mapping() / mapping_to_values() are the ONLY places that
ordering is spelled out -- presets, Randomize, and the Mosh click all go through
them, so the packing can never drift between handlers.
"""

import random

from datamosh import MoshConfig, from_mapping, presets
from datamosh.config import float_fields, range_fields

DEFAULTS = MoshConfig()
FLOATS = float_fields()
RANGES = range_fields()


def label(name):
    return name.replace("_", " ")


def values_to_mapping(values, *, sort_ranges=False):
    """Flat control values -> {field name: value} (floats coerced, ranges to tuples).

    sort_ranges swaps a (lo, hi) pair the user dragged inverted; presets keep the
    raw pair so saving round-trips exactly what the sliders show.
    """
    mapping = {}
    i = 0
    for f in FLOATS:
        mapping[f.name] = float(values[i])
        i += 1
    for f in RANGES:
        lo, hi = int(values[i]), int(values[i + 1])
        i += 2
        mapping[f.name] = (min(lo, hi), max(lo, hi)) if sort_ranges else (lo, hi)
    return mapping


def mapping_to_values(mapping):
    """{field name: value} -> flat control values (defaults fill missing fields)."""
    out = [mapping.get(f.name, getattr(DEFAULTS, f.name)) for f in FLOATS]
    for f in RANGES:
        lo, hi = mapping.get(f.name, getattr(DEFAULTS, f.name))
        out += [lo, hi]
    return out


def render_config(source, output, n, seed, min_shot, values):
    """The MoshConfig for one Mosh click, via the same from_mapping() coercion the
    CLI and presets use."""
    base = MoshConfig(
        source=source,
        output=output,
        n=int(n),
        seed=seed,
        min_shot=float(min_shot),
        reset=True,
        fixup=False,
    )
    return from_mapping(values_to_mapping(values, sort_ranges=True), base=base)


def preset_names():
    return list(presets.all_presets())


def preset_values(name):
    """[n, min_shot, *tunable values] for a preset (defaults fill gaps)."""
    p = presets.all_presets().get(name, {})
    return [
        p.get("n", DEFAULTS.n),
        p.get("min_shot", DEFAULTS.min_shot),
        *mapping_to_values(p),
    ]


def save_preset_values(name, n, min_shot, values):
    """Persist the current control values as a named preset in output/user_presets.json."""
    mapping = {"n": int(n), "min_shot": float(min_shot), **values_to_mapping(values)}
    presets.save_user_preset(name, mapping)


def random_values():
    """Random flat control values for the Randomize button.

    The ranges here are curated taste, deliberately narrower than the fields'
    slider bounds (e.g. escalate rolls 0-3 on a 0-6 slider) -- don't derive them
    from the field metadata.
    """
    out = []
    for f in FLOATS:
        if f.name == "short_gop_bias":
            out.append(round(random.uniform(-1, 3), 2))
        elif f.name == "escalate":
            out.append(round(random.uniform(0, 3), 2))
        elif f.name.endswith("frac"):
            out.append(round(random.uniform(0, 0.5), 2))
        else:
            out.append(round(random.uniform(0, 1), 2))
    for f in RANGES:
        lo_b, hi_b = int(f.metadata["lo"]), int(f.metadata["hi"])
        a = random.randint(lo_b, max(lo_b, hi_b // 3))
        b = random.randint(a, min(hi_b, a + hi_b // 2 + 2))
        out += [a, b]
    return out
