"""After tracking (F-10b): join fragments of one target, and cover heads the face detector missed.

Both run on contract tracks, so they need no model: tune them on cached detections with
``retrack``, or on any cached track set.

- ``stitch``: a tracker that loses a target for longer than its ``max_gap`` starts a new track
  when it comes back. One person can become dozens of fragments, which means dozens of timeline
  lanes, review items and (for F-13) judge calls. Two fragments of the same label are joined when
  the second starts within ``gap`` seconds of the first ending and where the first was heading.
- ``head_fallback``: "blur faces" must not depend on the face detector seeing every face (turned
  away, small, blurred). For each moment a person is tracked with no face box on their head, it
  adds an ``uncertain`` face box over the top of the person box. Privacy fails closed, and the
  review queue (F-41) shows these boxes as uncertain.
"""

from pydantic import Field

from vibe.contract import Keyframe, Track
from vibe.contract.common import Model
from vibe.modules.vision.detection import iou

Box = tuple[float, float, float, float]


class StitchSettings(Model):
    """Everything that changes which fragments are joined. Hashed into the cache key when used.

    The defaults are starting points to tune with the F-09 benchmark, not measured values."""

    gap: float = Field(default=1.5, gt=0, description="Most seconds between two fragments.")
    min_iou: float = Field(
        default=0.1, ge=0, le=1, description="Overlap of the predicted and the next first box."
    )
    max_shift: float = Field(
        default=1.0, gt=0, description="Or: centre distance in box sizes, if they don't overlap."
    )


class HeadSettings(Model):
    """Where a person's head is assumed to be, as fractions of the person box."""

    top: float = Field(default=0.0, ge=0, lt=1, description="From the box top.")
    height: float = Field(default=0.25, gt=0, le=1)
    width: float = Field(default=0.6, gt=0, le=1, description="Centred.")
    covered: float = Field(
        default=0.5, gt=0, le=1, description="A face box covering this much of the head counts."
    )


def _centre(b: Box) -> tuple[float, float]:
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _predict(track: Track, t: float) -> Box:
    """Where the track's box would be at ``t``, moving as it did over its last two keyframes."""
    last = track.keyframes[-1]
    if len(track.keyframes) < 2 or t <= last.t:
        return last.box
    prev = track.keyframes[-2]
    dt = last.t - prev.t
    if dt <= 0:
        return last.box
    k = (t - last.t) / dt
    moved = [b + (b - a) * k for a, b in zip(prev.box, last.box, strict=True)]
    x1, y1, x2, y2 = (min(max(v, 0.0), 1.0) for v in moved)
    if x2 - x1 <= 0 or y2 - y1 <= 0:
        return last.box
    return (x1, y1, x2, y2)


def _fits(prev: Track, nxt: Track, s: StitchSettings) -> float | None:
    """How well ``nxt`` continues ``prev`` (higher is better), or None if it doesn't."""
    gap = nxt.keyframes[0].t - prev.keyframes[-1].t
    if gap <= 0 or gap > s.gap:
        return None
    predicted, first = _predict(prev, nxt.keyframes[0].t), nxt.keyframes[0].box
    overlap = iou(predicted, first)
    if overlap >= s.min_iou:
        return 1.0 + overlap
    (px, py), (fx, fy) = _centre(predicted), _centre(first)
    size = max(predicted[2] - predicted[0], predicted[3] - predicted[1], 1e-6)
    shift = ((px - fx) ** 2 + (py - fy) ** 2) ** 0.5 / size
    return 1.0 - shift / s.max_shift if shift <= s.max_shift else None


def stitch(tracks: list[Track], settings: StitchSettings | None = None) -> list[Track]:
    """Join same-label fragments into one track each, keeping the first fragment's id.

    Greedy in time order: each fragment continues into the best later one that fits, and each
    fragment is continued at most once. Keyframes are kept as they were; the renderer
    interpolates across the gap as it does between any two keyframes.
    """
    s = settings or StitchSettings()
    order = sorted(range(len(tracks)), key=lambda i: tracks[i].keyframes[-1].t)
    chains: dict[int, list[int]] = {i: [i] for i in range(len(tracks))}
    head_of = {i: i for i in range(len(tracks))}
    taken: set[int] = set()
    for i in order:
        current = tracks[chains[head_of[i]][-1]]
        if chains[head_of[i]][-1] != i:
            continue  # i was already continued
        best, best_fit = None, None
        for j, other in enumerate(tracks):
            if j in taken or head_of[j] == head_of[i] or other.label != current.label:
                continue
            if chains[head_of[j]][0] != j:
                continue  # only a chain's first fragment can be joined onto
            fit = _fits(current, other, s)
            if fit is not None and (best_fit is None or fit > best_fit):
                best, best_fit = j, fit
        if best is None:
            continue
        taken.add(best)
        head = head_of[i]
        for k in chains.pop(best):
            chains[head].append(k)
            head_of[k] = head
    out = []
    for _, members in sorted(chains.items()):
        first = tracks[members[0]]
        keyframes = [k for m in members for k in tracks[m].keyframes]
        out.append(first.model_copy(update={"keyframes": keyframes}))
    return out


def _head(box: Box, h: HeadSettings) -> Box:
    x1, y1, x2, y2 = box
    w, hh = x2 - x1, y2 - y1
    cx = (x1 + x2) / 2
    return (
        max(cx - w * h.width / 2, 0.0),
        max(y1 + hh * h.top, 0.0),
        min(cx + w * h.width / 2, 1.0),
        min(y1 + hh * (h.top + h.height), 1.0),
    )


def _coverage(cover: Box, target: Box) -> float:
    ix = max(0.0, min(cover[2], target[2]) - max(cover[0], target[0]))
    iy = max(0.0, min(cover[3], target[3]) - max(cover[1], target[1]))
    area = (target[2] - target[0]) * (target[3] - target[1])
    return ix * iy / area if area > 0 else 0.0


def _box_at(track: Track, t: float, slack: float) -> Box | None:
    """The track's box at ``t`` (linear between keyframes), or None if it isn't there."""
    kfs = track.keyframes
    if t < kfs[0].t - slack or t > kfs[-1].t + slack:
        return None
    for a, b in zip(kfs, kfs[1:], strict=False):
        if a.t <= t <= b.t:
            f = 0.0 if b.t == a.t else (t - a.t) / (b.t - a.t)
            x1, y1, x2, y2 = (p + (q - p) * f for p, q in zip(a.box, b.box, strict=True))
            return (x1, y1, x2, y2)
    return kfs[0].box if t < kfs[0].t else kfs[-1].box


def head_fallback(
    tracks: list[Track],
    settings: HeadSettings | None = None,
    person: str = "person",
    face: str = "face",
) -> list[Track]:
    """``tracks`` plus one uncertain face track per person, over the head wherever no face box
    covers it. A person whose face was always found gets none."""
    h = settings or HeadSettings()
    faces = [t for t in tracks if t.label == face]
    added = []
    for p in (t for t in tracks if t.label == person):
        keyframes = []
        for k in p.keyframes:
            head = _head(k.box, h)
            if head[2] <= head[0] or head[3] <= head[1]:
                continue
            seen = (_box_at(f, k.t, slack=0.05) for f in faces)
            if any(b is not None and _coverage(b, head) >= h.covered for b in seen):
                continue
            keyframes.append(Keyframe(t=k.t, box=head, conf=min(k.conf, 0.5), state="uncertain"))
        if keyframes:
            added.append(
                Track(
                    track_id=f"{p.track_id}_head",
                    label=face,
                    source="detector",
                    keyframes=keyframes,
                )
            )
    return [*tracks, *added]


def post_settings(
    stitch_with: StitchSettings | None, heads: HeadSettings | None
) -> dict[str, object] | None:
    """What goes in a tracks key for these steps, or None when neither runs (old keys stay)."""
    post: dict[str, object] = {}
    if stitch_with is not None:
        post["stitch"] = stitch_with.model_dump()
    if heads is not None:
        post["heads"] = heads.model_dump()
    return post or None


def postprocess(
    tracks: list[Track], stitch_with: StitchSettings | None, heads: HeadSettings | None
) -> list[Track]:
    """Stitch first (so a head track follows one whole person), then add head boxes."""
    if stitch_with is not None:
        tracks = stitch(tracks, stitch_with)
    if heads is not None:
        tracks = head_fallback(tracks, heads)
    return tracks
