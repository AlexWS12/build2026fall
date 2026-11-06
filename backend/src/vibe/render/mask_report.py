"""Whole-clip audit of the masks: what happens during each masked stretch, without any pixels.

A mask in effect always has a box (``render/geometry.py``), but the box isn't always on the
target. This works out how far to trust it, from the timeline state and the tracks alone, so it
can run before a render. It says where a target is hidden by a normal box, where the box is a
guess (``held``, ``gap``, ``uncertain``), and where the target may have left it (``lost``). F-41
can list the guesses for review.

``lost`` and ``no_track`` are the stretches the renderer reports in its issues, sampled there
frame by frame. A test keeps the two in step, because this repeats the renderer's rules.

Pure: no files, no video and no network. Times are source seconds, start included, end
excluded, like ``Span.contains``. Cut ranges are left out, because nobody sees them.
"""

import math
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal, get_args

from vibe.contract import Effect, Keyframe, MaskSpan, TimelineState, TimeRange, Track
from vibe.render.geometry import HOLD_SECONDS, MAX_GAP_SECONDS

StretchKind = Literal["covered", "held", "gap", "uncertain", "lost", "no_track"]
"""``covered``: a normal box between two keyframes. ``held``: past either end of a track, within
``HOLD_SECONDS``. ``gap``: between keyframes more than ``MAX_GAP_SECONDS`` apart, within
``HOLD_SECONDS`` of one, under a box covering both. ``uncertain``: next to an ``uncertain``
keyframe. ``lost``: further than ``HOLD_SECONDS`` from any detection, so a box stays masked but
the target may have left it. ``no_track``: no track has this id, so the whole frame is masked."""

_CAUTION: dict[StretchKind, int] = {"covered": 0, "held": 1, "gap": 2, "uncertain": 3}
"""Which kind to report when several apply, e.g. two tracks sharing an id. Higher wins."""

_Piece = tuple[float, float, StretchKind]


@dataclass(frozen=True)
class Stretch:
    kind: StretchKind
    start: float
    end: float

    @property
    def seconds(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class SpanAudit:
    """One mask span, with the part of it that's in the output split into stretches."""

    track_id: str
    op_id: str
    request_id: str
    effect: Effect
    stretches: tuple[Stretch, ...]


@dataclass(frozen=True)
class MaskAudit:
    spans: tuple[SpanAudit, ...]
    """Sorted by track id, then start."""
    rules_not_audited: tuple[str, ...]
    """Standing rules, by id. They mask whatever is detected on each frame, so this can't say
    anything about them."""

    @property
    def totals(self) -> dict[StretchKind, float]:
        """Seconds of each kind, added up over every span."""
        totals = dict.fromkeys(get_args(StretchKind), 0.0)
        for span in self.spans:
            for stretch in span.stretches:
                totals[stretch.kind] += stretch.seconds
        return totals

    @property
    def clean(self) -> bool:
        """True when the renderer would report nothing and there are no rules left unchecked."""
        totals = self.totals
        return not (totals["lost"] or totals["no_track"] or self.rules_not_audited)


def audit_masks(state: TimelineState, tracks: Iterable[Track], *, duration: float) -> MaskAudit:
    """Audit every mask in ``state`` against ``tracks``, for a video ``duration`` seconds long."""
    if not (math.isfinite(duration) and duration > 0):
        raise ValueError(f"duration must be finite and above 0, got {duration}")

    paths: dict[str, list[list[_Piece]]] = {}
    for track in tracks:
        paths.setdefault(track.track_id, []).append(_pieces(track))

    spans = []
    for track_id in sorted(state.masks):
        for span in state.masks[track_id]:
            stretches = _audit_span(span, paths.get(track_id), state.cuts, duration)
            if stretches:
                spans.append(
                    SpanAudit(track_id, span.op_id, span.request_id, span.effect, stretches)
                )
    return MaskAudit(tuple(spans), tuple(r.rule_id for r in state.rules))


def _pieces(track: Track) -> list[_Piece]:
    """Where ``track`` isn't lost, as sorted pieces that don't overlap. Anywhere else, it is."""
    keyframes = track.keyframes
    first, last = keyframes[0], keyframes[-1]
    pieces: list[_Piece] = [(first.t - HOLD_SECONDS, first.t, _held_kind(first))]
    for a, b in pairwise(keyframes):
        long_gap = b.t - a.t > MAX_GAP_SECONDS
        if "uncertain" in (a.state, b.state):
            kind: StretchKind = "uncertain"
        else:
            kind = "gap" if long_gap else "covered"
        if long_gap and b.t - a.t > 2 * HOLD_SECONDS:
            pieces += [(a.t, a.t + HOLD_SECONDS, kind), (b.t - HOLD_SECONDS, b.t, kind)]
        else:
            pieces.append((a.t, b.t, kind))
    pieces.append((last.t, last.t + HOLD_SECONDS, _held_kind(last)))
    return pieces


def _held_kind(keyframe: Keyframe) -> StretchKind:
    return "uncertain" if keyframe.state == "uncertain" else "held"


def _audit_span(
    span: MaskSpan,
    paths: list[list[_Piece]] | None,
    cuts: Sequence[TimeRange],
    duration: float,
) -> tuple[Stretch, ...]:
    end = duration if span.end is None else min(span.end, duration)
    stretches: list[Stretch] = []
    for window in _visible(span.start, end, cuts):
        if paths is None:
            stretches.append(Stretch("no_track", *window))
        else:
            stretches.extend(_classify(window, paths))
    return tuple(stretches)


def _visible(start: float, end: float, cuts: Sequence[TimeRange]) -> list[tuple[float, float]]:
    """``[start, end)`` without the cuts, which are sorted and merged."""
    windows = []
    cursor = start
    for cut in cuts:
        if cut.start >= end:
            break
        cut_end = end if cut.end is None else min(cut.end, end)
        if cut_end <= cursor:
            continue
        if cut.start > cursor:
            windows.append((cursor, cut.start))
        cursor = cut_end
    if cursor < end:
        windows.append((cursor, end))
    return windows


def _classify(window: tuple[float, float], paths: list[list[_Piece]]) -> list[Stretch]:
    """Split ``window`` wherever the answer changes, joining neighbours of the same kind."""
    start, end = window
    edges = {start, end}
    for pieces in paths:
        for piece_start, piece_end, _ in pieces:
            edges.update(x for x in (piece_start, piece_end) if start < x < end)
    lookups = [(pieces, [p[0] for p in pieces]) for pieces in paths]

    stretches: list[Stretch] = []
    for a, b in pairwise(sorted(edges)):
        kind = _kind_at((a + b) / 2, lookups)
        if stretches and stretches[-1].kind == kind:
            stretches[-1] = Stretch(kind, stretches[-1].start, b)
        else:
            stretches.append(Stretch(kind, a, b))
    return stretches


def _kind_at(t: float, lookups: list[tuple[list[_Piece], list[float]]]) -> StretchKind:
    """The most cautious kind at ``t``. It's ``lost`` only if every track is, as in the renderer:
    one track still seeing the target is enough."""
    kinds: list[StretchKind] = []
    for pieces, starts in lookups:
        i = bisect_right(starts, t) - 1
        if i >= 0 and t < pieces[i][1]:
            kinds.append(pieces[i][2])
    return max(kinds, key=_CAUTION.__getitem__, default="lost")
