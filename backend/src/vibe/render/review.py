"""The review queue (F-41): the stretches of the masks a person should look at, most serious first.

Derived from the mask audit (``render/mask_report.py``), so it comes from the op log and the
tracks alone. Nothing is stored: a ``confirm_mask`` takes its stretch off the queue because
``TimelineState.reviewed`` covers it, so undoing the confirmation (and redoing it) brings the item
back (or removes it again) for free.

The queue lists ``lost``, ``no_track``, ``uncertain`` and ``gap`` stretches. ``covered`` ones are
fine and ``held`` ones are short and expected, so those are only counted (``held_seconds``).

Known limitation: confirmations live in the op log, while the geometry lives in the tracks. A later
hand correction of a track (F-14/F-42), or a re-analysis that reuses track ids, doesn't take a
confirmation back. F-42's box tool must erase ``reviewed`` over the range it corrects.

Pure: no files, no video and no network. Times are source seconds.
"""

import hashlib
from collections.abc import Iterable, Sequence
from typing import Literal

from vibe.contract import Effect, TimelineState, Track
from vibe.contract.common import Model
from vibe.render.mask_report import SpanAudit, Stretch, audit_masks

ReviewKind = Literal["lost", "no_track", "uncertain", "gap"]

_SEVERITY: dict[str, int] = {"lost": 0, "no_track": 1, "uncertain": 2, "gap": 3}
"""Lower is more serious. Also the order of the queue."""

_JOIN = 1e-9
"""Stretches or reviewed spans whose ends differ by less than this are touching."""

_MIN_SECONDS = 1e-6
"""Remainders shorter than this are dropped."""

_Interval = tuple[float, float]


class ReviewItem(Model):
    item_id: str
    """Stable for the same track, kind and range, so a client can keep its place."""
    kind: ReviewKind
    track_id: str
    label: str | None
    """The track's label (``face``, ...), or null if no track has this id."""
    start: float
    end: float
    effect: Effect
    op_id: str
    """The mask op whose span this stretch is in."""
    request_id: str


class ReviewQueue(Model):
    items: list[ReviewItem]
    """Most serious first (lost, no_track, uncertain, gap), then by start, then by track id."""
    held_seconds: float
    """Time in held stretches (a box kept just past either end of a track) not yet reviewed."""
    rules_not_audited: list[str]
    """Standing rules, which can't be audited ahead of a render."""


def review_queue(
    state: TimelineState,
    tracks: Iterable[Track],
    *,
    duration: float,
    frame_times: Sequence[float] | None = None,
) -> ReviewQueue:
    """What to review in ``state``'s masks over ``tracks``, for a video ``duration`` seconds long.

    ``frame_times`` is passed on to ``audit_masks``.
    """
    tracks = list(tracks)
    labels: dict[str, str] = {}
    for track in tracks:
        labels.setdefault(track.track_id, track.label)
    audit = audit_masks(state, tracks, duration=duration, frame_times=frame_times)

    items: list[ReviewItem] = []
    held_seconds = 0.0
    for span in audit.spans:
        reviewed = _cover(state, span.track_id)
        for start, end in ((s.start, s.end) for s in span.stretches if s.kind == "held"):
            held_seconds += sum(b - a for a, b in _subtract(start, end, reviewed))
        for kind, start, end in _merge_flagged(span):
            for a, b in _subtract(start, end, reviewed):
                items.append(_item(span, kind, a, b, labels.get(span.track_id)))

    items.sort(key=lambda i: (_SEVERITY[i.kind], i.start, i.track_id))
    return ReviewQueue(
        items=items, held_seconds=held_seconds, rules_not_audited=list(audit.rules_not_audited)
    )


def _item(
    span: SpanAudit, kind: ReviewKind, start: float, end: float, label: str | None
) -> ReviewItem:
    digest = hashlib.sha1(f"{span.track_id}|{kind}|{start!r}|{end!r}".encode()).hexdigest()
    return ReviewItem(
        item_id="rv_" + digest[:12],
        kind=kind,
        track_id=span.track_id,
        label=label,
        start=start,
        end=end,
        effect=span.effect,
        op_id=span.op_id,
        request_id=span.request_id,
    )


def _merge_flagged(span: SpanAudit) -> list[tuple[ReviewKind, float, float]]:
    """The flagged stretches of one mask span, touching ones joined under the most serious kind.

    A ``held`` or ``covered`` stretch between two flagged ones keeps them apart."""
    merged: list[tuple[ReviewKind, float, float]] = []
    previous: Stretch | None = None
    for stretch in span.stretches:
        if stretch.kind not in _SEVERITY:
            previous = None
            continue
        kind: ReviewKind = stretch.kind  # type: ignore[assignment]
        if previous is not None and abs(previous.end - stretch.start) < _JOIN:
            old_kind, start, _ = merged[-1]
            merged[-1] = (min(old_kind, kind, key=_SEVERITY.__getitem__), start, stretch.end)
        else:
            merged.append((kind, stretch.start, stretch.end))
        previous = stretch
    return merged


def _cover(state: TimelineState, track_id: str) -> list[_Interval]:
    """The reviewed time of ``track_id`` as sorted intervals, touching spans joined (they may come
    from different requests)."""
    spans = sorted(
        (
            (s.start, float("inf") if s.end is None else s.end)
            for s in state.reviewed.get(track_id, [])
        ),
    )
    cover: list[_Interval] = []
    for start, end in spans:
        if cover and start - cover[-1][1] < _JOIN:
            cover[-1] = (cover[-1][0], max(cover[-1][1], end))
        else:
            cover.append((start, end))
    return cover


def _subtract(start: float, end: float, cover: list[_Interval]) -> list[_Interval]:
    """``[start, end)`` without ``cover``, dropping remainders that are too short."""
    pieces: list[_Interval] = []
    cursor = start
    for a, b in cover:
        if b <= cursor:
            continue
        if a >= end:
            break
        if a > cursor:
            pieces.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < end:
        pieces.append((cursor, end))
    return [(a, b) for a, b in pieces if b - a >= _MIN_SECONDS]
