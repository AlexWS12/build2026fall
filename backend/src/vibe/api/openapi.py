"""The committed OpenAPI spec, which the UI generates its TypeScript types from.

``python -m vibe.api`` regenerates ``openapi.json``; ``python -m vibe.api --check`` fails if
it is out of date (a test runs it too).
"""

import json
from pathlib import Path

from vibe.api.app import app

SPEC_PATH = Path(__file__).parent / "openapi.json"


def spec_text() -> str:
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def is_stale() -> bool:
    return not SPEC_PATH.exists() or SPEC_PATH.read_text(encoding="utf-8") != spec_text()


def write_spec() -> bool:
    """Write the spec if it changed; return whether it did."""
    if not is_stale():
        return False
    SPEC_PATH.write_text(spec_text(), encoding="utf-8", newline="\n")
    return True
