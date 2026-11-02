"""The benchmark report: what can be measured on this machine right now, as a table (F-09).

``python -m vibe.benchmark report`` checks every clip in the manifest (here? the right file?),
checks its labels, and scores every cached set of tracks for it against those labels. Every
report states the commit, the hardware and the backend it ran on, because a number without them
can't be compared (CLAUDE.md, "Honesty").

What it doesn't do: score the planner. R-1's planner bench does that on the shared request set,
and this report doesn't run a second copy of it.

Cached tracks are read from ``cache/<sha256>/tracks/<key>.json`` (D-014), one file per model and
settings, as a list of contract ``Track``s: the shape of ``tracks.schema.json``. Whoever writes
the cache (F-08 / F-10) writes that shape, and every key found is scored, so two models on the
same clip come out side by side.
"""

import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, TypeAdapter, ValidationError

from vibe import config
from vibe.benchmark.labels import LABELS_DIR, check_labels, load_labels
from vibe.benchmark.manifest import (
    MANIFEST_PATH,
    BenchmarkError,
    ClipStatus,
    clip_status,
    load_manifest,
)
from vibe.benchmark.score import (
    DEFAULT_COVERAGE,
    DEFAULT_IOU,
    DEFAULT_PADDING,
    TrackScore,
    score_tracks,
)
from vibe.contract import Track
from vibe.contract.common import Model
from vibe.storage import Storage

REPO_ROOT = Path(__file__).resolve().parents[4]
_TRACKS = TypeAdapter(list[Track])


class RunInfo(Model):
    created_at: datetime
    commit: str
    dirty: bool = Field(description="Uncommitted changes to tracked files when it ran.")
    platform: str
    cpu: str
    cpu_count: int | None
    gpu: str
    python: str
    backend: config.Backend
    verified: bool = Field(description="Clips were re-hashed and re-probed (--verify).")


class ClipRow(Model):
    clip_id: str
    status: ClipStatus
    detail: str
    duration: float
    resolution: str
    fps: str
    has_audio: bool
    consent_cloud: bool
    tags: list[str]
    labels: Literal["none", "ok", "invalid"]
    label_targets: int
    label_keyframes: int


class ScoreRow(Model):
    clip_id: str
    tracks_key: str
    status: Literal["scored", "unreadable"]
    detail: str = ""
    score: TrackScore | None = None


class Report(Model):
    run: RunInfo
    clips: list[ClipRow]
    scores: list[ScoreRow]
    problems: list[str] = Field(description="Anything that needs fixing. Empty means none.")


# --- Building it --------------------------------------------------------------------------


def build_report(
    manifest_path: Path = MANIFEST_PATH,
    labels_dir: Path = LABELS_DIR,
    storage: Storage | None = None,
    verify: bool = False,
) -> Report:
    storage = storage or Storage()
    manifest = load_manifest(manifest_path)
    clips: list[ClipRow] = []
    scores: list[ScoreRow] = []
    problems: list[str] = []

    for entry in manifest.clips:
        status, detail = clip_status(entry, storage, verify)
        if status not in ("ok", "missing"):
            problems.append(f"{entry.clip_id}: {status}: {detail}")

        labels, label_state = None, "none"
        try:
            labels = load_labels(entry.clip_id, labels_dir)
        except BenchmarkError as exc:
            problems.append(f"{entry.clip_id}: labels: {exc}")
            label_state = "invalid"
        if labels is not None:
            issues = check_labels(labels, entry)
            problems += [f"{entry.clip_id}: labels: {i}" for i in issues]
            label_state = "invalid" if issues else "ok"

        clips.append(
            ClipRow(
                clip_id=entry.clip_id,
                status=status,
                detail=detail,
                duration=entry.duration,
                resolution=entry.resolution,
                fps=entry.fps,
                has_audio=entry.has_audio,
                consent_cloud=entry.consent_cloud,
                tags=entry.tags,
                labels=label_state,
                label_targets=len(labels.tracks) if labels else 0,
                label_keyframes=labels.keyframe_count if labels else 0,
            )
        )
        if label_state != "ok" or labels is None:
            continue
        for path in sorted((storage.cache_dir / entry.video.sha256 / "tracks").glob("*.json")):
            try:
                predicted = _TRACKS.validate_json(path.read_bytes())
            except (OSError, ValidationError) as exc:
                reason = f"not a list of contract Tracks: {str(exc).splitlines()[0]}"
                scores.append(
                    ScoreRow(
                        clip_id=entry.clip_id,
                        tracks_key=path.stem,
                        status="unreadable",
                        detail=reason,
                    )
                )
                problems.append(f"{entry.clip_id}: cached tracks {path.name}: {reason}")
                continue
            scores.append(
                ScoreRow(
                    clip_id=entry.clip_id,
                    tracks_key=path.stem,
                    status="scored",
                    score=score_tracks(labels.tracks, predicted),
                )
            )

    return Report(run=run_info(verify), clips=clips, scores=scores, problems=problems)


def run_info(verified: bool = False) -> RunInfo:
    commit = _git("rev-parse", "--short", "HEAD") or "unknown"
    # --no-optional-locks: don't refresh the index, so a report never leaves an index.lock.
    dirty = bool(_git("--no-optional-locks", "status", "--porcelain", "--untracked-files=no"))
    return RunInfo(
        created_at=datetime.now(UTC).replace(microsecond=0),
        commit=commit,
        dirty=dirty,
        platform=f"{platform.system()} {platform.release()} ({platform.machine()})",
        cpu=_cpu_name(),
        cpu_count=os.cpu_count(),
        gpu=_gpu_name(),
        python=platform.python_version(),
        backend=config.model_backend(),
        verified=verified,
    )


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def _cpu_name() -> str:
    try:
        if sys.platform.startswith("linux"):
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
        elif sys.platform == "darwin":
            return subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return platform.processor() or "unknown"


def _gpu_name() -> str:
    """NVIDIA GPUs only (nvidia-smi); others show as not detected, which the report says."""
    if shutil.which("nvidia-smi") is None:
        return "none detected (checks NVIDIA only)"
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "nvidia-smi failed"
    return "; ".join(line.strip() for line in out.splitlines() if line.strip()) or "none detected"


# --- Rendering ----------------------------------------------------------------------------
# ASCII only: Windows consoles piping cp1252 choke on anything else.


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    widths = [
        max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)
    ]
    line = lambda cells: "  ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)).rstrip()  # noqa: E731
    return [line(headers), line(["-" * w for w in widths]), *(line(r) for r in rows)]


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    return out + ["| " + " | ".join(c.replace("|", "\\|") for c in r) + " |" for r in rows]


def _sections(report: Report) -> list[tuple[str, list[str], list[list[str]] | None, str]]:
    """(title, headers, rows, note) for each part of the report; rows None = just the note."""
    present = sum(c.status == "ok" for c in report.clips)
    clip_rows = [
        [
            c.clip_id,
            c.status,
            f"{c.duration:g}s",
            c.resolution,
            c.fps,
            "yes" if c.has_audio else "no",
            "yes" if c.consent_cloud else "no",
            c.labels
            if c.labels != "ok"
            else f"ok ({c.label_targets} targets, {c.label_keyframes} kf)",
            c.detail,
        ]
        for c in report.clips
    ]
    clips_note = (
        ""
        if report.clips
        else "No clips in the manifest yet. Register consented footage with:\n"
        "  uv run python -m vibe.benchmark add-clip FILE --id ID --source TEXT --consent TEXT"
    )
    score_rows = []
    for s in report.scores:
        if s.score is None:
            score_rows.append(
                [s.clip_id, s.tracks_key, "-", "-", "-", "-", "-", s.status + ": " + s.detail]
            )
            continue
        for ls in [*s.score.by_label, s.score.total]:
            score_rows.append(
                [
                    s.clip_id,
                    s.tracks_key,
                    ls.label,
                    str(ls.truth_boxes),
                    _pct(ls.recall),
                    _pct(ls.precision),
                    f"{ls.exposed} ({_pct(ls.exposure_rate)})",
                    "",
                ]
            )
    labelled = sum(c.labels == "ok" for c in report.clips)
    scores_note = (
        ""
        if report.scores
        else f"Nothing to score: {labelled} clip(s) have valid labels, and none has cached "
        "tracks yet (cache/<sha256>/tracks/<key>.json, written by F-08 / F-10)."
    )
    return [
        (
            f"Clips (manifest: {len(report.clips)}, present here: {present})",
            ["clip", "status", "length", "size", "fps", "audio", "cloud ok", "labels", "detail"],
            clip_rows or None,
            clips_note,
        ),
        (
            f"Vision vs ground truth (IoU >= {DEFAULT_IOU}, covered >= {DEFAULT_COVERAGE:.0%} "
            f"after {DEFAULT_PADDING:g} padding)",
            ["clip", "tracks", "label", "truth", "recall", "precision", "exposed", "note"],
            score_rows or None,
            scores_note,
        ),
    ]


def _header(r: RunInfo) -> list[str]:
    return [
        f"commit {r.commit}{' (uncommitted changes)' if r.dirty else ''}, "
        f"{r.created_at:%Y-%m-%d %H:%M} UTC, backend {r.backend}"
        f"{', clips verified' if r.verified else ''}",
        f"hardware: {r.platform}, {r.cpu}, {r.cpu_count} threads, GPU: {r.gpu}, Python {r.python}",
    ]


def render_text(report: Report) -> str:
    lines = ["Vibe benchmark report (F-09)", *_header(report.run)]
    for title, headers, rows, note in _sections(report):
        lines += ["", title]
        lines += _table(headers, rows) if rows else []
        lines += [note] if note else []
    lines += ["", "Problems:" if report.problems else "Problems: none"]
    lines += [f"  - {p}" for p in report.problems]
    return "\n".join(lines) + "\n"


def render_markdown(report: Report) -> str:
    lines = ["# Vibe benchmark report (F-09)", "", *(f"- {h}" for h in _header(report.run))]
    for title, headers, rows, note in _sections(report):
        lines += ["", f"## {title}", ""]
        lines += _md_table(headers, rows) if rows else []
        lines += ["", note] if note and rows else ([note] if note else [])
    lines += ["", "## Problems", ""]
    lines += [f"- {p}" for p in report.problems] or ["None."]
    return "\n".join(lines) + "\n"
