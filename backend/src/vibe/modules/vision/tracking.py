"""Linking detections across frames into contract tracks (F-10).

A tracker gets one sampled frame at a time (``update``) and, at the end, returns ``Track``s with
one keyframe per detection. Between keyframes the renderer and the benchmark interpolate, so a
face missed for a few frames is still covered as long as its track continues.

Privacy fails closed here too: faces and plates are kept at a much lower confidence than other
labels, low-confidence keyframes are marked ``uncertain`` (masked with extra padding and flagged
for review) instead of dropped, and a detection the tracker can't link starts its own track.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from pydantic import Field

from vibe.contract import Keyframe, Track
from vibe.contract.common import Model
from vibe.contract.tracks import KeyframeState
from vibe.modules.vision.detection import Box, Detection, NotInstalled, iou, normalize_box

PRIVACY_LABELS = frozenset({"face", "license_plate"})
"""Labels where a miss leaks something private, so they're kept at ``min_conf_privacy``."""


class TrackerSettings(Model):
    """Everything that changes which tracks come out. It's hashed into the cache key.

    The defaults are starting points to tune with the F-09 benchmark, not measured values.
    """

    min_conf: float = Field(default=0.3, ge=0, le=1, description="Drop weaker detections.")
    min_conf_privacy: float = Field(
        default=0.1, ge=0, le=1, description="The same for faces and plates (fail closed)."
    )
    uncertain_below: float = Field(
        default=0.5, ge=0, le=1, description="Keyframes under this are marked uncertain."
    )
    iou_match: float = Field(
        default=0.3, ge=0, le=1, description="Overlap needed to continue a track."
    )
    max_gap: float = Field(
        default=1.0, gt=0, description="Seconds a track may go undetected and still continue."
    )

    def keeps(self, d: Detection) -> bool:
        floor = self.min_conf_privacy if d.label in PRIVACY_LABELS else self.min_conf
        return d.conf >= floor

    def state(self, conf: float) -> KeyframeState:
        return "detected" if conf >= self.uncertain_below else "uncertain"


class Tracker(Protocol):
    name: str
    settings: TrackerSettings

    def update(self, t: float, detections: Sequence[Detection], size: tuple[int, int]) -> None:
        """The detections of the frame at ``t`` seconds (increasing), ``size`` = (width, height)."""
        ...

    def tracks(self) -> list[Track]: ...


class _TrackBuilder:
    """Collects keyframes per track and numbers tracks ``trk_1``, ``trk_2``... by first sighting,
    so the same video and settings always give the same ids."""

    def __init__(self, settings: TrackerSettings) -> None:
        self._settings = settings
        self._labels: dict[int, str] = {}
        self._keyframes: dict[int, list[Keyframe]] = defaultdict(list)

    def new(self, label: str) -> int:
        key = len(self._labels) + 1
        self._labels[key] = label
        return key

    def add(self, key: int, t: float, d: Detection) -> None:
        self._keyframes[key].append(
            Keyframe(t=t, box=d.box, conf=round(d.conf, 4), state=self._settings.state(d.conf))
        )

    def tracks(self) -> list[Track]:
        return [
            Track(
                track_id=f"trk_{key}",
                label=label,
                source="detector",
                keyframes=self._keyframes[key],
            )
            for key, label in self._labels.items()
        ]


def _check_time(last: float | None, t: float) -> None:
    if last is not None and t <= last:
        raise ValueError(f"frames must come in time order: {t} after {last}")


@dataclass
class _Live:
    key: int
    label: str
    box: Box
    t: float
    velocity: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    """Box change per second, from the last two keyframes."""

    def predict(self, t: float) -> Box:
        dt = t - self.t
        x1, y1, x2, y2 = (c + v * dt for c, v in zip(self.box, self.velocity, strict=True))
        return (x1, y1, x2, y2)

    def move(self, t: float, box: Box) -> None:
        dt = t - self.t
        self.velocity = tuple((n - o) / dt for n, o in zip(box, self.box, strict=True))  # type: ignore[assignment]
        self.box, self.t = box, t


class IouTracker:
    """Greedy overlap matching with a constant-velocity guess. No packages, fully deterministic.

    The baseline every other tracker is compared with, and what tests and the stub use. Each
    label is tracked separately (a face never continues a person's track).
    """

    name = "iou"

    def __init__(self, settings: TrackerSettings | None = None) -> None:
        self.settings = settings or TrackerSettings()
        self._builder = _TrackBuilder(self.settings)
        self._live: list[_Live] = []
        self._last_t: float | None = None

    def update(self, t: float, detections: Sequence[Detection], size: tuple[int, int]) -> None:
        _check_time(self._last_t, t)
        self._last_t = t
        s = self.settings
        self._live = [lv for lv in self._live if t - lv.t <= s.max_gap]
        kept = [d for d in detections if s.keeps(d)]

        pairs = []
        for li, lv in enumerate(self._live):
            guess = lv.predict(t)
            for di, d in enumerate(kept):
                if d.label == lv.label and (overlap := iou(guess, d.box)) >= s.iou_match:
                    pairs.append((overlap, li, di))
        used_live: set[int] = set()
        used_det: set[int] = set()
        for _, li, di in sorted(pairs, key=lambda p: (-p[0], p[1], p[2])):
            if li in used_live or di in used_det:
                continue
            used_live.add(li)
            used_det.add(di)
            lv, d = self._live[li], kept[di]
            lv.move(t, d.box)
            self._builder.add(lv.key, t, d)

        for di, d in enumerate(kept):
            if di not in used_det:
                lv = _Live(key=self._builder.new(d.label), label=d.label, box=d.box, t=t)
                self._live.append(lv)
                self._builder.add(lv.key, t, d)

    def tracks(self) -> list[Track]:
        return self._builder.tracks()


class ByteTracker:
    """ByteTrack from Roboflow ``trackers`` (Apache 2.0; D-015), one instance per label.

    **Not run in CI**, and written against the ``trackers`` 2.x API without being executed here:
    check it against the installed version before trusting its numbers. Install with
    ``uv pip install trackers supervision``. Not BoxMOT: that one is AGPL.
    """

    name = "bytetrack"

    def __init__(self, settings: TrackerSettings | None = None) -> None:
        try:
            import supervision as sv
            from trackers import ByteTrackTracker
        except ImportError as e:
            raise NotInstalled(
                f"the bytetrack tracker needs `uv pip install trackers supervision` ({e})"
            ) from e
        self._sv = sv
        self._make = ByteTrackTracker
        self.settings = settings or TrackerSettings()
        self._builder = _TrackBuilder(self.settings)
        self._per_label: dict[str, Any] = {}
        self._keys: dict[tuple[str, int], int] = {}
        self._last_t: float | None = None

    def update(self, t: float, detections: Sequence[Detection], size: tuple[int, int]) -> None:
        _check_time(self._last_t, t)
        self._last_t = t
        sv, (width, height) = self._sv, size
        by_label: dict[str, list[Detection]] = defaultdict(list)
        for d in detections:
            if self.settings.keeps(d):
                by_label[d.label].append(d)
        # Labels seen before get an empty update too, so their tracks age like ByteTrack expects.
        for label in sorted(set(by_label) | set(self._per_label)):
            dets = by_label.get(label, [])
            tracker = self._per_label.setdefault(label, self._make())
            if dets:
                xyxy = np.array(
                    [[x1 * width, y1 * height, x2 * width, y2 * height] for x1, y1, x2, y2 in
                     (d.box for d in dets)],
                    dtype=np.float32,
                )  # fmt: skip
                inputs = sv.Detections(
                    xyxy=xyxy,
                    confidence=np.array([d.conf for d in dets], dtype=np.float32),
                    class_id=np.zeros(len(dets), dtype=int),
                )
            else:
                inputs = sv.Detections.empty()
            out = tracker.update(inputs)
            self._collect(label, t, dets, out, width, height)

    def _collect(
        self, label: str, t: float, dets: list[Detection], out: Any, width: int, height: int
    ) -> None:
        returned: list[Box] = []
        ids = getattr(out, "tracker_id", None)
        for i in range(len(out)):
            tid = None if ids is None else int(ids[i])
            box = normalize_box(*out.xyxy[i], width, height)
            if tid is None or tid < 0 or box is None:
                continue
            conf = float(out.confidence[i]) if out.confidence is not None else 0.0
            key = self._keys.get((label, tid))
            if key is None:
                key = self._keys[(label, tid)] = self._builder.new(label)
            self._builder.add(key, t, Detection(label, box, conf))
            returned.append(box)
        # Fail closed: a kept detection ByteTrack didn't confirm still gets its own track.
        for d in dets:
            if not any(iou(d.box, b) >= 0.5 for b in returned):
                self._builder.add(self._builder.new(label), t, d)

    def tracks(self) -> list[Track]:
        return self._builder.tracks()


TRACKERS: dict[str, type[IouTracker] | type[ByteTracker]] = {
    "iou": IouTracker,
    "bytetrack": ByteTracker,
}
