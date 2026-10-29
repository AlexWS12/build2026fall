"""Toolbox: typed tools that write ops to the edit timeline (F-06). Names and args are shared.

The planner, the API and the UI's hand edits all go through ``call_tool``, so every edit lands
in the same op log and undo works the same way (docs/ARCHITECTURE.md, "Toolbox").
"""

from vibe.toolbox.context import OpSink, ToolContext
from vibe.toolbox.registry import TOOLS, call_tool
from vibe.toolbox.spec import Returns, ToolError, ToolKind, ToolSpec

__all__ = [
    "TOOLS",
    "OpSink",
    "Returns",
    "ToolContext",
    "ToolError",
    "ToolKind",
    "ToolSpec",
    "call_tool",
]
