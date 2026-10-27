"""Derived state: what the renderer reads. Produced by ``fold(ops)``, never written by hand.

Every list of spans is sorted by start, and spans for the same key never overlap. Each span
keeps the ``op_id`` and ``request_id`` that produced it, so the UI can say where a mask came
from and undo the right request.
"""

from pydantic import Field, model_validator

from vibe.contract.common import SCHEMA_VERSION, AudioFilterType, Effect, Model, TimeRange
from vibe.contract.ops import Rule


class Span(Model):
    start: float = Field(ge=0)
    end: float | None = Field(default=None, description="Null means until the end of the video.")
    op_id: str
    request_id: str

    @model_validator(mode="after")
    def _end_after_start(self) -> "Span":
        if self.end is not None and self.end <= self.start:
            raise ValueError(f"end ({self.end}) must be after start ({self.start})")
        return self

    def contains(self, t: float) -> bool:
        return self.start <= t and (self.end is None or t < self.end)


class MaskSpan(Span):
    effect: Effect


class MuteSpan(Span):
    speaker: str | None = Field(description="Null = all audio.")


class AudioFilterSpan(Span):
    filter: AudioFilterType
    strength: float | None = None


class TimelineState(Model):
    schema_version: int = SCHEMA_VERSION
    masks: dict[str, list[MaskSpan]] = Field(
        default_factory=dict, description="Per track id: when it's masked and how."
    )
    cuts: list[TimeRange] = Field(
        default_factory=list, description="Source time ranges removed from the output, merged."
    )
    mutes: list[MuteSpan] = Field(default_factory=list)
    audio_filters: list[AudioFilterSpan] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list, description="Active standing rules.")

    # --- Per-frame lookups (the per-frame renderer and live mode use these) ---------------

    def masks_at(self, t: float) -> dict[str, MaskSpan]:
        """Which tracks are masked at source time ``t``, and with what."""
        found = {}
        for track_id, spans in self.masks.items():
            for span in spans:
                if span.contains(t):
                    found[track_id] = span
                    break
        return found

    def rules_at(self, t: float) -> list[Rule]:
        return [r for r in self.rules if r.time_range is None or _in_range(r.time_range, t)]

    def is_cut(self, t: float) -> bool:
        return any(_in_range(c, t) for c in self.cuts)

    # --- Source time <-> output time ------------------------------------------------------

    def source_to_output(self, t: float) -> float:
        """Where source time ``t`` lands in the output. Inside a cut, it lands at the cut point."""
        removed = 0.0
        for cut in self.cuts:
            if cut.start >= t:
                break
            end = t if cut.end is None else min(cut.end, t)
            removed += end - cut.start
        return t - removed

    def output_to_source(self, t: float) -> float:
        """The source time shown at output time ``t``. At a cut point, the frame after the cut."""
        if t < 0:
            raise ValueError(f"output time must be >= 0, got {t}")
        source = t
        for cut in self.cuts:
            if cut.start > source:
                break
            if cut.end is None:
                raise ValueError(f"output time {t} is past the end of the output")
            source += cut.end - cut.start
        return source


def _in_range(r: TimeRange, t: float) -> bool:
    return r.start <= t and (r.end is None or t < r.end)
