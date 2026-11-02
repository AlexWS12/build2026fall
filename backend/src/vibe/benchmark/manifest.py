"""The clip manifest: which consented clips everyone benchmarks on (F-09).

Videos are never committed (``.gitignore``). What's committed is ``clips.json``: one entry per
clip, with its content hash, its metadata, who filmed it and what its consent covers. Each
teammate gets the files from the team drive and registers them with
``python -m vibe.benchmark add-clip FILE``, which checks the hash and stores the video once in
the data dir (``Storage.add_video``), the same place projects keep theirs. So everyone's numbers
come from the same bytes.

Consent (README, "Constraints"; R-4): ``consent`` says how the people in the clip agreed and
where that's recorded. ``consent_cloud`` says whether it also covers sending frames or audio to
a third-party service. It defaults to false, so footage stays on team machines unless someone
said otherwise.
"""

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from fractions import Fraction
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from vibe.contract.common import Model
from vibe.storage import Storage, VideoRef
from vibe.video import VideoError, VideoInfo, probe

HERE = Path(__file__).parent
MANIFEST_PATH = HERE / "clips.json"

MANIFEST_VERSION = 1
"""Version of ``clips.json``. Bump it on a change that breaks a reader or writer."""

CLIP_ID = r"^[a-z0-9][a-z0-9_-]{0,63}$"
SHA256 = r"^[0-9a-f]{64}$"

DURATION_TOLERANCE = 0.05
"""Seconds two probes of the same file may disagree by (containers round timestamps)."""

ABOUT = (
    "Clips the benchmark measures on (F-09). Videos are never committed: get them from the team "
    "drive and run `uv run python -m vibe.benchmark add-clip FILE`, which checks the hash and "
    "stores the video in the data dir. Every clip is our own footage with the people in it "
    "agreeing, or properly licensed. consent_cloud says whether that consent covers third-party "
    "cloud processing (R-4)."
)


class BenchmarkError(Exception):
    """A manifest, label or clip problem the user has to fix. The message says how."""


class ClipEntry(Model):
    clip_id: str = Field(
        pattern=CLIP_ID, description='Short name for labels and reports, e.g. "street-walk".'
    )
    video: VideoRef
    size_bytes: int = Field(gt=0)
    duration: float = Field(gt=0, description="Seconds.")
    width: int = Field(gt=0, description="As displayed, after rotation.")
    height: int = Field(gt=0)
    fps: str = Field(description='Exact frame rate as a fraction, e.g. "30000/1001".')
    has_audio: bool
    source: str = Field(min_length=1, description="Who filmed it, where and when.")
    consent: str = Field(
        min_length=1, description="How the people in it agreed, and where that's recorded."
    )
    consent_cloud: bool = Field(
        default=False, description="The consent covers third-party cloud processing (R-4)."
    )
    license: str = Field(default="own footage", min_length=1)
    tags: list[str] = Field(
        default_factory=list,
        description='What it exercises, e.g. "faces", "plates", "speech", "handheld".',
    )
    notes: str = ""

    @field_validator("fps")
    @classmethod
    def _positive_fraction(cls, fps: str) -> str:
        try:
            value = Fraction(fps)
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError(f"fps {fps!r} must be a fraction like '30000/1001'") from exc
        if value <= 0:
            raise ValueError(f"fps {fps!r} must be positive")
        return fps

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"


class ClipManifest(Model):
    manifest_version: int = MANIFEST_VERSION
    about: str = ABOUT
    clips: list[ClipEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "ClipManifest":
        for what, values in (
            ("clip_id", [c.clip_id for c in self.clips]),
            ("video", [c.video.sha256 for c in self.clips]),
        ):
            dupes = sorted({v for v in values if values.count(v) > 1})
            if dupes:
                raise ValueError(f"duplicate {what} in the manifest: {', '.join(dupes)}")
        return self

    def get(self, clip_id: str) -> ClipEntry | None:
        return next((c for c in self.clips if c.clip_id == clip_id), None)

    def by_sha256(self, sha256: str) -> ClipEntry | None:
        return next((c for c in self.clips if c.video.sha256 == sha256), None)


# --- Reading and writing ------------------------------------------------------------------


def load_manifest(path: Path = MANIFEST_PATH) -> ClipManifest:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BenchmarkError(f"no clip manifest at {path}") from exc
    version = data.get("manifest_version") if isinstance(data, dict) else None
    if version != MANIFEST_VERSION:
        raise BenchmarkError(
            f"{path.name} has manifest_version {version!r}; this code reads {MANIFEST_VERSION}"
        )
    return ClipManifest.model_validate(data)


def save_manifest(manifest: ClipManifest, path: Path = MANIFEST_PATH) -> None:
    atomic_write(path, manifest.model_dump_json(indent=2) + "\n")


def atomic_write(path: Path, text: str) -> None:
    """Temp file, fsync, rename: a crash never leaves a half-written file (same as storage)."""
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


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- Registering a clip -------------------------------------------------------------------


def add_clip(
    path: str | Path,
    *,
    manifest_path: Path = MANIFEST_PATH,
    storage: Storage | None = None,
    clip_id: str | None = None,
    source: str | None = None,
    consent: str | None = None,
    consent_cloud: bool = False,
    license: str = "own footage",
    tags: Iterable[str] = (),
    notes: str = "",
) -> tuple[ClipEntry, bool]:
    """Store a clip's video in the data dir and make sure the manifest lists it.

    A file the manifest already lists (same hash) is just stored: that's how teammates get the
    team's clips. A new file needs ``clip_id``, ``source`` and ``consent`` and is appended.
    Returns the entry and whether the manifest changed. Nothing is stored or written until
    every check passes.
    """
    path = Path(path)
    storage = storage or Storage()
    manifest = load_manifest(manifest_path)
    info = probe(path)  # FileNotFoundError / VideoError for a missing or non-media file
    sha = sha256_of(path)

    existing = manifest.by_sha256(sha)
    if existing is not None:
        if clip_id is not None and clip_id != existing.clip_id:
            raise BenchmarkError(
                f"{path.name} is already in the manifest as {existing.clip_id!r}, not {clip_id!r}"
            )
        _store(storage, path, sha)
        return existing, False

    missing = [
        flag
        for flag, v in (("--id", clip_id), ("--source", source), ("--consent", consent))
        if not v
    ]
    if missing:
        raise BenchmarkError(
            f"{path.name} isn't in the manifest. To add it, pass {', '.join(missing)} "
            "(and --cloud-ok only if the people in it agreed to cloud processing)"
        )
    assert clip_id and source and consent
    if manifest.get(clip_id) is not None:
        raise BenchmarkError(f"clip id {clip_id!r} is already used by another video")

    entry = ClipEntry(
        clip_id=clip_id,
        video=VideoRef(sha256=sha, ext=path.suffix.lower(), original_name=path.name),
        size_bytes=path.stat().st_size,
        duration=round(info.duration, 3),
        width=info.width,
        height=info.height,
        fps=str(info.fps),
        has_audio=info.audio is not None,
        source=source,
        consent=consent,
        consent_cloud=consent_cloud,
        license=license,
        tags=sorted(set(tags)),
        notes=notes,
    )
    updated = ClipManifest(about=manifest.about, clips=[*manifest.clips, entry])  # validates
    _store(storage, path, sha)
    save_manifest(updated, manifest_path)
    return entry, True


def _store(storage: Storage, path: Path, sha: str) -> None:
    ref = storage.add_video(path)
    if ref.sha256 != sha:  # the file changed between hashing and copying
        raise BenchmarkError(f"{path} changed while it was being stored; try again")


# --- Is the clip here, and is it the right one? -------------------------------------------

ClipStatus = Literal[
    "ok", "missing", "size_mismatch", "hash_mismatch", "unreadable", "metadata_mismatch"
]


def clip_status(entry: ClipEntry, storage: Storage, verify: bool = False) -> tuple[ClipStatus, str]:
    """Where this machine stands on one clip, and what to do about it.

    Without ``verify`` it only checks the file is there with the right size (fast). With
    ``verify`` it also re-hashes and re-probes it, which reads the whole file.
    """
    path = storage.video_path(entry.video)
    if not path.is_file():
        return "missing", f"get {entry.video.original_name} from the team drive, then add-clip it"
    size = path.stat().st_size
    if size != entry.size_bytes:
        return "size_mismatch", f"{size} bytes here, the manifest says {entry.size_bytes}"
    if not verify:
        return "ok", ""
    if sha256_of(path) != entry.video.sha256:
        return "hash_mismatch", "the stored file isn't the one the manifest lists"
    try:
        info = probe(path)
    except VideoError as exc:
        return "unreadable", str(exc)
    diffs = metadata_diffs(entry, info)
    if diffs:
        return "metadata_mismatch", "; ".join(diffs)
    return "ok", "hash and metadata verified"


def metadata_diffs(entry: ClipEntry, info: VideoInfo) -> list[str]:
    diffs = []
    if abs(info.duration - entry.duration) > DURATION_TOLERANCE:
        diffs.append(f"duration {info.duration:.3f}s vs {entry.duration:.3f}s")
    if (info.width, info.height) != (entry.width, entry.height):
        diffs.append(f"size {info.width}x{info.height} vs {entry.resolution}")
    if Fraction(info.fps) != Fraction(entry.fps):
        diffs.append(f"fps {info.fps} vs {entry.fps}")
    if (info.audio is not None) != entry.has_audio:
        diffs.append("audio track differs")
    return diffs
