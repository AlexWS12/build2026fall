"""The op log in memory and as JSON Lines. Where the file lives is up to storage (R-2), not here.

File format: the first line is a header ``{"schema_version": 1}``, then one op per line, oldest
first. Appending an edit is appending a line.
"""

import json
from collections.abc import Iterable, Iterator

from vibe.contract.common import SCHEMA_VERSION, ContractError, Source
from vibe.contract.fold import History, fold, history
from vibe.contract.ops import OP_ADAPTER, Op, Redo, Undo
from vibe.contract.state import TimelineState


class OpLog:
    """An append-only list of ops that refuses any op that would break the contract."""

    def __init__(self, ops: Iterable[Op] = ()) -> None:
        self._ops: list[Op] = []
        for op in ops:
            self.append(op)

    def __iter__(self) -> Iterator[Op]:
        return iter(self._ops)

    def __len__(self) -> int:
        return len(self._ops)

    @property
    def ops(self) -> tuple[Op, ...]:
        return tuple(self._ops)

    def append(self, op: Op) -> Op:
        """Add an op after checking the whole log still folds. Returns the op."""
        candidate = [*self._ops, op]
        fold(candidate)  # raises ContractError (or ValueError) and leaves the log unchanged
        self._ops.append(op)
        return op

    def history(self) -> History:
        return history(self._ops)

    def state(self) -> TimelineState:
        return fold(self._ops)

    def undo(self, request_id: str, source: Source = "user") -> Undo:
        """Undo the latest request in effect. ``request_id`` is the undo action's own id."""
        target = self.history().can_undo
        if target is None:
            raise ContractError("nothing to undo")
        op = Undo(request_id=request_id, source=source, undo_request_id=target)
        self.append(op)
        return op

    def redo(self, request_id: str, source: Source = "user") -> Redo:
        target = self.history().can_redo
        if target is None:
            raise ContractError("nothing to redo")
        op = Redo(request_id=request_id, source=source, redo_request_id=target)
        self.append(op)
        return op

    # --- JSON Lines -----------------------------------------------------------------------

    def to_jsonl(self) -> str:
        lines = [json.dumps({"schema_version": SCHEMA_VERSION})]
        lines += [OP_ADAPTER.dump_json(op, exclude_none=True).decode() for op in self._ops]
        return "\n".join(lines) + "\n"

    @classmethod
    def from_jsonl(cls, text: str) -> "OpLog":
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            raise ContractError("empty op log: expected a schema_version header line")
        header = json.loads(lines[0])
        version = header.get("schema_version") if isinstance(header, dict) else None
        if version != SCHEMA_VERSION:
            raise ContractError(
                f"op log has schema_version {version!r}; this code reads {SCHEMA_VERSION}"
            )
        return cls(OP_ADAPTER.validate_json(line) for line in lines[1:])
