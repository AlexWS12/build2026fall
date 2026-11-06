"""Loads the hand-written mask audit scenario. A plain module: tests/masks has no __init__.py."""

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from vibe.contract import OpLog, TimelineState, Track

HERE = Path(__file__).parent
TRACKS = TypeAdapter(list[Track])


def scenario_tracks() -> list[Track]:
    return TRACKS.validate_json((HERE / "mask_audit.tracks.json").read_bytes())


def scenario_state() -> TimelineState:
    return OpLog.from_jsonl((HERE / "mask_audit.jsonl").read_text(encoding="utf-8")).state()


def scenario_expected() -> dict[str, Any]:
    return json.loads((HERE / "mask_audit.expected.json").read_text(encoding="utf-8"))
