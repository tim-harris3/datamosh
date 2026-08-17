"""datamosh -- shot-based datamoshing toolkit.

Quick start (see recipes/ for copy-and-tweak examples):

    from datamosh import MoshConfig, run_mosh
    cfg = MoshConfig(source="media/truck.AVI", output="output/mine.avi",
                     n=12, seed=7, keyframe_delete_prob=0.8)
    run_mosh(cfg)

Everything importable from the submodules is re-exported here, so recipes never
need to know which module a function lives in.
"""

from . import cli, ffmpeg, paths, presets, sections
from .avi import parse_avi, write_avi
from .chroma import CHROMA_MODES, chroma_databend
from .config import (MoshConfig, describe, from_mapping, tunable_fields,
                     float_fields, range_fields)
from .effects import mangle_audio, mosh_segment, stretch_audio
from .pipeline import run_mosh
from .pixelsort import PIXELSORT_KEYS, PIXELSORT_MODES, pixel_sort
from .scenes import (DEFAULT_AUDIO_VIDEO_RATIO, audio_video_ratio,
                     build_scene_map, detect_scene_cuts, extract_shot)
from .sections import (delete_tagged_keyframes, example_section_pool,
                       keyframe_shots, make_moshable, mosh_pass,
                       replace_sections, split_sections)

__all__ = [
    "MoshConfig", "run_mosh", "describe", "from_mapping", "tunable_fields",
    "float_fields", "range_fields",
    "parse_avi", "write_avi",
    "chroma_databend", "CHROMA_MODES",
    "pixel_sort", "PIXELSORT_MODES", "PIXELSORT_KEYS",
    "mosh_segment", "mangle_audio", "stretch_audio",
    "build_scene_map", "detect_scene_cuts", "extract_shot", "audio_video_ratio",
    "DEFAULT_AUDIO_VIDEO_RATIO",
    "make_moshable", "keyframe_shots", "split_sections", "mosh_pass",
    "example_section_pool", "replace_sections", "delete_tagged_keyframes",
    "cli", "ffmpeg", "paths", "presets", "sections",
]
