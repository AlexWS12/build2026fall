"""Draw ground-truth boxes for a clip in the browser, then import them as labels (F-09b).

``label-frames`` writes one self-contained ``label.html`` per clip: a frame about once a second
(plus the last one), with any boxes already in the clip's labels drawn on them. You draw a box per
target per frame, give each real target one track number, and press Save, which downloads a JSON
file. ``import-labels`` turns that file into tracks in ``labels/<clip_id>.json`` (replacing that
label's tracks, keeping the others) and checks them against the manifest.

The page and its frames are media from the clip: they go under ``data/`` (gitignored), never in
the repo. Only the labels file, which is text, is committed.
"""

import base64
import json
from fractions import Fraction
from importlib import resources
from pathlib import Path

import av
import numpy as np

from vibe.benchmark.labels import ClipLabels, check_labels
from vibe.benchmark.manifest import BenchmarkError, ClipEntry
from vibe.contract import Keyframe, Track
from vibe.video import VideoReader

MIN_SIDE = 0.004
"""A drawn box narrower or shorter than this (in frame fractions) was a click. Same as the page."""

LABEL_FILE_VERSION = 1
"""Version of the JSON the page saves. Bump it on a change that breaks ``import_label_file``."""


def sample_times(duration: float, step: float = 1.0) -> list[float]:
    """0, step, 2*step... and the last moment, so a target's exit is always labelled."""
    if duration <= 0 or step <= 0:
        raise ValueError(f"duration and step must be positive, got {duration} and {step}")
    times, t = [], 0.0
    while t < duration - 1e-6:
        times.append(round(t, 6))
        t += step
    return [*times, round(duration, 6)]


def _jpeg(image: np.ndarray, quality: int = 4) -> bytes:
    """RGB -> JPEG with PyAV's encoder, so the tool needs no image library."""
    height, width = image.shape[:2]
    codec = av.CodecContext.create("mjpeg", "w")
    codec.width, codec.height, codec.pix_fmt = width - width % 2, height - height % 2, "yuvj420p"
    codec.time_base = Fraction(1, 25)
    codec.options = {"qscale": str(quality)}
    frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(image), format="rgb24")
    frame = frame.reformat(width=codec.width, height=codec.height, format="yuvj420p")
    return b"".join(bytes(p) for p in [*codec.encode(frame), *codec.encode(None)])


def sample_frames(video: Path, duration: float, step: float = 1.0) -> list[tuple[float, bytes]]:
    """``(source time, JPEG)`` for the first frame at or after each sample time."""
    wanted = sample_times(duration, step)
    found: list[tuple[float, bytes]] = []
    last: tuple[float, np.ndarray] | None = None
    with VideoReader(video) as reader:
        for frame in reader.iter_frames():
            last = (frame.time, frame.image)
            while wanted and frame.time >= wanted[0] - 1e-6:
                wanted.pop(0)
                if not found or found[-1][0] != frame.time:
                    found.append((frame.time, _jpeg(frame.image)))
    if wanted and last is not None and (not found or found[-1][0] != last[0]):
        found.append((last[0], _jpeg(last[1])))  # the clip ended before its stated duration
    if not found:
        raise BenchmarkError(f"{video.name}: no frames to label")
    return found


def page_data(
    entry: ClipEntry, frames: list[tuple[float, bytes]], label: str, labels: ClipLabels | None
) -> dict:
    """What the page needs: frames as data URLs, plus the label's existing boxes to correct."""
    tracks = [t for t in (labels.tracks if labels else []) if t.label == label]
    boxes: list[list[dict]] = [[] for _ in frames]
    for number, track in enumerate(tracks, start=1):
        for i, (t, _) in enumerate(frames):
            for k in track.keyframes:
                if abs(k.t - t) < 0.05:
                    boxes[i].append({"track": number, "box": list(k.box)})
    return {
        "version": LABEL_FILE_VERSION,
        "clip_id": entry.clip_id,
        "sha256": entry.video.sha256,
        "label": label,
        "frames": [
            {"t": t, "src": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}
            for t, jpg in frames
        ],
        "boxes": boxes,
    }


def write_label_page(data: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "label.html"
    page = resources.files("vibe.benchmark").joinpath("label_page.html").read_text("utf-8")
    # "</" can't end the script early: JSON escapes it, so no frame data can break the page.
    path.write_text(page.replace("/*DATA*/null", json.dumps(data).replace("</", "<\\/")), "utf-8")
    return path


def import_label_file(saved: dict, entry: ClipEntry, labels: ClipLabels | None) -> ClipLabels:
    """The clip's labels with ``saved``'s label replaced by the boxes drawn on the page."""
    if not isinstance(saved, dict) or saved.get("version") != LABEL_FILE_VERSION:
        raise BenchmarkError(f"not a label-frames file (version {LABEL_FILE_VERSION} expected)")
    if saved.get("clip_id") != entry.clip_id or saved.get("sha256") != entry.video.sha256:
        raise BenchmarkError(f"this file is for {saved.get('clip_id')!r}, not {entry.clip_id!r}")
    label = saved.get("label")
    if not isinstance(label, str) or not label:
        raise BenchmarkError("the file names no label")
    by_track: dict[int, list[Keyframe]] = {}
    for frame in saved.get("frames", []):
        t = float(frame["t"])
        for drawn in frame.get("boxes", []):
            x1, y1, x2, y2 = (min(max(float(v), 0.0), 1.0) for v in drawn["box"])
            x1, x2 = sorted((x1, x2))
            y1, y2 = sorted((y1, y2))
            if x2 - x1 < MIN_SIDE or y2 - y1 < MIN_SIDE:
                continue  # a click, not a box
            box = (round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4))
            by_track.setdefault(int(drawn["track"]), []).append(
                Keyframe(t=t, box=box, conf=1.0, state="user")
            )
    new = [
        Track(
            track_id=f"{label}_{number}",
            label=label,
            source="user",
            keyframes=sorted(kfs, key=lambda k: k.t),
        )
        for number, kfs in sorted(by_track.items())
    ]
    base = labels or ClipLabels(clip_id=entry.clip_id, sha256=entry.video.sha256)
    kept = [t for t in base.tracks if t.label != label]
    taken = {t.track_id for t in kept} & {t.track_id for t in new}
    if taken:
        raise BenchmarkError(f"track ids already used by another label: {', '.join(sorted(taken))}")
    merged = base.model_copy(update={"tracks": [*kept, *new]})
    merged = ClipLabels.model_validate(merged.model_dump())  # re-run the validators
    problems = check_labels(merged, entry)
    if problems:
        raise BenchmarkError("; ".join(problems))
    return merged
