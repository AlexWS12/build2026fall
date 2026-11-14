"""Toolbox: typed tools that write ops to the edit timeline (F-06). Names and args are shared.

The planner, the API and the UI's hand edits all go through ``call_tool``, so every edit lands
in the same op log and undo works the same way (docs/ARCHITECTURE.md, "Toolbox").
"""

from vibe.toolbox.context import Describer, DescriberError, OpSink, ToolContext, Verdict
from vibe.toolbox.registry import TOOLS, call_tool
from vibe.toolbox.spec import Returns, ToolError, ToolKind, ToolSpec, describe_errors

__all__ = [
    "TOOLS",
    "Describer",
    "DescriberError",
    "OpSink",
    "Returns",
    "ToolContext",
    "ToolError",
    "ToolKind",
    "ToolSpec",
    "Verdict",
    "call_tool",
    "describe_errors",
]
