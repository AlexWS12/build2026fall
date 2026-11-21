"""Which plans wait for a person's go-ahead before they're applied (F-44).

docs/ARCHITECTURE.md, "Fail safe by edit type": a cut is not made when unsure, it is previewed.
A plan that cuts or keeps is proposed first; everything else applies at once, as before.
Inpainting joins when it exists.
"""

from vibe.agent.plan import Plan

HEAVY_TOOLS = frozenset({"cut_range", "keep_range"})


def needs_preview(plan: Plan) -> bool:
    """True if any step of ``plan`` removes footage, so a person should see it first."""
    return any(call.tool in HEAVY_TOOLS for call in plan.calls)
