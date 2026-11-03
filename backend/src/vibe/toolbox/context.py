"""What a tool call runs against: who asked, the timeline to write to, and the video's facts."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from vibe.contract import Op, Track
from vibe.contract.common import Source


class OpSink(Protocol):
    """Where edit tools write. ``contract.OpLog`` (in memory) and ``storage.Project`` (on disk)
    both fit: ``append`` validates the op against the whole log and raises before writing."""

    def append(self, op: Op) -> Op: ...


@dataclass(frozen=True)
class ToolContext:
    request_id: str
    """The chat message or UI action; every op it causes shares it, so undo works per request."""
    source: Source
    log: OpSink | None = None
    """The timeline edit tools write to. Query tools don't need one."""
    duration: float | None = None
    """Video length in seconds, for ``resolve_time``."""
    tracks: Sequence[Track] | None = None
    """Tracks from the analysis cache (stub: given by the caller; real: F-08 / F-10). ``None``
    means the video hasn't been analysed; ``[]`` means it was and nothing was found."""
