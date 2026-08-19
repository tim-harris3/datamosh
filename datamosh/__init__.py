"""datamosh -- shot-based datamoshing toolkit.

Quick start (see recipes/ for copy-and-tweak examples):

    from datamosh import MoshConfig, run_mosh
    cfg = MoshConfig(source="media/sample.avi", output="output/mine.avi",
                     n=12, seed=7, keyframe_delete_prob=0.8)
    run_mosh(cfg)

Everything importable from the submodules is re-exported here, so recipes never
need to know which module a function lives in.
"""

from . import cli, ffmpeg, paths, presets, sections
from .avi import parse_avi, write_avi
from .chroma import CHROMA_MODES, chroma_databend
from .config import (
    MoshConfig,
    describe,
    escalation_intensity,
    float_fields,
    from_mapping,
    range_fields,
    tunable_fields,
)
from .effects import (
    databend_blob,
    delete_keyframe,
    drop_frames,
    dup_frame,
    interleave,
    mangle_audio,
    mosh_segment,
    mosh_video,
    reorder_pframes,
    stretch_audio,
    transplant_pframes,
)
from .pipeline import run_mosh
from .pixelsort import PIXELSORT_KEYS, PIXELSORT_MODES, pixel_sort
from .scenes import (
    DEFAULT_AUDIO_VIDEO_RATIO,
    audio_video_ratio,
    bounds_to_shots,
    build_scene_map,
    detect_scene_cuts,
    extract_shot,
)
from .script import (
    AudioDatabend,
    AudioReverse,
    AudioScramble,
    ClassicMosh,
    Databend,
    DeleteKeyframe,
    DropFrames,
    DupFrames,
    Entry,
    FrameQuota,
    MoshScript,
    Reorder,
    Transplant,
    run_script,
)
from .sections import (
    delete_tagged_keyframes,
    example_section_pool,
    keyframe_shots,
    make_moshable,
    mosh_pass,
    replace_sections,
    split_sections,
)

__all__ = [
    "MoshConfig",
    "run_mosh",
    "describe",
    "escalation_intensity",
    "from_mapping",
    "tunable_fields",
    "float_fields",
    "range_fields",
    "parse_avi",
    "write_avi",
    "chroma_databend",
    "CHROMA_MODES",
    "pixel_sort",
    "PIXELSORT_MODES",
    "PIXELSORT_KEYS",
    "mosh_segment",
    "mosh_video",
    "mangle_audio",
    "stretch_audio",
    "databend_blob",
    "delete_keyframe",
    "drop_frames",
    "dup_frame",
    "interleave",
    "reorder_pframes",
    "transplant_pframes",
    "MoshScript",
    "Entry",
    "run_script",
    "ClassicMosh",
    "DeleteKeyframe",
    "DupFrames",
    "DropFrames",
    "Databend",
    "Reorder",
    "Transplant",
    "FrameQuota",
    "AudioReverse",
    "AudioScramble",
    "AudioDatabend",
    "bounds_to_shots",
    "build_scene_map",
    "detect_scene_cuts",
    "extract_shot",
    "audio_video_ratio",
    "DEFAULT_AUDIO_VIDEO_RATIO",
    "make_moshable",
    "keyframe_shots",
    "split_sections",
    "mosh_pass",
    "example_section_pool",
    "replace_sections",
    "delete_tagged_keyframes",
    "cli",
    "ffmpeg",
    "paths",
    "presets",
    "sections",
]
