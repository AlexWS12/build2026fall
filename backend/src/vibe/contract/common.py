"""Building blocks shared by ops, state and tracks: version, errors, time ranges, effects, ids."""

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1
"""Version of the contract. Bump it on any change that breaks a reader or writer."""


class ContractError(ValueError):
    """An op log that breaks a contract rule (e.g. undoing a request that isn't the latest)."""


class Model(BaseModel):
    """Base for every contract model: immutable, and unknown fields are rejected, not ignored."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def new_id(prefix: str) -> str:
    """A short random id like ``op_3f9a1c2e4b5d``. Order comes from the log, never from ids."""
    return f"{prefix}_{uuid4().hex[:12]}"


class TimeRange(Model):
    """A span of **source** time in seconds, start included, end excluded.

    ``end`` is ``None`` for "until the end of the video". Times always refer to the original
    video, so a cut never shifts other edits (docs/ARCHITECTURE.md, "The contract").
    """

    start: float = Field(ge=0, description="Seconds from the start of the source video.")
    end: float | None = Field(
        default=None, description="Seconds, excluded. Null means until the end of the video."
    )

    @model_validator(mode="after")
    def _end_after_start(self) -> "TimeRange":
        if self.end is not None and self.end <= self.start:
            raise ValueError(f"end ({self.end}) must be after start ({self.start})")
        return self


WHOLE_CLIP = TimeRange(start=0)

EffectType = Literal["blur", "pixelate", "box"]
"""How a visual target is hidden. ``box`` is a solid black box."""

AudioFilterType = Literal["denoise"]

Source = Literal["agent", "user"]
"""Who wrote an op: the agent (from a chat request) or the user (a hand edit in the UI)."""


class Effect(Model):
    """How to hide a visual target. The renderer owns what each setting looks like."""

    type: EffectType
    padding: float = Field(
        default=0.15, ge=0, le=1, description="Grow the box by this fraction of its size."
    )
    strength: float | None = Field(
        default=None, ge=0, le=1, description="0 = lightest, 1 = strongest. Null = default."
    )
