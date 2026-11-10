"""Fixtures for the F-10 tests. The clip helpers are in ``vision_helpers``."""

from pathlib import Path

import pytest
from vision_helpers import square_frames, write_clip


@pytest.fixture
def square_clip(tmp_path: Path) -> Path:
    return write_clip(tmp_path / "squares.mp4", square_frames())
