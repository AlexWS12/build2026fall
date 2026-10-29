"""DRAFT tool registry the planner targets until the real toolbox (F-06) lands.

**A stand-in, not the toolbox.** Tool names and arguments follow docs/ARCHITECTURE.md
("Toolbox") and get replaced by F-06's registry, which is a shared file. Only the shapes the
planner needs are here: a name, whether the tool queries or edits, an argument model, and what
it returns so a later step can refer to it with ``{"$ref": "<step_id>"}``.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, Field

from vibe.contract.common import Effect, Model, TimeRange

ToolKind = Literal["query", "edit"]

Returns = Literal["track_ids", "time_range", "none"]
"""What a tool's result is, so a ``$ref`` to it can be type-checked before anything runs."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: ToolKind
    args: type[BaseModel]
    returns: Returns
    ref_args: Mapping[str, Returns] = field(default_factory=dict)
    """Arguments that may be a ``$ref`` to an earlier step, and the result type they take."""
    ref_required: frozenset[str] = frozenset()
    """Arguments a plan must fill with a ``$ref``, never a literal: mask targets have to come
    from a detection step, so a planner can't make up track ids."""


class FindObjectsArgs(Model):
    label: str = Field(min_length=1, description='Detector label, e.g. "face", "person".')


class ResolveTimeArgs(Model):
    phrase: str = Field(
        min_length=1, description='A time phrase that needs the video, e.g. "first half".'
    )


class AddMaskArgs(Model):
    targets: list[str] = Field(min_length=1, description="Track ids.")
    effect: Effect
    time_range: TimeRange | None = None


class CutRangeArgs(Model):
    time_range: TimeRange


class MuteArgs(Model):
    time_range: TimeRange | None = None
    speaker: str | None = None


_TOOLS = (
    ToolSpec("find_objects", "query", FindObjectsArgs, "track_ids"),
    ToolSpec("resolve_time", "query", ResolveTimeArgs, "time_range"),
    ToolSpec(
        "add_mask",
        "edit",
        AddMaskArgs,
        "none",
        {"targets": "track_ids", "time_range": "time_range"},
        frozenset({"targets"}),
    ),
    ToolSpec("cut_range", "edit", CutRangeArgs, "none", {"time_range": "time_range"}),
    ToolSpec("mute", "edit", MuteArgs, "none", {"time_range": "time_range"}),
)

DRAFT_REGISTRY: Mapping[str, ToolSpec] = MappingProxyType({t.name: t for t in _TOOLS})
