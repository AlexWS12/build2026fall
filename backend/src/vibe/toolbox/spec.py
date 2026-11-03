"""What a tool is: a name, typed arguments, a result, and the function that runs it."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from vibe.toolbox.context import ToolContext

ToolKind = Literal["query", "edit"]
"""``query`` tools look at the video and return candidates; ``edit`` tools write ops."""

Returns = Literal["track_ids", "time_range", "none"]
"""What a tool's result is, so a planner's ``$ref`` to it can be type-checked before running."""


def describe_errors(exc: ValidationError) -> str:
    """One line per problem, ``field.path: message``: the same wording for the planner's plan
    errors and for ``ToolError``s, so the LLM retry and the user see consistent messages."""
    return "; ".join(
        f"{'.'.join(map(str, e['loc'])) or '(args)'}: {e['msg']}" for e in exc.errors()
    )


class ToolError(ValueError):
    """A tool call that can't be done: bad arguments, missing context, or an op the contract
    refuses. Nothing is written. The message names the tool and says what's wrong."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: ToolKind
    description: str
    args: type[BaseModel]
    result: type[BaseModel]
    """What ``run`` returns; ``call_tool`` checks it."""
    returns: Returns
    run: Callable[[Any, ToolContext], BaseModel]
    """Called with validated arguments (an instance of ``args``)."""
    ref_args: Mapping[str, Returns] = field(default_factory=dict)
    """Arguments a plan may fill with a ``$ref`` to an earlier step, and the result type."""
    ref_required: frozenset[str] = frozenset()
    """Arguments a plan must fill with a ``$ref``, never a literal: mask targets have to come
    from a detection step, so a planner can't make up track ids."""
