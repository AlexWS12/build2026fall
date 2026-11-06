"""Benchmark harness (F-09): consented clips, ground-truth labels, scores, and the report.

Every quality or speed number the team quotes comes from a script, with the hardware stated
(CLAUDE.md, "Honesty"). This package is that script for vision: which clips we measure on
(``manifest``), what's really in them (``labels``), how a model's tracks compare (``score``), and
the table that puts it together (``report``, run as ``python -m vibe.benchmark report``).

The planner is scored separately by R-1's planner bench on the shared request set.
"""

from vibe.benchmark.labels import ClipLabels, SpeechSegment, check_labels, load_labels
from vibe.benchmark.manifest import (
    BenchmarkError,
    ClipEntry,
    ClipManifest,
    add_clip,
    clip_status,
    load_manifest,
)
from vibe.benchmark.report import Report, build_report, render_markdown, render_text
from vibe.benchmark.score import LabelScore, TrackScore, score_tracks

__all__ = [
    "BenchmarkError",
    "ClipEntry",
    "ClipLabels",
    "ClipManifest",
    "LabelScore",
    "Report",
    "SpeechSegment",
    "TrackScore",
    "add_clip",
    "build_report",
    "check_labels",
    "clip_status",
    "load_labels",
    "load_manifest",
    "render_markdown",
    "render_text",
    "score_tracks",
]
