"""Where analysis results live: the shared per-video cache (docs/ARCHITECTURE.md, "Storage").

Two kinds per video, each one JSON file per cache key:

- ``cache/<sha256>/detections/<key>.json``: a ``DetectionsFile``, what the detectors saw;
- ``cache/<sha256>/tracks/<key>.json``: a JSON list of contract ``Track``s, the shape the F-09
  benchmark scores, so ``python -m vibe.benchmark report`` compares every cached key side by side.

A key names the detectors and the stride and ends with a hash of every setting, so changing a
model, a threshold or the tracker never serves a stale result (D-014).
"""

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter

from vibe.contract import Track
from vibe.modules.vision.analyze import DetectionsFile
from vibe.modules.vision.detection import Detector
from vibe.modules.vision.tracking import TrackerSettings
from vibe.storage import Project, Storage

_TRACKS = TypeAdapter(list[Track])
_UNSAFE = re.compile(r"[^A-Za-z0-9._@-]+")
_MAX_KEY = 128  # Storage allows 1-128 letters, digits and . _ @ -


class AmbiguousAnalysis(Exception):
    """Several track sets are cached for a video and the caller didn't say which one to use."""


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:8]


def _key(readable: str, value: object) -> str:
    digest = _digest(value)
    readable = _UNSAFE.sub("-", readable).strip("-._@") or "x"
    return f"{readable[: _MAX_KEY - len(digest) - 1]}-{digest}"


def detections_key(detectors: Sequence[Detector], every: int) -> str:
    """E.g. ``rfdetr-l_egoblur-face-k1-3f9a1c2e``."""
    names = [d.name for d in detectors]
    settings = {d.name: dict(d.settings) for d in detectors}
    return _key(f"{'_'.join(names)}-k{every}", {"detectors": settings, "every": every})


def tracks_key(detections_key: str, tracker: str, settings: TrackerSettings) -> str:
    """E.g. ``rfdetr-l_egoblur-face-k1-3f9a1c2e-iou-7b0d4e11``."""
    return _key(f"{detections_key}-{tracker}", {"tracker": tracker, **settings.model_dump()})


def _write(path: Path, text: str) -> None:
    """Temp file, fsync, rename (same as storage): a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_detections(storage: Storage, sha256: str, key: str, detections: DetectionsFile) -> Path:
    path = storage.cache_path(sha256, "detections", key)
    _write(path, detections.model_dump_json() + "\n")
    return path


def load_detections(storage: Storage, sha256: str, key: str) -> DetectionsFile:
    return DetectionsFile.model_validate_json(
        storage.cache_path(sha256, "detections", key).read_bytes()
    )


def namespaced(tracks: list[Track], key: str) -> list[Track]:
    """``trk_3`` becomes ``trk_<key hash>_3``.

    Ops name tracks by id, so ids from two analyses must never collide: otherwise re-running with
    another model would silently move a blur onto whatever is ``trk_3`` now. With the key's hash
    in the id, an edit made on one analysis names ids the other one doesn't have, which shows up
    as a missing target instead of a wrong one. It also keeps them apart from hand-drawn tracks.
    """
    digest = key.rsplit("-", 1)[-1]
    return [
        t.model_copy(update={"track_id": f"trk_{digest}_{t.track_id.removeprefix('trk_')}"})
        for t in tracks
    ]


def save_tracks(storage: Storage, sha256: str, key: str, tracks: list[Track]) -> list[Track]:
    """Write ``tracks`` under ``key``, with ids namespaced by the key. Returns what was written."""
    tracks = namespaced(tracks, key)
    path = storage.cache_path(sha256, "tracks", key)
    _write(path, _TRACKS.dump_json(tracks, exclude_none=True).decode() + "\n")
    return tracks


def _keys(storage: Storage, sha256: str, kind: str) -> list[str]:
    folder = storage.cache_path(sha256, kind, "x").parent
    return sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []


def detection_keys(storage: Storage, sha256: str) -> list[str]:
    return _keys(storage, sha256, "detections")


def track_keys(storage: Storage, sha256: str) -> list[str]:
    return _keys(storage, sha256, "tracks")


def load_tracks(storage: Storage, sha256: str, key: str | None = None) -> list[Track] | None:
    """The cached tracks for a video, or ``None`` if it was never analysed.

    ``None`` is not ``[]``: the toolbox fails "blur all faces" on ``None`` instead of masking
    nothing (privacy fails closed). With no ``key``, the one cached set is used; if there are
    several (e.g. during a model comparison) the caller must pick, so it raises instead of
    guessing.
    """
    if key is None:
        keys = track_keys(storage, sha256)
        if not keys:
            return None
        if len(keys) > 1:
            raise AmbiguousAnalysis(
                f"{len(keys)} track sets are cached for video {sha256[:12]}; pick one of: "
                + ", ".join(keys)
            )
        key = keys[0]
    path = storage.cache_path(sha256, "tracks", key)
    if not path.exists():
        return None
    return _TRACKS.validate_json(path.read_bytes())


def project_tracks(
    storage: Storage, project: Project, key: str | None = None
) -> list[Track] | None:
    """What the toolbox should get as ``ToolContext.tracks`` for a project: the video's cached
    detector tracks plus the project's hand-drawn ones.

    Still ``None`` when the video was never analysed, even if boxes were drawn by hand: a few
    drawn boxes must not make "blur every face" look done.
    """
    tracks = load_tracks(storage, project.info.video.sha256, key)
    if tracks is None:
        return None
    return [*tracks, *project.user_tracks()]
