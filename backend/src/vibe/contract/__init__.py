"""Edit timeline contract: op models, fold, undo, JSON schema (F-02). Shared file (HANDOFF.md).

Everything upstream (agent, toolbox, UI hand edits) writes ``Op``s to an ``OpLog``; everything
downstream (renderer, preview, live) reads the ``TimelineState`` that ``fold`` derives from it,
together with the ``Track``s from the analysis cache. Rules: docs/ARCHITECTURE.md, "The contract".
"""

from vibe.contract.common import (
    SCHEMA_VERSION,
    WHOLE_CLIP,
    ContractError,
    Effect,
    TimeRange,
    new_id,
)
from vibe.contract.fold import History, fold, history
from vibe.contract.log import OpLog
from vibe.contract.ops import (
    OP_ADAPTER,
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
    Rule,
    SetEffect,
    Uncut,
    Undo,
    Unmute,
)
from vibe.contract.state import AudioFilterSpan, MaskSpan, MuteSpan, TimelineState
from vibe.contract.tracks import Keyframe, Track

__all__ = [
    "OP_ADAPTER",
    "SCHEMA_VERSION",
    "WHOLE_CLIP",
    "AddAudioFilter",
    "AddMask",
    "AddRule",
    "AudioFilterSpan",
    "ContractError",
    "Cut",
    "Effect",
    "History",
    "Keyframe",
    "MaskSpan",
    "Mute",
    "MuteSpan",
    "Op",
    "OpLog",
    "Redo",
    "RemoveAudioFilter",
    "RemoveMask",
    "RemoveRule",
    "Rule",
    "SetEffect",
    "TimeRange",
    "TimelineState",
    "Track",
    "Uncut",
    "Undo",
    "Unmute",
    "fold",
    "history",
    "new_id",
]
