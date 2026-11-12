"""Run detectors over a video and link what they find into tracks (F-10).

``analyze_video`` reads every ``every``-th frame, runs each detector on it, and feeds the boxes to
a tracker. It returns the raw detections too, so the tracker can be swapped or retuned later
from the cache (``retrack``) without running a model again.
"""

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import Field

from vibe.contract import Track
from vibe.contract.common import Model
from vibe.modules.vision.detection import Box, Detection, Detector
from vibe.modules.vision.tracking import Tracker
from vibe.video import Frame, VideoReader


class CachedDetection(Model):
    label: str
    box: Box
    conf: float = Field(ge=0, le=1)


class FrameDetections(Model):
    t: float = Field(ge=0, description="Source time in seconds.")
    detections: list[CachedDetection]


class DetectionsFile(Model):
    """What every detector saw on every sampled frame, before any tracking."""

    about: str = "Raw detections per sampled frame (F-10). Tracks are built from these."
    detectors: list[str]
    settings: dict[str, dict[str, object]] = Field(
        description="Each detector's settings (threshold...), by detector name."
    )
    every: int = Field(ge=1, description="Every how many frames a frame was sampled.")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frames: list[FrameDetections]


@dataclass
class Analysis:
    detections: DetectionsFile
    tracks: list[Track]
    seconds: dict[str, float] = field(default_factory=dict)
    """Wall time per stage: ``decode``, each detector's name, ``track``. Not a benchmark: the
    F-09/F-50 scripts own quoted numbers."""

    @property
    def frames(self) -> int:
        return len(self.detections.frames)


def analyze_frames(
    frames: Iterable[Frame],
    detectors: Sequence[Detector],
    tracker: Tracker,
    every: int = 1,
) -> Analysis:
    """The pipeline on frames already decoded (and already sampled every ``every`` frames)."""
    if not detectors:
        raise ValueError("analyze needs at least one detector")
    names = [d.name for d in detectors]
    if len(set(names)) != len(names):
        raise ValueError(f"detector names must differ, got {names}")
    seconds = dict.fromkeys(["decode", *names, "track"], 0.0)
    sampled: list[FrameDetections] = []
    size: tuple[int, int] | None = None
    it = iter(frames)
    while True:
        start = time.perf_counter()
        frame = next(it, None)
        seconds["decode"] += time.perf_counter() - start
        if frame is None:
            break
        height, width = frame.image.shape[:2]
        size = (width, height)
        found: list[Detection] = []
        for det in detectors:
            start = time.perf_counter()
            found.extend(det.detect(frame.image))
            seconds[det.name] += time.perf_counter() - start
        start = time.perf_counter()
        tracker.update(frame.time, found, size)
        seconds["track"] += time.perf_counter() - start
        sampled.append(
            FrameDetections(
                t=frame.time,
                detections=[CachedDetection(label=d.label, box=d.box, conf=d.conf) for d in found],
            )
        )
    if size is None:
        raise ValueError("the video has no frames to analyze")
    detections = DetectionsFile(
        detectors=names,
        settings={d.name: dict(d.settings) for d in detectors},
        every=every,
        width=size[0],
        height=size[1],
        frames=sampled,
    )
    return Analysis(detections=detections, tracks=tracker.tracks(), seconds=seconds)


def analyze_video(
    path: str | Path, detectors: Sequence[Detector], tracker: Tracker, every: int = 1
) -> Analysis:
    """Detect and track in a video file, on frames 0, ``every``, 2 * ``every``..."""
    if every < 1:
        raise ValueError(f"every must be at least 1, got {every}")
    with VideoReader(path) as reader:
        return analyze_frames(reader.iter_frames(step=every), detectors, tracker, every)


def retrack(detections: DetectionsFile, tracker: Tracker) -> list[Track]:
    """Tracks from cached detections: try another tracker or settings with no model and no GPU."""
    size = (detections.width, detections.height)
    for f in detections.frames:
        tracker.update(f.t, [Detection(d.label, d.box, d.conf) for d in f.detections], size)
    return tracker.tracks()
