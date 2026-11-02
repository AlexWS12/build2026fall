"""Ground truth for a benchmark clip: where each target really is, and who speaks when (F-09).

One file per clip, ``labels/<clip_id>.json``, committed (it's text, never media). Visual targets
reuse the contract's ``Track`` (normalized boxes, D-013), so a model's tracks and the ground
truth have the same shape and ``score.score_tracks`` compares them directly.

How to label, so the scores mean something:

- **One track per real target**, with the label the detector uses (``face``, ``person``,
  ``license_plate``), because scoring only compares a label with the same label.
- **A keyframe at least once a second while the target is visible**, plus one where it enters
  and one where it leaves, all with ``source="user"``, ``state="user"`` and ``conf=1``. Scores
  are taken at these moments, so a target with no keyframe in a stretch isn't measured there.
- **Draw the whole target**, the full face or plate, not just the part that's easy to box. A
  tight box is what makes a privacy leak show up.
- **Identity, if it matters** (for "everyone except me" rules): ``identity="id_me"`` on the
  enrolled person's tracks. Never store a face embedding here (R-4: biometrics stay local).
- **Speech** (for F-31 and F-32 later): segments with a speaker id and, optionally, the words.
  Not scored yet.

``python -m vibe.benchmark new-labels CLIP_ID`` writes an empty file to start from.
"""

import json
from fractions import Fraction
from pathlib import Path

from pydantic import Field, ValidationError, model_validator

from vibe.benchmark.manifest import CLIP_ID, SHA256, BenchmarkError, ClipEntry, atomic_write
from vibe.contract import Track
from vibe.contract.common import Model

HERE = Path(__file__).parent
LABELS_DIR = HERE / "labels"

LABELS_VERSION = 1
"""Version of the label format. Bump it on a change that breaks a reader or writer."""


class SpeechSegment(Model):
    start: float = Field(ge=0, description="Source seconds.")
    end: float = Field(description="Source seconds, excluded.")
    speaker: str | None = Field(default=None, description='e.g. "spk_jean". Null if unknown.')
    text: str = ""

    @model_validator(mode="after")
    def _end_after_start(self) -> "SpeechSegment":
        if self.end <= self.start:
            raise ValueError(f"end ({self.end}) must be after start ({self.start})")
        return self


class ClipLabels(Model):
    labels_version: int = LABELS_VERSION
    clip_id: str = Field(pattern=CLIP_ID)
    sha256: str = Field(pattern=SHA256, description="The video these labels describe.")
    tracks: list[Track] = Field(default_factory=list, description="Ground-truth targets.")
    speech: list[SpeechSegment] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def _unique_track_ids(self) -> "ClipLabels":
        ids = [t.track_id for t in self.tracks]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate track_id: {', '.join(dupes)}")
        return self

    @property
    def keyframe_count(self) -> int:
        return sum(len(t.keyframes) for t in self.tracks)


def labels_path(clip_id: str, labels_dir: Path = LABELS_DIR) -> Path:
    return labels_dir / f"{clip_id}.json"


def load_labels(clip_id: str, labels_dir: Path = LABELS_DIR) -> ClipLabels | None:
    """The clip's labels, or None if nobody has labelled it yet.

    Raises ``BenchmarkError`` (naming the file) if the file exists but isn't valid.
    """
    path = labels_path(clip_id, labels_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"{path.name}: not valid JSON: {exc}") from exc
    version = data.get("labels_version") if isinstance(data, dict) else None
    if version != LABELS_VERSION:
        raise BenchmarkError(
            f"{path.name} has labels_version {version!r}; this code reads {LABELS_VERSION}"
        )
    try:
        return ClipLabels.model_validate(data)
    except ValidationError as exc:
        raise BenchmarkError(f"{path.name}: {exc}") from exc


def check_labels(labels: ClipLabels, entry: ClipEntry) -> list[str]:
    """Problems between a clip's labels and its manifest entry. Empty means they agree."""
    problems = []
    if labels.clip_id != entry.clip_id:
        problems.append(f"labels say clip_id {labels.clip_id!r}, the file is for {entry.clip_id!r}")
    if labels.sha256 != entry.video.sha256:
        problems.append("labels are for a different video (sha256 differs from the manifest)")
    end = entry.duration + float(1 / Fraction(entry.fps))  # allow one frame of rounding
    late = [f"{t.track_id}@{k.t:g}s" for t in labels.tracks for k in t.keyframes if k.t > end] + [
        f"speech@{s.start:g}s" for s in labels.speech if s.end > end
    ]
    if late:
        shown = ", ".join(late[:5]) + (" ..." if len(late) > 5 else "")
        problems.append(f"times past the clip's end ({entry.duration:g}s): {shown}")
    return problems


def write_labels_template(entry: ClipEntry, labels_dir: Path = LABELS_DIR) -> Path:
    """Start a labels file for a clip. Refuses to overwrite one that exists."""
    path = labels_path(entry.clip_id, labels_dir)
    if path.exists():
        raise BenchmarkError(f"{path.name} already exists; edit it instead")
    empty = ClipLabels(clip_id=entry.clip_id, sha256=entry.video.sha256)
    atomic_write(path, empty.model_dump_json(indent=2) + "\n")
    return path
