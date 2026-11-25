"""Analyse a stored video and cache the result, for the CLI and for the app (F-10).

    video ──► detectors ──► tracker ──► cache/<sha256>/{detections,tracks}/<key>.json

``analyse_and_store`` is the pipeline the ``analyze`` command runs. ``analyse_for_app`` is the
same with the detectors and tracker the server is configured with (``VIBE_DETECTORS``,
``VIBE_TRACKER``, ``VIBE_DETECT_EVERY``), for ``POST /api/projects/{id}/analysis``.

It runs on this machine, whatever ``VIBE_BACKEND`` says, except ``stub`` (which never runs a
model). There is no remote vision server yet; with ``remote`` the backend switch still sends the
other modules (the description judge, transcripts) where it says. The footage stays here, so
D-017 has nothing to add.
"""

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from vibe import config
from vibe.contract import Track
from vibe.modules.vision import cache
from vibe.modules.vision.analyze import Analysis, analyze_video
from vibe.modules.vision.detection import Detector
from vibe.modules.vision.detectors import make_detector, parse_spec
from vibe.modules.vision.tracking import TRACKERS, Tracker, TrackerSettings
from vibe.storage import Storage, VideoRef


def make_tracker(spec: str) -> Tracker:
    """A tracker from ``name[:setting=value,...]``, e.g. ``bytetrack:min_conf=0.85``. Raises
    ``ValueError`` for an unknown name or a bad setting, ``NotInstalled`` if its package is
    missing."""
    name, raw = parse_spec(spec)
    cls = TRACKERS.get(name)
    if cls is None:
        raise ValueError(f"unknown tracker {name!r}; known: {', '.join(TRACKERS)}")
    fields = TrackerSettings.model_fields
    unknown = [k for k in raw if k not in fields]
    if unknown:
        raise ValueError(
            f"bad settings for tracker {name!r}: unknown {', '.join(unknown)} "
            f"(settings: {', '.join(fields)})"
        )
    try:
        settings = TrackerSettings(**{k: float(v) for k, v in raw.items()})
    except (ValueError, ValidationError) as e:
        raise ValueError(f"bad settings for tracker {name!r} in {spec!r}: {e}") from None
    return cls(settings)


@dataclass
class Stored:
    detections_key: str
    tracks_key: str
    tracks: list[Track]
    """As cached: track ids namespaced by the key."""
    analysis: Analysis


def analyse_and_store(
    storage: Storage,
    sha256: str,
    video: Path,
    detectors: list[Detector],
    tracker: Tracker,
    every: int = 1,
    *,
    make_default: bool = False,
) -> Stored:
    """Detect and track ``video`` (whose hash is ``sha256``), cache the detections and the tracks,
    and, with ``make_default``, mark the tracks as the ones the app uses for this video."""
    analysis = analyze_video(video, detectors, tracker, every=every)
    det_key = cache.detections_key(detectors, every)
    trk_key = cache.tracks_key(
        det_key, tracker.name, tracker.settings, getattr(tracker, "version", 1)
    )
    cache.save_detections(storage, sha256, det_key, analysis.detections)
    tracks = cache.save_tracks(storage, sha256, trk_key, analysis.tracks)
    if make_default:
        cache.set_default_tracks(storage, sha256, trk_key)
    return Stored(det_key, trk_key, tracks, analysis)


def can_analyse() -> bool:
    """Whether this server is set up to analyse a video itself: a detector is configured and the
    backend isn't ``stub``."""
    return config.model_backend() != "stub" and bool(config.detectors())


def analyse_for_app(storage: Storage, video: VideoRef) -> Stored | None:
    """Analyse a stored video with the configured detectors and tracker, cache it and make it the
    video's default. ``None`` when this server isn't set up to (:func:`can_analyse`).

    Raises ``ValueError`` for bad settings, ``NotInstalled`` for a missing package or weights
    (both before the video is read), and the video reader's errors."""
    if not can_analyse():
        return None
    # Everything is built before any frame is read: a bad spec or missing weights fail fast.
    detectors = [make_detector(spec) for spec in config.detectors()]
    tracker = make_tracker(config.tracker())
    every = config.detect_every()
    return analyse_and_store(
        storage,
        video.sha256,
        storage.video_path(video),
        detectors,
        tracker,
        every,
        make_default=True,
    )
