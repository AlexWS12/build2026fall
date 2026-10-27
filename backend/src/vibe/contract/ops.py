"""Ops: the edits the agent and the UI write to the append-only log.

One model per ``kind``. ``Op`` is the union of all of them, told apart by ``kind``, so a
malformed op (an unknown kind, a typo in a field, a bad effect) fails validation loudly.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from vibe.contract.common import (
    AudioFilterType,
    Effect,
    Model,
    Source,
    TimeRange,
    new_id,
)


class OpBase(Model):
    op_id: str = Field(default_factory=lambda: new_id("op"), description="Unique in the log.")
    request_id: str = Field(
        description="The chat message or UI action that caused this op. Undo works per request."
    )
    source: Source
    created_at: datetime | None = None


# --- Visual masks -------------------------------------------------------------------------


class AddMask(OpBase):
    """Hide targets with an effect over a time range (the whole clip if ``time_range`` is null).

    Where targets overlap an earlier mask, this op wins over its own range.
    """

    kind: Literal["add_mask"] = "add_mask"
    targets: list[str] = Field(min_length=1, description="Track ids from the analysis cache.")
    effect: Effect
    time_range: TimeRange | None = None


class RemoveMask(OpBase):
    """Unmask targets over a time range (the whole clip if null). No-op where not masked."""

    kind: Literal["remove_mask"] = "remove_mask"
    targets: list[str] = Field(min_length=1)
    time_range: TimeRange | None = None


class SetEffect(OpBase):
    """Change the effect where targets are already masked. Doesn't mask anything new."""

    kind: Literal["set_effect"] = "set_effect"
    targets: list[str] = Field(min_length=1)
    effect: Effect
    time_range: TimeRange | None = None


# --- Cuts ---------------------------------------------------------------------------------


class Cut(OpBase):
    """Remove a time range from the output. Overlapping cuts merge."""

    kind: Literal["cut"] = "cut"
    time_range: TimeRange


class Uncut(OpBase):
    """Bring back a time range that was cut. No-op where nothing was cut."""

    kind: Literal["uncut"] = "uncut"
    time_range: TimeRange


# --- Audio --------------------------------------------------------------------------------


class Mute(OpBase):
    """Silence a time range: all audio if ``speaker`` is null, else one speaker's voice."""

    kind: Literal["mute"] = "mute"
    speaker: str | None = Field(default=None, description="Speaker id from diarization.")
    time_range: TimeRange | None = None


class Unmute(OpBase):
    """Undo a mute over a time range, for the same ``speaker`` (null = the all-audio mute)."""

    kind: Literal["unmute"] = "unmute"
    speaker: str | None = None
    time_range: TimeRange | None = None


class AddAudioFilter(OpBase):
    """Apply an audio filter (e.g. denoise) over a time range (the whole clip if null)."""

    kind: Literal["add_audio_filter"] = "add_audio_filter"
    filter: AudioFilterType
    strength: float | None = Field(default=None, ge=0, le=1)
    time_range: TimeRange | None = None


class RemoveAudioFilter(OpBase):
    kind: Literal["remove_audio_filter"] = "remove_audio_filter"
    filter: AudioFilterType
    time_range: TimeRange | None = None


# --- Standing rules (live mode, also works offline) ---------------------------------------


class Rule(Model):
    """A standing rule, evaluated on every frame against whatever the detectors find.

    "Only I am visible" is ``category="person", except_identities=["id_me"]``. Fail closed:
    a detection whose identity is unknown or unsure is **not** excepted, so it gets masked.
    """

    rule_id: str = Field(default_factory=lambda: new_id("rule"))
    category: str = Field(description='Detector label to mask, e.g. "person", "face".')
    except_identities: list[str] = Field(
        default_factory=list, description="Enrolled identity ids that stay visible."
    )
    effect: Effect
    time_range: TimeRange | None = None


class AddRule(OpBase):
    kind: Literal["add_rule"] = "add_rule"
    rule: Rule


class RemoveRule(OpBase):
    kind: Literal["remove_rule"] = "remove_rule"
    rule_id: str


# --- History ------------------------------------------------------------------------------


class Undo(OpBase):
    """Undo every op of ``undo_request_id``, which must be the latest request still in effect."""

    kind: Literal["undo"] = "undo"
    undo_request_id: str


class Redo(OpBase):
    """Redo the most recently undone request. Any new edit after an undo clears redo."""

    kind: Literal["redo"] = "redo"
    redo_request_id: str


EditOp = (
    AddMask
    | RemoveMask
    | SetEffect
    | Cut
    | Uncut
    | Mute
    | Unmute
    | AddAudioFilter
    | RemoveAudioFilter
    | AddRule
    | RemoveRule
)
"""Ops that change the timeline. Undo and redo act on these, a whole request at a time."""

HistoryOp = Undo | Redo

Op = Annotated[EditOp | HistoryOp, Field(discriminator="kind")]

OP_ADAPTER: TypeAdapter[Op] = TypeAdapter(Op)
"""Validate or serialize one op of any kind: ``OP_ADAPTER.validate_json(line)``."""
