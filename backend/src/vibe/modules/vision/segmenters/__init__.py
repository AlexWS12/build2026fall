"""Every segmenter F-15 can run, by name, with its license. Picked with a spec string like the
detectors: ``sam2``, ``sam2:model=facebook/sam2.1-hiera-small``, ``stub``.

A segmenter gets one RGB frame and some normalized boxes and returns one full-frame boolean mask
per box, or ``None`` where it found nothing.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from vibe.modules.vision.detection import Box
from vibe.modules.vision.detectors import parse_spec, resolve_options
from vibe.modules.vision.segmenters.sam2_seg import Sam2Segmenter
from vibe.modules.vision.segmenters.stub import EllipseSegmenter


class Segmenter(Protocol):
    name: str
    """Model and size. Goes into the masks' cache key."""
    settings: Mapping[str, object]
    """Everything else that changes its output, hashed into the cache key with ``name``."""

    def segment(self, image: np.ndarray, boxes: Sequence[Box]) -> list[np.ndarray | None]:
        """One (height, width) boolean mask per box in the RGB ``uint8`` frame, ``None`` where
        there is none."""
        ...


@dataclass(frozen=True)
class SegmenterInfo:
    make: Callable[..., Segmenter]
    license: str
    needs: str
    defaults: dict[str, object] = field(default_factory=dict)


SEGMENTERS: dict[str, SegmenterInfo] = {
    "stub": SegmenterInfo(EllipseSegmenter, "ours", "nothing"),
    "sam2": SegmenterInfo(Sam2Segmenter, "Apache 2.0 (code and weights)", "torch, transformers"),
}


def make_segmenter(spec: str) -> Segmenter:
    """Build a segmenter from a spec. Raises ``NotInstalled`` if its packages are missing,
    ``ValueError`` for an unknown name or a bad option."""
    name, raw = parse_spec(spec)
    info = SEGMENTERS.get(name)
    if info is None:
        raise ValueError(f"unknown segmenter {name!r}; known: {', '.join(SEGMENTERS)}")
    return info.make(**{**info.defaults, **resolve_options(name, raw, info.make)})
