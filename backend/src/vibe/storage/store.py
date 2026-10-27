"""Project storage on disk: one folder per project, videos stored once, a shared analysis cache.

Layout (docs/ARCHITECTURE.md, "Storage"; decision D-014)::

    <data dir>/
      videos/<sha256>.<ext>                   each uploaded video, stored once
      cache/<sha256>/<kind>/<key>.json        AI analysis per video (tracks, transcript...); F-08
      projects/<project_id>/
        project.json                          ProjectInfo
        timeline.jsonl                        the op log (contract file format)
        tracks.user.json                      tracks drawn or corrected by hand
        exports/                              rendered files

Writes are atomic (temp file + rename), and an op is appended as one line, so a crash never
leaves a half-written file: at worst it loses an incomplete last line of the log.
"""

import hashlib
import logging
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field, TypeAdapter

from vibe import config
from vibe.contract import OP_ADAPTER, Op, OpLog, TimelineState, Track, new_id
from vibe.contract.common import Model

log = logging.getLogger(__name__)

STORAGE_VERSION = 1
"""Version of the folder layout and ``project.json``. Bump it on a breaking change."""

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")
_TRACKS = TypeAdapter(list[Track])


class StorageError(Exception):
    """A missing project, a bad name, or a file that can't be read."""


class VideoRef(Model):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ext: str = Field(pattern=r"^(\.[a-z0-9]{1,10})?$", description='e.g. ".mp4"; may be empty.')
    original_name: str

    @property
    def filename(self) -> str:
        return f"{self.sha256}{self.ext}"


class ProjectInfo(Model):
    storage_version: int = STORAGE_VERSION
    project_id: str
    name: str
    created_at: datetime
    video: VideoRef


def _safe(name: str, what: str) -> str:
    """Reject names that could escape their folder (``..``, slashes) or are empty."""
    if not _SAFE_NAME.match(name) or ".." in name:
        raise StorageError(f"invalid {what} {name!r}: use letters, digits and . _ @ -")
    return name


def _atomic_write(path: Path, text: str) -> None:
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


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Project:
    """One project folder. Reads go to disk every time, so two handles never disagree."""

    def __init__(self, root: Path, info: ProjectInfo) -> None:
        self.dir = root
        self.info = info

    @property
    def id(self) -> str:
        return self.info.project_id

    @property
    def timeline_path(self) -> Path:
        return self.dir / "timeline.jsonl"

    @property
    def user_tracks_path(self) -> Path:
        return self.dir / "tracks.user.json"

    @property
    def exports_dir(self) -> Path:
        return self.dir / "exports"

    # --- The op log -----------------------------------------------------------------------

    def log(self) -> OpLog:
        text = self.timeline_path.read_text(encoding="utf-8")
        if text and not text.endswith("\n"):
            # A crash mid-append leaves an incomplete last line: drop it, keep the rest.
            text, _, partial = text.rpartition("\n")
            log.warning("%s: dropped an incomplete last line: %.80s", self.timeline_path, partial)
            text += "\n"
        return OpLog.from_jsonl(text)

    def append(self, op: Op) -> Op:
        """Validate ``op`` against the whole log, then append it as one line.

        Raises ``ContractError`` (and writes nothing) if the op breaks a contract rule. The
        caller must apply one request at a time per project (the contract needs a request's
        ops to be contiguous).
        """
        self.log().append(op)  # validates; raises before anything is written
        line = OP_ADAPTER.dump_json(op, exclude_none=True).decode() + "\n"
        with self.timeline_path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        return op

    def state(self) -> TimelineState:
        return self.log().state()

    # --- Tracks drawn or corrected by hand (edits, so they live with the project) ---------

    def user_tracks(self) -> list[Track]:
        if not self.user_tracks_path.exists():
            return []
        return _TRACKS.validate_json(self.user_tracks_path.read_bytes())

    def save_user_tracks(self, tracks: list[Track]) -> None:
        _TRACKS.validate_python(tracks)
        _atomic_write(self.user_tracks_path, _TRACKS.dump_json(tracks, indent=2).decode() + "\n")


class Storage:
    """Everything under one data directory (``VIBE_DATA_DIR``, default ``<repo>/data``)."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else config.data_dir()

    @property
    def videos_dir(self) -> Path:
        return self.root / "videos"

    @property
    def projects_dir(self) -> Path:
        return self.root / "projects"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    # --- Videos ---------------------------------------------------------------------------

    def add_video(self, source: Path) -> VideoRef:
        """Store a video by content hash (once, however many projects use it)."""
        source = Path(source)
        if not source.is_file():
            raise StorageError(f"no such video file: {source}")
        ref = VideoRef(sha256=_sha256(source), ext=source.suffix.lower(), original_name=source.name)
        target = self.video_path(ref)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".upload.", suffix=".tmp")
            os.close(fd)
            try:
                shutil.copyfile(source, tmp)
                os.replace(tmp, target)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        return ref

    def video_path(self, ref: VideoRef) -> Path:
        return self.videos_dir / ref.filename

    # --- Projects -------------------------------------------------------------------------

    def create_project(self, video: Path, name: str | None = None) -> Project:
        ref = self.add_video(video)
        info = ProjectInfo(
            project_id=new_id("prj"),
            name=name or Path(video).stem,
            created_at=datetime.now(UTC),
            video=ref,
        )
        root = self.projects_dir / info.project_id
        root.mkdir(parents=True)
        (root / "exports").mkdir()
        _atomic_write(root / "timeline.jsonl", OpLog().to_jsonl())
        _atomic_write(root / "project.json", info.model_dump_json(indent=2) + "\n")
        return Project(root, info)

    def open_project(self, project_id: str) -> Project:
        root = self.projects_dir / _safe(project_id, "project id")
        info_path = root / "project.json"
        if not info_path.exists():
            raise StorageError(f"no project {project_id!r} in {self.projects_dir}")
        info = ProjectInfo.model_validate_json(info_path.read_bytes())
        if info.storage_version != STORAGE_VERSION:
            raise StorageError(
                f"project {project_id!r} has storage_version {info.storage_version}; "
                f"this code reads {STORAGE_VERSION}"
            )
        return Project(root, info)

    def list_projects(self) -> list[ProjectInfo]:
        """Every readable project, newest first. Unreadable folders are skipped with a warning."""
        infos = []
        if self.projects_dir.exists():
            for path in self.projects_dir.glob("*/project.json"):
                try:
                    infos.append(ProjectInfo.model_validate_json(path.read_bytes()))
                except ValueError as e:
                    log.warning("skipping %s: %s", path, e)
        return sorted(infos, key=lambda i: i.created_at, reverse=True)

    # --- Analysis cache (what goes in it, and its keys, is F-08's call) --------------------

    def cache_path(self, video_sha256: str, kind: str, key: str) -> Path:
        """``cache/<sha256>/<kind>/<key>.json``.

        ``kind`` is the type of analysis (``tracks``, ``transcript``, ``speakers``...). ``key``
        must change whenever the result would, e.g. ``yoloe-11s@1-3f9a1c`` (model, version,
        settings hash), so switching models never serves stale results.
        """
        VideoRef(sha256=video_sha256, ext="", original_name="")  # validates the hash
        return (
            self.cache_dir
            / video_sha256
            / _safe(kind, "cache kind")
            / f"{_safe(key, 'cache key')}.json"
        )
