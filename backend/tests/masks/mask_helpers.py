"""Loads the hand-written mask audit scenario. A plain module: tests/masks has no __init__.py."""

import json
from pathlib import Path
from typing import Any

import av
import numpy as np
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


def write_clip(path: Path, frames: int, fps: int) -> Path:
    """A small silent clip, encoded with PyAV's own ffmpeg, so no ffmpeg install is needed."""
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for i in range(frames):
            image = np.full((48, 64, 3), i * 5 % 256, dtype=np.uint8)
            container.mux(stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")))
        container.mux(stream.encode())
    return path
