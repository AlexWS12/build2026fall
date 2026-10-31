"""The request set: F-05's ``requests_draft.json`` plus R-1's ``requests_llm.json``."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from vibe.agent.bench.extras import EXTRAS
from vibe.toolbox import TOOLS, ToolSpec

REQUESTS_DIR = Path(__file__).resolve().parents[4] / "tests" / "agent"
DEFAULT_SETS = (REQUESTS_DIR / "requests_draft.json", REQUESTS_DIR / "requests_llm.json")

ToolSet = Literal["core", "extras"]


@dataclass(frozen=True)
class Case:
    request: str
    expect: Literal["plan", "clarify"]
    golds: tuple[tuple[Mapping[str, Any], ...], ...]
    """The gold plan, then any alternatives that are just as correct. Empty for clarify."""
    tools: ToolSet
    category: str
    source: str
    """Which file it came from (``draft`` or ``llm``)."""

    @property
    def id(self) -> str:
        return f"{self.tools}:{self.request}"


def registry_for(case: Case) -> Mapping[str, ToolSpec]:
    return EXTRAS if case.tools == "extras" else TOOLS


def load_cases(paths: Sequence[Path] = DEFAULT_SETS) -> list[Case]:
    cases: list[Case] = []
    for path in paths:
        source = path.stem.removeprefix("requests_")
        for entry in json.loads(path.read_text(encoding="utf-8"))["requests"]:
            golds = ()
            if entry["expect"] == "plan":
                golds = (tuple(entry["calls"]),) + tuple(
                    tuple(alt) for alt in entry.get("alternatives", ())
                )
            cases.append(
                Case(
                    request=entry["request"],
                    expect=entry["expect"],
                    golds=golds,
                    tools=entry.get("tools", "core"),
                    category=entry.get("category", f"template {entry['expect']}"),
                    source=source,
                )
            )
    ids = [c.id for c in cases]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate requests in {[str(p) for p in paths]}")
    return cases
