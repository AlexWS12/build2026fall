"""Score a model's tracks against a clip's ground truth (F-09). Pure: no files, no models.

At every ground-truth keyframe, two questions per labelled target:

- **Detected?** A predicted box with the same label overlaps it with IoU >= ``iou``, matched
  one-to-one, best overlap first. This gives recall and precision, the usual detection numbers.
- **Exposed?** Would the target still show after masking? The renderer masks each predicted box
  grown by the effect's ``padding``. If no padded box with the same label covers at least
  ``coverage`` of the true box, the target is visible at that moment: an exposure, the privacy
  failure this product must not have ("privacy edits fail closed", CLAUDE.md).

Both are reported because they disagree. A box can be a poor detection and still a good mask
(shifted, but big enough to cover), or a good detection and still a leak (tight, with part of the
face outside it). For "blur every face", exposure is the number that matters most.

Both truth and predicted tracks are read at every ground-truth keyframe time by linear
interpolation between their keyframes, the way a tracker carries a box between detections. So a
target labelled at t=0 and t=1 still counts at t=0.5 when another target has a keyframe there.
Outside a track's first and last keyframe, the track isn't there. Only the same label counts,
because "blur faces" masks face tracks only.

**Padding assumption:** the box grows by ``padding`` times its width and height, centred, then
is clipped to the frame. The renderer (F-04) owns what padding means; if it does something
else, change ``pad`` to match so exposure measures what viewers would actually see.
"""

import bisect
from collections.abc import Iterable, Sequence

from pydantic import Field

from vibe.contract import Effect, Track
from vibe.contract.common import Model

Box = tuple[float, float, float, float]

DEFAULT_IOU = 0.5
DEFAULT_COVERAGE = 0.9
DEFAULT_PADDING: float = Effect.model_fields["padding"].default
"""The contract's default effect padding, so the score matches what an unconfigured mask does."""

_MOMENT_DIGITS = 4
"""Keyframe times are rounded to this many decimals, so 0.1 and 0.1000001 are one moment."""
_TIME_EPS = 1e-4
"""How far outside its keyframes a track still counts. At least the rounding above (5e-5), so
rounding a track's own first or last keyframe time never makes it vanish there."""


class LabelScore(Model):
    label: str
    truth_boxes: int = Field(description="Ground-truth boxes, one per target per keyframe.")
    predicted_boxes: int = Field(description="Predicted boxes present at those same moments.")
    detected: int = Field(description="Truth boxes matched at IoU >= the threshold.")
    exposed: int = Field(description="Truth boxes no padded prediction covers enough.")
    recall: float | None
    precision: float | None
    exposure_rate: float | None


class TrackScore(Model):
    iou: float
    coverage: float
    padding: float
    moments: int = Field(description="Distinct ground-truth keyframe times scored.")
    by_label: list[LabelScore]
    total: LabelScore


# --- Geometry -----------------------------------------------------------------------------


def area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def intersection(a: Box, b: Box) -> float:
    return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def iou(a: Box, b: Box) -> float:
    inter = intersection(a, b)
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def pad(b: Box, padding: float) -> Box:
    """Grow ``b`` by ``padding`` times its size, centred, clipped to the frame."""
    dx = (b[2] - b[0]) * padding / 2
    dy = (b[3] - b[1]) * padding / 2
    return (max(0.0, b[0] - dx), max(0.0, b[1] - dy), min(1.0, b[2] + dx), min(1.0, b[3] + dy))


def coverage(truth: Box, mask: Box) -> float:
    """How much of ``truth`` the ``mask`` hides, 0..1."""
    a = area(truth)
    return intersection(truth, mask) / a if a > 0 else 1.0


def box_at(track: Track, t: float) -> Box | None:
    """Where ``track`` is at source time ``t``, interpolated, or None outside its keyframes."""
    frames = track.keyframes
    if t < frames[0].t - _TIME_EPS or t > frames[-1].t + _TIME_EPS:
        return None
    i = bisect.bisect_right([k.t for k in frames], t)
    if i == 0:
        return frames[0].box
    if i == len(frames):
        return frames[-1].box
    a, b = frames[i - 1], frames[i]
    f = (t - a.t) / (b.t - a.t)
    return tuple(x + f * (y - x) for x, y in zip(a.box, b.box, strict=True))  # type: ignore[return-value]


# --- Scoring ------------------------------------------------------------------------------


def _match(truth: Sequence[Box], predicted: Sequence[Box], threshold: float) -> int:
    """One-to-one matches at IoU >= threshold, best overlap first."""
    pairs = sorted(
        ((iou(t, p), i, j) for i, t in enumerate(truth) for j, p in enumerate(predicted)),
        reverse=True,
    )
    used_t: set[int] = set()
    used_p: set[int] = set()
    for overlap, i, j in pairs:
        if overlap < threshold:
            break
        if i not in used_t and j not in used_p:
            used_t.add(i)
            used_p.add(j)
    return len(used_t)


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def _label_score(label: str, truth: int, predicted: int, detected: int, exposed: int) -> LabelScore:
    return LabelScore(
        label=label,
        truth_boxes=truth,
        predicted_boxes=predicted,
        detected=detected,
        exposed=exposed,
        recall=_ratio(detected, truth),
        precision=_ratio(detected, predicted),
        exposure_rate=_ratio(exposed, truth),
    )


def score_tracks(
    truth: Iterable[Track],
    predicted: Iterable[Track],
    *,
    iou_threshold: float = DEFAULT_IOU,
    min_coverage: float = DEFAULT_COVERAGE,
    padding: float = DEFAULT_PADDING,
) -> TrackScore:
    truth, predicted = list(truth), list(predicted)
    moments = sorted({round(k.t, _MOMENT_DIGITS) for track in truth for k in track.keyframes})
    labels = sorted({t.label for t in truth} | {p.label for p in predicted})

    counts = {label: [0, 0, 0, 0] for label in labels}  # truth, predicted, detected, exposed
    for t in moments:
        for label in labels:
            gt = [b for g in truth if g.label == label and (b := box_at(g, t)) is not None]
            preds = [b for p in predicted if p.label == label and (b := box_at(p, t)) is not None]
            masks = [pad(p, padding) for p in preds]
            c = counts[label]
            c[0] += len(gt)
            c[1] += len(preds)
            c[2] += _match(gt, preds, iou_threshold)
            c[3] += sum(1 for g in gt if not any(coverage(g, m) >= min_coverage for m in masks))

    by_label = [_label_score(label, *counts[label]) for label in labels]
    totals = [sum(c[i] for c in counts.values()) for i in range(4)]
    return TrackScore(
        iou=iou_threshold,
        coverage=min_coverage,
        padding=padding,
        moments=len(moments),
        by_label=by_label,
        total=_label_score("all", *totals),
    )
