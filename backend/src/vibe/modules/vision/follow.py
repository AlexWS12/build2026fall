"""Follow a box drawn by hand through the clip (F-14).

The user draws a box around something on one frame. ``follow_box`` looks for the same picture
in the frames after it and before it, and returns a contract ``Track`` with ``source="user"``,
which the project keeps in ``tracks.user.json`` like any hand-drawn track (docs/ARCHITECTURE.md,
"Storage"). It needs no model and no GPU: it matches the drawn patch against each sampled frame
with normalized cross-correlation, on small grey frames, so it runs on every laptop in the team
and in CI. It is a classical tracker, not an AI module, so it has no stub / local / remote split.

Privacy fails closed here too:

- the drawn frame's keyframe is ``user`` (conf 1): a person said the target is there;
- a weak match is kept as an ``uncertain`` keyframe (the renderer pads it, the review queue
  flags it) instead of being dropped;
- once the match has been too weak for ``lost_after`` seconds, or the box has mostly left the
  frame, the follower stops in that direction and says where (``Followed.lost_before`` /
  ``lost_after``). Past a track's ends the renderer holds the last box and marks it ``lost``,
  so a mask over the whole clip still covers that stretch and the review queue lists it.

The settings are starting points, not tuned values: nothing here is measured on labelled clips.
"""

import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pydantic import Field

from vibe.contract import Keyframe, Track
from vibe.contract.common import Model
from vibe.video import FrameIndex, VideoReader, probe

Box = tuple[float, float, float, float]
"""Normalized ``[x1, y1, x2, y2]``, like the contract's."""

_EPS = 1e-6


class FollowSettings(Model):
    """Everything that changes the track that comes out. Starting points, not tuned values."""

    sample_fps: float = Field(
        default=10.0, gt=0, description="Frames looked at per second of video, at most."
    )
    work_width: int = Field(
        default=320, ge=32, description="Frames are matched in grey at this width."
    )
    min_template: int = Field(
        default=8,
        ge=4,
        description="The drawn box is at least this many pixels wide and high at the matching "
        "size; a smaller box makes the follower match at a bigger size instead.",
    )
    search: float = Field(
        default=1.0,
        gt=0,
        description="How far to look around the predicted box, as a fraction of its size.",
    )
    scales: tuple[float, ...] = Field(
        default=(0.95, 1.0, 1.05), min_length=1, description="Box size changes tried per step."
    )
    uncertain_below: float = Field(
        default=0.6, ge=0, le=1, description="A match under this is an uncertain keyframe."
    )
    lost_below: float = Field(
        default=0.35, ge=0, le=1, description="A match under this doesn't count as seen."
    )
    lost_after: float = Field(
        default=0.5, gt=0, description="Seconds of matches under lost_below before giving up."
    )
    learn_rate: float = Field(
        default=0.1,
        ge=0,
        le=1,
        description="How much of each good match goes into the template, so slow changes in "
        "how the target looks are followed.",
    )
    min_inside: float = Field(
        default=0.5,
        gt=0,
        le=1,
        description="The share of the box that must stay in the frame; under it, the target "
        "has left and the follower stops.",
    )


@dataclass(frozen=True)
class Followed:
    track: Track
    lost_before: float | None
    """Source time of the first keyframe when following backwards stopped before the clip's
    start (lost, or left the frame); ``None`` if it reached the first frame."""
    lost_after: float | None
    """Source time of the last keyframe when following forwards stopped before the clip's end;
    ``None`` if it reached the last frame."""


# --- Matching -------------------------------------------------------------------------------


def to_grey(image: np.ndarray) -> np.ndarray:
    """RGB ``uint8`` (h, w, 3) → float32 grey (h, w)."""
    rgb = image.astype(np.float32)
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def resize(image: np.ndarray, height: int, width: int) -> np.ndarray:
    """Bilinear resize of a 2-D array (numpy only: OpenCV isn't a dependency)."""
    h, w = image.shape
    if (h, w) == (height, width):
        return image
    ys = (np.arange(height, dtype=np.float32) + 0.5) * (h / height) - 0.5
    xs = (np.arange(width, dtype=np.float32) + 0.5) * (w / width) - 0.5
    ys = np.clip(ys, 0, h - 1)
    xs = np.clip(xs, 0, w - 1)
    y0 = np.floor(ys).astype(np.intp)
    x0 = np.floor(xs).astype(np.intp)
    y1 = np.minimum(y0 + 1, h - 1)
    x1 = np.minimum(x0 + 1, w - 1)
    wy = (ys - y0)[:, None]
    wx = (xs - x0)[None, :]
    top = image[y0][:, x0] * (1 - wx) + image[y0][:, x1] * wx
    bottom = image[y1][:, x0] * (1 - wx) + image[y1][:, x1] * wx
    return (top * (1 - wy) + bottom * wy).astype(np.float32)


def _window_sums(image: np.ndarray, th: int, tw: int) -> np.ndarray:
    """Sum of every ``th`` × ``tw`` window of ``image``, shape (h - th + 1, w - tw + 1)."""
    integral = np.zeros((image.shape[0] + 1, image.shape[1] + 1), dtype=np.float64)
    integral[1:, 1:] = image.cumsum(0).cumsum(1)
    return integral[th:, tw:] - integral[:-th, tw:] - integral[th:, :-tw] + integral[:-th, :-tw]


def ncc(search: np.ndarray, template: np.ndarray) -> np.ndarray:
    """Zero-mean normalized cross-correlation of ``template`` at every position inside
    ``search``: 1 is the same picture, 0 unrelated. A flat template (no detail to match) or a
    flat window scores 0, never a false 1."""
    th, tw = template.shape
    sh, sw = search.shape
    if th > sh or tw > sw:
        raise ValueError(f"template {template.shape} is bigger than the search area {search.shape}")
    t = template.astype(np.float64) - template.mean()
    t_norm = math.sqrt(float((t * t).sum()))
    out_shape = (sh - th + 1, sw - tw + 1)
    if t_norm < _EPS:
        return np.zeros(out_shape)
    s = search.astype(np.float64)
    spectrum = np.fft.rfft2(s) * np.conj(np.fft.rfft2(t, s=(sh, sw)))
    corr = np.fft.irfft2(spectrum, s=(sh, sw))[: out_shape[0], : out_shape[1]]
    n = th * tw
    sums = _window_sums(s, th, tw)
    var = _window_sums(s * s, th, tw) - sums * sums / n
    denom = np.sqrt(np.maximum(var, 0.0)) * t_norm
    flat = denom < _EPS * n
    scores = np.where(flat, 0.0, corr / np.where(flat, 1.0, denom))
    return np.clip(scores, -1.0, 1.0)


@dataclass
class _Rect:
    """A box in pixels of the working frame, as floats: x, y of the top-left corner, w, h."""

    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


class BoxFollower:
    """Follows one patch from frame to frame. ``start`` takes the frame and the box drawn on it;
    each ``step`` takes the next frame in the direction being followed (forwards or backwards,
    the follower doesn't care) and returns where the box is and how well it matched."""

    def __init__(self, settings: FollowSettings | None = None) -> None:
        self.settings = settings or FollowSettings()
        self._template: np.ndarray | None = None
        self._rect: _Rect | None = None
        self._velocity = (0.0, 0.0)
        self._scale = 1.0
        self._size = (0, 0)  # frame (height, width)

    def start(self, frame: np.ndarray, box: Box) -> None:
        h, w = frame.shape
        x1, y1, x2, y2 = box
        rect = _Rect(x1 * w, y1 * h, (x2 - x1) * w, (y2 - y1) * h)
        x0, y0 = round(rect.x), round(rect.y)
        tw, th = max(1, round(rect.w)), max(1, round(rect.h))
        x0, y0 = min(max(x0, 0), w - tw), min(max(y0, 0), h - th)
        self._template = frame[y0 : y0 + th, x0 : x0 + tw].astype(np.float32).copy()
        self._rect = _Rect(float(x0), float(y0), float(tw), float(th))
        self._velocity = (0.0, 0.0)
        self._scale = 1.0
        self._size = (h, w)

    def step(self, frame: np.ndarray) -> tuple[_Rect, float]:
        """Where the box is on ``frame`` and the match score (0..1). On a weak match the box
        stays where it was predicted, and the caller decides whether that still counts."""
        if self._template is None or self._rect is None:
            raise RuntimeError("call start() first")
        h, w = frame.shape
        th, tw = self._template.shape
        prev = self._rect
        px, py = prev.cx + self._velocity[0], prev.cy + self._velocity[1]
        best: tuple[float, _Rect, float] | None = None  # (score, rect, scale)
        for factor in self.settings.scales:
            scale = self._scale * factor
            bw, bh = tw * scale, th * scale
            margin = self.settings.search * max(bw, bh)
            sx0 = max(0, math.floor(px - bw / 2 - margin))
            sy0 = max(0, math.floor(py - bh / 2 - margin))
            sx1 = min(w, math.ceil(px + bw / 2 + margin))
            sy1 = min(h, math.ceil(py + bh / 2 + margin))
            region = frame[sy0:sy1, sx0:sx1]
            rh, rw = round(region.shape[0] / scale), round(region.shape[1] / scale)
            if rh < th or rw < tw:
                continue
            scores = ncc(resize(region, rh, rw), self._template)
            iy, ix = np.unravel_index(int(np.argmax(scores)), scores.shape)
            score = float(scores[iy, ix])
            if factor != 1.0:
                score *= 0.98  # prefer keeping the size: size changes drift on noise
            rect = _Rect(sx0 + ix * region.shape[1] / rw, sy0 + iy * region.shape[0] / rh, bw, bh)
            if best is None or score > best[0]:
                best = (score, rect, scale)
        if best is None:  # the search area is smaller than the box: nothing to compare
            return self._predicted(px, py), 0.0
        score, rect, scale = best
        score = max(0.0, score)
        if score < self.settings.lost_below:
            return self._predicted(px, py), score
        self._velocity = (rect.cx - prev.cx, rect.cy - prev.cy)
        self._rect = rect
        self._scale = scale
        if score >= self.settings.uncertain_below and self.settings.learn_rate > 0:
            patch = self._patch(frame, rect, th, tw)
            if patch is not None:
                lr = self.settings.learn_rate
                self._template = (1 - lr) * self._template + lr * patch
        return rect, score

    def _predicted(self, px: float, py: float) -> _Rect:
        prev = self._rect
        assert prev is not None
        return _Rect(px - prev.w / 2, py - prev.h / 2, prev.w, prev.h)

    @staticmethod
    def _patch(frame: np.ndarray, rect: _Rect, th: int, tw: int) -> np.ndarray | None:
        h, w = frame.shape
        x0, y0 = max(0, round(rect.x)), max(0, round(rect.y))
        x1, y1 = min(w, round(rect.x + rect.w)), min(h, round(rect.y + rect.h))
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        return resize(frame[y0:y1, x0:x1], th, tw)

    def inside(self, rect: _Rect) -> float:
        """The share of ``rect`` that is inside the frame."""
        h, w = self._size
        ix = max(0.0, min(rect.x + rect.w, w) - max(rect.x, 0.0))
        iy = max(0.0, min(rect.y + rect.h, h) - max(rect.y, 0.0))
        return ix * iy / max(rect.w * rect.h, _EPS)


def _normalized(rect: _Rect, size: tuple[int, int]) -> Box | None:
    """``rect`` clipped to the frame and normalized, or ``None`` if nothing of it is left."""
    h, w = size
    x1, y1 = max(0.0, rect.x / w), max(0.0, rect.y / h)
    x2, y2 = min(1.0, (rect.x + rect.w) / w), min(1.0, (rect.y + rect.h) / h)
    if x2 - x1 < _EPS or y2 - y1 < _EPS:
        return None
    return (round(x1, 5), round(y1, 5), round(x2, 5), round(y2, 5))


# --- One direction, then both ---------------------------------------------------------------


def follow_frames(
    start: tuple[float, np.ndarray],
    box: Box,
    frames: Iterable[tuple[float, np.ndarray]],
    settings: FollowSettings | None = None,
) -> tuple[list[Keyframe], bool]:
    """Follow ``box`` from the ``start`` frame through ``frames`` (``(t, grey)`` pairs, in the
    order to follow: increasing times forwards, decreasing backwards). Returns the keyframes
    found (not including the start) and whether the follower stopped early (lost, or left)."""
    settings = settings or FollowSettings()
    follower = BoxFollower(settings)
    t0, frame0 = start
    follower.start(frame0, box)
    keyframes: list[Keyframe] = []
    weak_since: float | None = None
    for t, frame in frames:
        rect, score = follower.step(frame)
        if follower.inside(rect) < settings.min_inside:
            return keyframes, True
        if score < settings.lost_below:
            weak_since = t if weak_since is None else weak_since
            if abs(t - weak_since) >= settings.lost_after:
                return keyframes, True
            continue
        weak_since = None
        placed = _normalized(rect, frame.shape)
        if placed is None:
            return keyframes, True
        state = "tracked" if score >= settings.uncertain_below else "uncertain"
        keyframes.append(Keyframe(t=t, box=placed, conf=round(min(score, 1.0), 4), state=state))
    # Out of frames: reaching the clip's end only counts if the target was still seen there.
    return keyframes, weak_since is not None


def work_size(width: int, height: int, box: Box, settings: FollowSettings) -> tuple[int, int]:
    """The (width, height) to match at: ``work_width``, made bigger when the drawn box would
    be under ``min_template`` pixels there, and never bigger than the video. Even numbers,
    because the decoder's scaler wants them."""
    bw, bh = (box[2] - box[0]), (box[3] - box[1])
    target = max(
        settings.work_width,
        math.ceil(settings.min_template / max(bw, _EPS)),
        math.ceil(settings.min_template / max(bh, _EPS) * width / height),
    )
    ww = min(width, target)
    wh = max(2, round(ww * height / width))
    return ww - ww % 2 or 2, wh - wh % 2 or 2


def follow_box(
    path: str | Path,
    t: float,
    box: Box,
    *,
    track_id: str,
    label: str = "object",
    settings: FollowSettings | None = None,
    index: FrameIndex | None = None,
) -> Followed:
    """Follow ``box`` (drawn on the frame shown at ``t``) forwards and backwards through the
    video at ``path``, and return it as a hand-drawn track."""
    settings = settings or FollowSettings()
    info = probe(path)
    index = index if index is not None else FrameIndex.build(path)
    size = work_size(info.width, info.height, box, settings)
    fps = len(index) / index.end if index.end > 0 else settings.sample_fps
    step = max(1, round(fps / settings.sample_fps))
    with VideoReader(path, index=index, size=size) as reader:
        i0 = index.frame_at(t)
        first = reader.read(i0)
        start = (first.time, to_grey(first.image))
        forward, lost_fwd = follow_frames(
            start, box, _grey_frames(reader, range(i0 + step, len(index), step)), settings
        )
        backward, lost_back = follow_frames(
            start, box, _grey_frames_backwards(reader, i0, step), settings
        )
    drawn = Keyframe(t=first.time, box=box, conf=1.0, state="user")
    keyframes = [*reversed(backward), drawn, *forward]
    track = Track(track_id=track_id, label=label, source="user", keyframes=keyframes)
    return Followed(
        track=track,
        lost_before=keyframes[0].t if lost_back else None,
        lost_after=keyframes[-1].t if lost_fwd else None,
    )


def _grey_frames(reader: VideoReader, indices: Iterable[int]) -> Iterator[tuple[float, np.ndarray]]:
    for i in indices:
        frame = reader.read(i)
        yield frame.time, to_grey(frame.image)


_BACK_CHUNK = 32
"""Sampled frames decoded per chunk when following backwards. Reading one frame at a time in
reverse would seek back to a keyframe for every frame; a chunk decodes straight through once."""


def _grey_frames_backwards(
    reader: VideoReader, i0: int, step: int
) -> Iterator[tuple[float, np.ndarray]]:
    """Every ``step``-th frame before ``i0``, latest first, decoded a chunk at a time."""
    indices = list(range(i0 - step, -1, -step))
    for at in range(0, len(indices), _BACK_CHUNK):
        chunk = indices[at : at + _BACK_CHUNK]
        frames = list(_grey_frames(reader, reversed(chunk)))  # in time order: no seek per frame
        yield from reversed(frames)
