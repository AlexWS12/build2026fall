"""Segmentation masks for the tracks an edit masks, made at export time and cached (F-15).

    timeline state ──► which tracks are masked, when ──► their keyframes in those spans
                         ──► segmenter (box prompt) ──► cache/<sha256>/masks/<key>.json
                                                                  └──► MaskSet ──► renderer

Only tracks an ``add_mask`` targets get masks, and only at the keyframes inside its spans (plus
one on each side, so the renderer can interpolate). Masks already cached are reused.

Masks live in the analysis cache, never in the op log or the tracks (D-018). The renderer finds
a keyframe's mask by (track id, keyframe time, keyframe box), so the contract doesn't change: a
track whose box was corrected by hand simply has no mask for that box and gets the box. One
file per track and segmenter; its key hashes the segmenter's settings and the track's
keyframes, so a changed track or model never serves stale masks (D-014).

Masks are media (D-017): they stay in the local cache like the frames they come from.
"""

import logging
import time
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict

from vibe.contract import Keyframe, TimelineState, Track
from vibe.modules.vision import rle
from vibe.modules.vision.cache import _key, _write
from vibe.modules.vision.detection import Box
from vibe.modules.vision.segmenters import Segmenter
from vibe.storage import Storage
from vibe.video import FrameIndex, VideoReader

log = logging.getLogger(__name__)

KIND = "masks"


class RleMask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    size: tuple[int, int]
    """[height, width] of the frame it was made on."""
    counts: str
    """COCO compressed RLE (``vision.rle``)."""


class MaskEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    t: float
    box: tuple[float, float, float, float]
    """The keyframe's box the mask was prompted with."""
    mask: RleMask | None
    """``None``: the segmenter found nothing there, so the renderer uses the box."""


class MasksFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segmenter: str
    settings: dict[str, object]
    track_id: str
    entries: list[MaskEntry]


def masks_key(segmenter: Segmenter, track: Track) -> str:
    """E.g. ``sam2.1-hiera-tiny-trk_3f9a1c2e_4-7b0d4e11``."""
    keyframes = [(k.t, k.box) for k in track.keyframes]
    return _key(
        f"{segmenter.name}-{track.track_id}",
        {"segmenter": segmenter.name, "settings": dict(segmenter.settings), "track": keyframes},
    )


def wanted_keyframes(state: TimelineState, track: Track) -> list[Keyframe]:
    """The keyframes of ``track`` the renderer can use a mask at: those inside the track's mask
    spans, plus the one before and the one after each span. ``uncertain`` ones never get a mask
    (D-018), so they aren't segmented."""
    spans = state.masks.get(track.track_id)
    if not spans:
        return []
    times = [k.t for k in track.keyframes]
    picked: set[int] = set()
    for span in spans:
        first = max(bisect_right(times, span.start) - 1, 0)
        last = len(times) - 1
        if span.end is not None:
            last = min(bisect_left(times, span.end), last)
        picked.update(range(first, last + 1))
    return [track.keyframes[i] for i in sorted(picked) if track.keyframes[i].state != "uncertain"]


@dataclass
class MaskReport:
    """What ``ensure_masks`` did, for the CLI and for benchmarks."""

    segmenter: str
    tracks: int = 0
    keyframes: int = 0
    reused: int = 0
    computed: int = 0
    empty: int = 0
    """Computed, but the segmenter found nothing: those keyframes render with the box."""
    frames_segmented: int = 0
    segment_seconds: float = 0.0
    """Time inside ``segmenter.segment`` only (no decoding or cache writes)."""
    total_seconds: float = 0.0


class MaskSet:
    """Decoded masks to look up per frame: what ``FrameRenderer`` gets as ``masks``.

    Satisfies the renderer's ``MaskLookup``. Decoded masks are kept for the last few keyframes,
    since consecutive frames ask for the same two.
    """

    def __init__(self, files: Iterable[MasksFile], keep: int = 16) -> None:
        self._entries: dict[tuple[str, float, Box], RleMask] = {}
        for f in files:
            for e in f.entries:
                if e.mask is not None:
                    self._entries[(f.track_id, e.t, e.box)] = e.mask
        self._decoded: OrderedDict[tuple[str, float, Box], np.ndarray] = OrderedDict()
        self._keep = keep

    def __len__(self) -> int:
        return len(self._entries)

    def mask(self, track_id: str, t: float, box: Box) -> np.ndarray | None:
        key = (track_id, t, tuple(box))
        if key in self._decoded:
            self._decoded.move_to_end(key)
            return self._decoded[key]
        found = self._entries.get(key)
        if found is None:
            return None
        decoded = rle.decode({"size": list(found.size), "counts": found.counts})
        self._decoded[key] = decoded
        if len(self._decoded) > self._keep:
            self._decoded.popitem(last=False)
        return decoded


@dataclass
class _Job:
    track: Track
    key: str
    entries: dict[tuple[float, Box], MaskEntry] = field(default_factory=dict)
    missing: list[Keyframe] = field(default_factory=list)


def load_masks(storage: Storage, sha256: str, key: str) -> MasksFile | None:
    path = storage.cache_path(sha256, KIND, key)
    if not path.exists():
        return None
    return MasksFile.model_validate_json(path.read_bytes())


def ensure_masks(
    storage: Storage,
    sha256: str,
    video: Path,
    state: TimelineState,
    tracks: Sequence[Track],
    segmenter: Segmenter,
) -> tuple[MaskSet, MaskReport]:
    """Masks for every keyframe the renderer can use one at, for the tracks ``state`` masks;
    computes only what isn't cached yet and writes it to the cache.

    Errors from the segmenter or the video reader propagate: the caller then renders with boxes
    (privacy fails closed) and says why.
    """
    started = time.perf_counter()
    report = MaskReport(segmenter.name)
    jobs: list[_Job] = []
    for track in tracks:
        wanted = wanted_keyframes(state, track)
        if not wanted:
            continue
        job = _Job(track, masks_key(segmenter, track))
        cached = load_masks(storage, sha256, job.key)
        if cached is not None:
            job.entries = {(e.t, e.box): e for e in cached.entries}
        for k in wanted:
            if (k.t, k.box) in job.entries:
                report.reused += 1
            else:
                job.missing.append(k)
        report.tracks += 1
        report.keyframes += len(wanted)
        jobs.append(job)

    todo = [(job, k) for job in jobs for k in job.missing]
    if todo:
        index = FrameIndex.build(video)
        by_frame: dict[int, list[tuple[_Job, Keyframe]]] = {}
        for job, k in todo:
            if 0 <= k.t < index.end:  # a keyframe past the clip has no frame: it keeps its box
                by_frame.setdefault(index.frame_at(k.t), []).append((job, k))
        with VideoReader(video, index=index) as reader:
            for i in sorted(by_frame):  # in order, so the reader decodes straight through
                image = reader.read(i).image
                asked = by_frame[i]
                t0 = time.perf_counter()
                masks = segmenter.segment(image, [k.box for _, k in asked])
                report.segment_seconds += time.perf_counter() - t0
                report.frames_segmented += 1
                if len(masks) != len(asked):
                    raise RuntimeError(
                        f"segmenter {segmenter.name} gave {len(masks)} masks for {len(asked)} boxes"
                    )
                for (job, k), m in zip(asked, masks, strict=True):
                    encoded = None
                    if m is not None and m.any():
                        packed = rle.encode(m)
                        encoded = RleMask(size=tuple(packed["size"]), counts=packed["counts"])
                    else:
                        report.empty += 1
                    job.entries[(k.t, k.box)] = MaskEntry(t=k.t, box=k.box, mask=encoded)
                    report.computed += 1

    files = []
    for job in jobs:
        f = MasksFile(
            segmenter=segmenter.name,
            settings=dict(segmenter.settings),
            track_id=job.track.track_id,
            entries=sorted(job.entries.values(), key=lambda e: e.t),
        )
        if job.missing:
            _write(storage.cache_path(sha256, KIND, job.key), f.model_dump_json() + "\n")
        files.append(f)
    report.total_seconds = time.perf_counter() - started
    log.info(
        "masks: %d tracks, %d keyframes (%d cached, %d computed, %d empty) with %s",
        report.tracks,
        report.keyframes,
        report.reused,
        report.computed,
        report.empty,
        segmenter.name,
    )
    return MaskSet(files), report
