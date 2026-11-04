"""Clip helpers for the F-10 tests: colored squares moving over black, encoded with PyAV.

PyAV bundles its own ffmpeg libraries, so these run everywhere (CI, Windows without ffmpeg on
PATH) and no video is committed. MPEG-4 is lossy, so detectors here use a color tolerance.
"""

from pathlib import Path

import av
import numpy as np

from vibe.video import Frame

WIDTH, HEIGHT, FPS = 96, 64, 10
RED, BLUE = (255, 0, 0), (0, 0, 255)


def square_frames(n: int = 10) -> list[np.ndarray]:
    """A red 20x20 square moving right 4 px a frame, and a still blue one in the bottom right."""
    frames = []
    for i in range(n):
        image = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        image[10:30, 4 + 4 * i : 24 + 4 * i] = RED
        image[44:56, 76:88] = BLUE
        frames.append(image)
    return frames


def as_frames(images: list[np.ndarray]) -> list[Frame]:
    return [Frame(index=i, time=i / FPS, image=image) for i, image in enumerate(images)]


def write_clip(path: Path, images: list[np.ndarray]) -> Path:
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=FPS)
        stream.width, stream.height, stream.pix_fmt = WIDTH, HEIGHT, "yuv420p"
        stream.bit_rate = 2_000_000
        for image in images:
            for packet in stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path
