"""Fold: op log -> derived state. Pure: no files, no cache, no network, same input -> same output.

Rules (docs/ARCHITECTURE.md, "The contract"):
- **Later ops win.** Each op paints its time range over what came before, per target (masks),
  per speaker (mutes) or per filter. Cuts add up into one merged list.
- **Undo is linear and works per request.** ``undo`` must name the latest request still in
  effect, and ``redo`` the most recently undone one. A new edit after an undo clears redo.
- **A request's ops are contiguous** in the log: the server applies one request at a time.
"""

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from vibe.contract.common import ContractError, TimeRange
from vibe.contract.ops import (
    AddAudioFilter,
    AddMask,
    AddRule,
    Cut,
    Mute,
    Op,
    Redo,
    RemoveAudioFilter,
    RemoveMask,
    RemoveRule,
    SetEffect,
    Uncut,
    Undo,
    Unmute,
)
from vibe.contract.ops import Rule as RuleModel
from vibe.contract.state import AudioFilterSpan, MaskSpan, MuteSpan, TimelineState

# --- History: which requests are in effect ------------------------------------------------


@dataclass(frozen=True)
class History:
    """Which requests are in effect after a log, and what undo or redo would act on next."""

    active: tuple[str, ...]
    """Requests in effect, oldest first."""
    undone: frozenset[str]
    redoable: tuple[str, ...]
    """Undone requests that redo can bring back, the next one last."""

    @property
    def can_undo(self) -> str | None:
        return self.active[-1] if self.active else None

    @property
    def can_redo(self) -> str | None:
        return self.redoable[-1] if self.redoable else None


def history(ops: Iterable[Op]) -> History:
    """Replay undo and redo over the log. Raises ``ContractError`` on an illegal sequence."""
    active: list[str] = []
    undone: set[str] = set()
    redoable: list[str] = []
    seen_ops: set[str] = set()

    for op in ops:
        if op.op_id in seen_ops:
            raise ContractError(f"duplicate op_id {op.op_id!r}")
        seen_ops.add(op.op_id)

        if isinstance(op, Undo):
            if not active:
                raise ContractError(f"{op.op_id}: nothing to undo")
            if active[-1] != op.undo_request_id:
                raise ContractError(
                    f"{op.op_id}: can only undo the latest request {active[-1]!r}, "
                    f"not {op.undo_request_id!r}"
                )
            redoable.append(active.pop())
            undone.add(op.undo_request_id)
        elif isinstance(op, Redo):
            if not redoable or redoable[-1] != op.redo_request_id:
                expected = redoable[-1] if redoable else None
                raise ContractError(
                    f"{op.op_id}: can only redo {expected!r}, not {op.redo_request_id!r}"
                )
            active.append(redoable.pop())
            undone.discard(op.redo_request_id)
        else:
            rid = op.request_id
            if rid in undone:
                raise ContractError(f"{op.op_id}: request {rid!r} was undone; use a new request")
            if rid in active and active[-1] != rid:
                raise ContractError(
                    f"{op.op_id}: request {rid!r} continues after a newer request; "
                    "a request's ops must be contiguous"
                )
            if not active or active[-1] != rid:
                active.append(rid)
            redoable.clear()

    return History(tuple(active), frozenset(undone), tuple(redoable))


# --- Painting time ranges -----------------------------------------------------------------


@dataclass(frozen=True)
class _Seg:
    start: float
    end: float  # math.inf = until the end of the video
    value: Any


def _bounds(r: TimeRange | None) -> tuple[float, float]:
    if r is None:
        return 0.0, math.inf
    return r.start, math.inf if r.end is None else r.end


def _merge(segs: list[_Seg]) -> list[_Seg]:
    segs = sorted(segs, key=lambda s: s.start)
    out: list[_Seg] = []
    for s in segs:
        if out and out[-1].end == s.start and out[-1].value == s.value:
            out[-1] = _Seg(out[-1].start, s.end, s.value)
        else:
            out.append(s)
    return out


def _paint(segs: list[_Seg], r: TimeRange | None, value: Any) -> list[_Seg]:
    """Overwrite ``r`` with ``value`` (or erase it, if ``value`` is None)."""
    start, end = _bounds(r)
    out: list[_Seg] = []
    for s in segs:
        if s.end <= start or s.start >= end:
            out.append(s)
            continue
        if s.start < start:
            out.append(_Seg(s.start, start, s.value))
        if s.end > end:
            out.append(_Seg(end, s.end, s.value))
    if value is not None:
        out.append(_Seg(start, end, value))
    return _merge(out)


def _update(segs: list[_Seg], r: TimeRange | None, fn: Callable[[Any], Any]) -> list[_Seg]:
    """Change the value of existing segments inside ``r``; leave gaps as they are."""
    start, end = _bounds(r)
    out: list[_Seg] = []
    for s in segs:
        if s.end <= start or s.start >= end:
            out.append(s)
            continue
        if s.start < start:
            out.append(_Seg(s.start, start, s.value))
        out.append(_Seg(max(s.start, start), min(s.end, end), fn(s.value)))
        if s.end > end:
            out.append(_Seg(end, s.end, s.value))
    return _merge(out)


def _end(x: float) -> float | None:
    return None if x == math.inf else x


# --- Fold ---------------------------------------------------------------------------------


def fold(ops: Iterable[Op]) -> TimelineState:
    """Compute the state the renderer reads from an op log."""
    ops = list(ops)
    undone = history(ops).undone

    masks: dict[str, list[_Seg]] = {}
    cuts: list[_Seg] = []
    mutes: dict[str | None, list[_Seg]] = {}
    filters: dict[str, list[_Seg]] = {}
    rules: dict[str, RuleModel] = {}

    for op in ops:
        if isinstance(op, Undo | Redo) or op.request_id in undone:
            continue
        prov = (op.op_id, op.request_id)
        match op:
            case AddMask():
                for t in op.targets:
                    masks[t] = _paint(masks.get(t, []), op.time_range, (op.effect, *prov))
            case RemoveMask():
                for t in op.targets:
                    masks[t] = _paint(masks.get(t, []), op.time_range, None)
            case SetEffect():
                value = (op.effect, *prov)
                for t in op.targets:
                    masks[t] = _update(masks.get(t, []), op.time_range, lambda _, v=value: v)
            case Cut():
                cuts = _paint(cuts, op.time_range, True)
            case Uncut():
                cuts = _paint(cuts, op.time_range, None)
            case Mute():
                mutes[op.speaker] = _paint(mutes.get(op.speaker, []), op.time_range, prov)
            case Unmute():
                mutes[op.speaker] = _paint(mutes.get(op.speaker, []), op.time_range, None)
            case AddAudioFilter():
                value = (op.strength, *prov)
                filters[op.filter] = _paint(filters.get(op.filter, []), op.time_range, value)
            case RemoveAudioFilter():
                filters[op.filter] = _paint(filters.get(op.filter, []), op.time_range, None)
            case AddRule():
                if op.rule.rule_id in rules:
                    raise ContractError(f"{op.op_id}: rule {op.rule.rule_id!r} already exists")
                rules[op.rule.rule_id] = op.rule
            case RemoveRule():
                if op.rule_id not in rules:
                    raise ContractError(f"{op.op_id}: no active rule {op.rule_id!r}")
                del rules[op.rule_id]

    return TimelineState(
        masks={
            t: [
                MaskSpan(
                    start=s.start,
                    end=_end(s.end),
                    effect=s.value[0],
                    op_id=s.value[1],
                    request_id=s.value[2],
                )
                for s in segs
            ]
            for t, segs in masks.items()
            if segs
        },
        cuts=[TimeRange(start=s.start, end=_end(s.end)) for s in cuts],
        mutes=sorted(
            (
                MuteSpan(
                    start=s.start,
                    end=_end(s.end),
                    speaker=speaker,
                    op_id=s.value[0],
                    request_id=s.value[1],
                )
                for speaker, segs in mutes.items()
                for s in segs
            ),
            key=lambda m: m.start,
        ),
        audio_filters=sorted(
            (
                AudioFilterSpan(
                    start=s.start,
                    end=_end(s.end),
                    filter=name,
                    strength=s.value[0],
                    op_id=s.value[1],
                    request_id=s.value[2],
                )
                for name, segs in filters.items()
                for s in segs
            ),
            key=lambda f: f.start,
        ),
        rules=list(rules.values()),
    )
