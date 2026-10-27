"""Generated JSON schemas and the example timelines that ship with the contract.

``python -m vibe.contract`` regenerates ``schema/*.json`` and ``examples/*.state.json``;
``python -m vibe.contract --check`` fails if any of them is out of date (a test runs it too).
"""

import json
from pathlib import Path

from pydantic import TypeAdapter

from vibe.contract.log import OpLog
from vibe.contract.ops import OP_ADAPTER
from vibe.contract.state import TimelineState
from vibe.contract.tracks import Track

HERE = Path(__file__).parent
SCHEMA_DIR = HERE / "schema"
EXAMPLES_DIR = HERE / "examples"


def schemas() -> dict[str, dict]:
    """File name -> JSON schema, for every type other modules and the UI exchange."""
    return {
        "op.schema.json": OP_ADAPTER.json_schema(),
        "state.schema.json": TimelineState.model_json_schema(),
        "tracks.schema.json": TypeAdapter(list[Track]).json_schema(),
    }


def example_logs() -> dict[str, OpLog]:
    """Example name -> its op log, loaded (and so validated) from ``examples/<name>.jsonl``."""
    return {
        p.stem: OpLog.from_jsonl(p.read_text(encoding="utf-8"))
        for p in sorted(EXAMPLES_DIR.glob("*.jsonl"))
    }


def example_tracks() -> dict[str, list[Track]]:
    adapter = TypeAdapter(list[Track])
    return {
        p.name.removesuffix(".tracks.json"): adapter.validate_json(p.read_bytes())
        for p in sorted(EXAMPLES_DIR.glob("*.tracks.json"))
    }


def _dump(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def generated_files() -> dict[Path, str]:
    """Every generated file and the content it should have."""
    files = {SCHEMA_DIR / name: _dump(schema) for name, schema in schemas().items()}
    for name, log in example_logs().items():
        state = log.state().model_dump(mode="json")
        files[EXAMPLES_DIR / f"{name}.state.json"] = _dump(state)
    return files


def stale_files() -> list[Path]:
    """Generated files that are missing or differ from what the code produces now."""
    return [
        path
        for path, content in generated_files().items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]


def write_generated() -> list[Path]:
    written = []
    for path in stale_files():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(generated_files()[path], encoding="utf-8", newline="\n")
        written.append(path)
    return written
