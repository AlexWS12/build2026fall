"""Vision as an F-08 analysis: detect and track a video under ``stub`` / ``local`` / ``remote``.

This is what makes the app's Analyse button (``POST /api/projects/{id}/analysis``) find targets:
:func:`vibe.modules.backend.run_analysis` replays a cached result, runs the detectors here
(``local``), or asks a model server (``remote``), and caches the tracks under
``cache/<sha256>/tracks/<key>.json``, the same place the CLI writes and the toolbox and renderer
read (``cache.load_tracks``).

The CLI (``python -m vibe.modules.vision analyze``) stays the tool for comparing models: it also
caches raw detections, so ``retrack`` can retune a tracker without a model. An app run keeps only
the tracks.

Model weights are named by their ``models.json`` entry (``weights=@yunet``), not a path, so the
same settings work on every machine and the key covers the pinned weights' sha256.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Self

from vibe import config
from vibe.modules.backend import Analysis, register
from vibe.modules.cache import ModuleError, cache_key
from vibe.modules.vision.analyze import analyze_video
from vibe.modules.vision.cache import namespaced
from vibe.modules.vision.detection import NotInstalled
from vibe.modules.vision.detectors import (
    check_spec,
    finds_people,
    make_detector,
    parse_spec,
    weights_refs,
)
from vibe.modules.vision.stitch import HeadSettings, StitchSettings, postprocess
from vibe.modules.vision.tracking import TRACKERS, TrackerSettings

DEFAULT_EVERY = 1
"""Detect on every frame unless told otherwise. The app's stride is ``config.vision_every()``."""
DEFAULT_TRACKER = "iou"
"""Built in, so it needs no package. ``bytetrack`` needs Roboflow's ``trackers``."""


class VisionNotInstalled(ModuleError):
    """A detector's package or weights aren't on the machine that runs it. The message says
    which and how to get it."""


@register
class VisionAnalysis(Analysis):
    """Detectors + tracker over a whole video. The model server rebuilds it from ``settings``,
    so they match ``__init__``'s arguments exactly."""

    module = "vision"
    kind = "tracks"
    version = 1
    """Bump when the pipeline's output changes in a way the settings don't show."""

    def __init__(
        self,
        detectors: Sequence[str],
        every: int = DEFAULT_EVERY,
        tracker: str = DEFAULT_TRACKER,
        tracker_settings: dict[str, object] | None = None,
        stitch: dict[str, object] | None = None,
        heads: dict[str, object] | None = None,
    ) -> None:
        detectors = [str(d).strip() for d in detectors if str(d).strip()]
        if not detectors:
            raise ValueError("vision analysis needs at least one detector spec")
        for spec in detectors:
            check_spec(spec)  # a typo fails here, before any key or video, with no model import
        if every < 1:
            raise ValueError(f"every must be at least 1, got {every}")
        if tracker not in TRACKERS:
            raise ValueError(f"unknown tracker {tracker!r}; known: {', '.join(sorted(TRACKERS))}")
        settings = TrackerSettings(**(tracker_settings or {}))  # validates before any key exists
        self.stitch = None if stitch is None else StitchSettings(**stitch)
        self.heads = None if heads is None else HeadSettings(**heads)
        post: dict[str, object] = {}  # only when on, so keys made without them stay the same
        if self.stitch is not None:
            post["stitch"] = self.stitch.model_dump()
        if self.heads is not None:
            post["heads"] = self.heads.model_dump()
        super().__init__(
            detectors=detectors,
            every=every,
            tracker=tracker,
            tracker_settings=settings.model_dump(),
            **post,
        )
        self.detectors = detectors
        self.every = every
        self.tracker = tracker
        self.tracker_settings = settings

    @classmethod
    def default(cls) -> Self:
        """What the app runs (D-022): ``VIBE_VISION_DETECTORS`` every ``VIBE_VISION_EVERY`` frames,
        with fragments stitched, and head boxes for faces the face detector missed when a person
        detector runs.
        """
        specs = config.vision_detectors()
        people = any(finds_people(spec) for spec in specs)
        return cls(specs, every=config.vision_every(), stitch={}, heads={} if people else None)

    @property
    def name(self) -> str:
        """E.g. ``vision-yunet-k1-iou``: readable, the digest after it tells settings apart."""
        names = "_".join(parse_spec(d)[0] for d in self.detectors)
        post = ("-stitch" if self.stitch else "") + ("-heads" if self.heads else "")
        return f"vision-{names}-k{self.every}-{self.tracker}{post}"

    @property
    def key(self) -> str:
        # The pinned sha256 of every @weights file is part of the key: new weights must never
        # replay tracks made with the old ones. They come from models.json, so every machine
        # (and the model server) computes the same key from the same settings.
        return cache_key(
            self.name, self.version, {**self.settings, "weights": weights_refs(self.detectors)}
        )

    def compute(self, video_path: Path) -> object:
        try:
            detectors = [make_detector(spec) for spec in self.detectors]
            tracker = TRACKERS[self.tracker](self.tracker_settings)
        except NotInstalled as e:  # a missing package or weights file: say how to get it
            raise VisionNotInstalled(f"vision can't run here: {e}") from e
        result = analyze_video(video_path, detectors, tracker, every=self.every)
        tracks = namespaced(postprocess(result.tracks, self.stitch, self.heads), self.key)
        return [t.model_dump(mode="json", exclude_none=True) for t in tracks]
