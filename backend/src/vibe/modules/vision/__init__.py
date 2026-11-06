"""Vision: find and track people, faces, plates and named objects, cached per video (F-10).

    frames ──► detectors (one RGB frame each) ──► tracker ──► contract Tracks ──► cache
                                                         toolbox, renderer, F-09 ◄──┘

- ``detection``: ``Detection`` and the ``Detector`` interface every model implements.
- ``detectors``: the models, by name (``DETECTORS``, ``make_detector``), with their licenses.
- ``tracking``: trackers that link detections into tracks (``iou`` built in, ``bytetrack``).
- ``analyze``: the pipeline over a video, and ``retrack`` from cached detections.
- ``cache``: reading and writing ``cache/<sha256>/{detections,tracks}/<key>.json``, the video's
  default tracks, and ``project_tracks`` for the toolbox's ``ToolContext.tracks``.

CLI: ``uv run python -m vibe.modules.vision --help``.

Model packages (torch, rfdetr, ultralytics, opencv, trackers) are **not** project dependencies
yet: each detector imports its own when built and says how to install it if it's missing, so
CI and CPU laptops run everything here on the stub. Adding them to ``pyproject.toml`` as an
optional group is a shared-file change for after the F-10 comparison (HANDOFF.md).
"""

from vibe.modules.vision.analyze import Analysis, analyze_frames, analyze_video, retrack
from vibe.modules.vision.cache import (
    AmbiguousAnalysis,
    CacheError,
    load_tracks,
    project_tracks,
    save_detections,
    save_tracks,
    set_default_tracks,
)
from vibe.modules.vision.detection import Detection, Detector, NotInstalled
from vibe.modules.vision.detectors import DETECTORS, make_detector
from vibe.modules.vision.tracking import TRACKERS, IouTracker, TrackerSettings

__all__ = [
    "DETECTORS",
    "TRACKERS",
    "AmbiguousAnalysis",
    "Analysis",
    "CacheError",
    "Detection",
    "Detector",
    "IouTracker",
    "NotInstalled",
    "TrackerSettings",
    "analyze_frames",
    "analyze_video",
    "load_tracks",
    "make_detector",
    "project_tracks",
    "retrack",
    "save_detections",
    "save_tracks",
    "set_default_tracks",
]
