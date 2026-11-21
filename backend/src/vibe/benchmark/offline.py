"""Offline detector bake-off (F-10b): run detector sets over the benchmark clips, time them, and
cache their tracks so ``python -m vibe.benchmark report`` scores them.

    python -m vibe.benchmark offline --set "yunet:weights=@yunet" --set rfdetr-n --every 1 --every 3

Each run is one clip x one detector set x one stride. It caches detections and tracks under the
same keys as ``python -m vibe.modules.vision analyze`` (``retrack`` can retune the tracker later),
and records wall time per stage, frames, seconds per frame, the process's peak memory and how many
tracks each label got. A detector that can't load here (package, weights, memory) is a
``skipped`` row with the reason, never a silently missing one.

Numbers are only as general as the machine: the report states the hardware (CLAUDE.md,
"Honesty"). Quality scores come from ``report``; this file only adds speed and track counts.
"""

import resource
import sys
import time
from collections import Counter
from collections.abc import Sequence
from typing import Literal

from pydantic import Field

from vibe.benchmark.labels import ClipLabels
from vibe.benchmark.manifest import ClipEntry
from vibe.benchmark.report import RunInfo, _md_table, run_info
from vibe.contract.common import Model
from vibe.modules.vision import cache
from vibe.modules.vision.analyze import analyze_video
from vibe.modules.vision.detection import NotInstalled
from vibe.modules.vision.detectors import make_detector
from vibe.modules.vision.tracking import TRACKERS, TrackerSettings
from vibe.storage import Storage


class RunRow(Model):
    clip_id: str
    detectors: list[str] = Field(description="The detector specs of this set.")
    every: int
    tracker: str
    status: Literal["ran", "skipped", "failed"]
    detail: str = ""
    frames: int = 0
    seconds: dict[str, float] = Field(default_factory=dict, description="Wall time per stage.")
    seconds_per_frame: float | None = None
    peak_rss_mb: float | None = Field(
        default=None, description="The process's peak memory so far (it never goes down)."
    )
    tracks_key: str | None = None
    tracks_by_label: dict[str, int] = Field(default_factory=dict)


class OfflineReport(Model):
    run: RunInfo
    rows: list[RunRow]


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)  # bytes vs KiB


def run_one(
    entry: ClipEntry,
    storage: Storage,
    specs: Sequence[str],
    every: int,
    tracker: str = "iou",
    settings: TrackerSettings | None = None,
) -> RunRow:
    row = RunRow(clip_id=entry.clip_id, detectors=list(specs), every=every, tracker=tracker,
                 status="skipped")  # fmt: skip
    video = storage.video_path(entry.video)
    if not video.is_file():
        return row.model_copy(update={"detail": "the clip isn't stored on this machine"})
    try:
        detectors = [make_detector(spec) for spec in specs]
        track = TRACKERS[tracker](settings or TrackerSettings())
    except (NotInstalled, ValueError) as e:
        return row.model_copy(update={"detail": str(e)})
    except MemoryError:
        return row.model_copy(update={"detail": "out of memory loading the model"})
    try:
        start = time.perf_counter()
        result = analyze_video(video, detectors, track, every=every)
        total = time.perf_counter() - start
    except MemoryError:
        return row.model_copy(update={"status": "failed", "detail": "out of memory"})
    except RuntimeError as e:  # e.g. a backend error from the model package
        return row.model_copy(update={"status": "failed", "detail": str(e).splitlines()[0]})
    det_key = cache.detections_key(detectors, every)
    trk_key = cache.tracks_key(det_key, track.name, track.settings)
    cache.save_detections(storage, entry.video.sha256, det_key, result.detections)
    tracks = cache.save_tracks(storage, entry.video.sha256, trk_key, result.tracks)
    return row.model_copy(
        update={
            "status": "ran",
            "frames": result.frames,
            "seconds": {k: round(v, 3) for k, v in {**result.seconds, "total": total}.items()},
            "seconds_per_frame": round(total / result.frames, 4) if result.frames else None,
            "peak_rss_mb": _peak_rss_mb(),
            "tracks_key": trk_key,
            "tracks_by_label": dict(Counter(t.label for t in tracks)),
        }
    )


def run_offline(
    entries: Sequence[ClipEntry],
    storage: Storage,
    sets: Sequence[Sequence[str]],
    everies: Sequence[int] = (1,),
    tracker: str = "iou",
    progress: bool = False,
) -> OfflineReport:
    rows = []
    for entry in entries:
        for specs in sets:
            for every in everies:
                row = run_one(entry, storage, specs, every, tracker)
                rows.append(row)
                if progress:
                    took = f"{row.seconds.get('total', 0):.1f}s" if row.status == "ran" else ""
                    print(f"{entry.clip_id} {'+'.join(specs)} k{every}: {row.status} {took} "
                          f"{row.detail}".rstrip(), file=sys.stderr)  # fmt: skip
    return OfflineReport(run=run_info(), rows=rows)


def render_markdown(report: OfflineReport, labels: dict[str, ClipLabels] | None = None) -> str:
    """Speed and track counts, with tracks per labelled target where the clip has labels (1.0 is
    one track per real target; more means fragments, or detections of things nobody labelled)."""
    r = report.run
    lines = [
        "# Offline detector runs",
        "",
        f"commit {r.commit}{' (dirty)' if r.dirty else ''}, {r.created_at:%Y-%m-%d %H:%M} UTC",
        f"hardware: {r.platform}, {r.cpu}, {r.cpu_count} threads, GPU: {r.gpu}, Python {r.python}",
        "",
        "Wall time on this machine only. Quality (recall, exposure) is in `benchmark report`.",
        "",
    ]
    rows = []
    for row in report.rows:
        truth = (
            Counter(t.label for t in labels[row.clip_id].tracks)
            if labels and row.clip_id in labels
            else Counter()
        )  # noqa: E501
        per_target = ", ".join(
            f"{label} {n / truth[label]:.1f}" for label, n in sorted(row.tracks_by_label.items())
            if truth[label]
        )  # fmt: skip
        rows.append(
            [
                row.clip_id,
                " + ".join(row.detectors),
                str(row.every),
                row.status + (f": {row.detail}" if row.detail else ""),
                str(row.frames or "-"),
                f"{row.seconds_per_frame:.3f}" if row.seconds_per_frame is not None else "-",
                f"{row.peak_rss_mb:.0f}" if row.peak_rss_mb is not None else "-",
                ", ".join(f"{k} {v}" for k, v in sorted(row.tracks_by_label.items())) or "-",
                per_target or "-",
            ]
        )
    headers = ["clip", "detectors", "every", "status", "frames", "s/frame", "peak MB", "tracks",
               "tracks per labelled target"]  # fmt: skip
    return "\n".join([*lines, *_md_table(headers, rows), ""])
