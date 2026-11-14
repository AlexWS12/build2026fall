"""Loads the hand-written mask audit scenario, and makes the clips the export tests render.

A plain module: tests/masks has no __init__.py.
"""

import json
from pathlib import Path
from typing import Any

import av
import numpy as np
from pydantic import TypeAdapter

from vibe.contract import OpLog, TimelineState, Track

HERE = Path(__file__).parent
TRACKS = TypeAdapter(list[Track])
WIDTH, HEIGHT = 64, 48
"""The size of every clip ``write_clip`` makes."""


def scenario_tracks() -> list[Track]:
    return TRACKS.validate_json((HERE / "mask_audit.tracks.json").read_bytes())


def scenario_state() -> TimelineState:
    return OpLog.from_jsonl((HERE / "mask_audit.jsonl").read_text(encoding="utf-8")).state()


def scenario_expected() -> dict[str, Any]:
    return json.loads((HERE / "mask_audit.expected.json").read_text(encoding="utf-8"))


def texture() -> np.ndarray:
    """A picture with detail everywhere, so a blur or a pixelate shows on it, and nothing darker
    than 64, so a black box shows too. It's grey cells of 2 × 2 pixels, because the clip's encoder
    smears single pixels and stores colour at half the resolution."""
    cells = np.random.default_rng(0).integers(64, 256, (HEIGHT // 2, WIDTH // 2), dtype=np.uint8)
    grey = np.repeat(np.repeat(cells, 2, axis=0), 2, axis=1)
    return np.stack([grey, grey, grey], axis=-1)


def write_clip(path: Path, frames: int, fps: int, picture: np.ndarray | None = None) -> Path:
    """A small silent clip, encoded with PyAV's own ffmpeg, so no ffmpeg install is needed.

    Every frame is ``picture`` when one is given (see ``texture``). Without one, each frame is a
    flat grey: enough for the audit, but a blur changes nothing on it.

    MPEG-4 is lossy and doesn't give every frame the same pixels, even from one picture, so
    compare a rendered frame with the clip's frame at the same time, not with ``picture``.
    """
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height, stream.pix_fmt = WIDTH, HEIGHT, "yuv420p"
        for i in range(frames):
            image = picture
            if image is None:
                image = np.full((HEIGHT, WIDTH, 3), i * 5 % 256, dtype=np.uint8)
            container.mux(stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")))
        container.mux(stream.encode())
    return path
