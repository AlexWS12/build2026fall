"""COCO run-length encoding for binary masks, in numpy: no pycocotools needed (F-15).

A mask is flattened column by column (Fortran order) into alternating runs of 0s and 1s,
starting with 0s; the run lengths are then packed into the compressed ASCII string COCO uses
(``{"size": [h, w], "counts": "..."}``). The strings match ``pycocotools.mask.encode``, so other
tools can read the cache.
"""

from typing import TypedDict

import numpy as np


class Rle(TypedDict):
    size: list[int]
    """[height, width]."""
    counts: str


def encode(mask: np.ndarray) -> Rle:
    """A (height, width) boolean mask as compressed COCO RLE."""
    if mask.ndim != 2:
        raise ValueError(f"mask must be (height, width), got {mask.shape}")
    height, width = mask.shape
    flat = np.asarray(mask, dtype=bool).ravel(order="F")
    if flat.size == 0:
        return {"size": [height, width], "counts": ""}
    changes = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    counts = np.diff(np.concatenate(([0], changes, [flat.size]))).tolist()
    if flat[0]:
        counts.insert(0, 0)  # runs start with 0s
    return {"size": [height, width], "counts": _pack(counts)}


def decode(rle: Rle) -> np.ndarray:
    """Compressed COCO RLE back to a (height, width) boolean mask."""
    height, width = rle["size"]
    counts = _unpack(rle["counts"])
    if sum(counts) != height * width:
        raise ValueError(f"RLE covers {sum(counts)} pixels, not {height}x{width}")
    values = np.arange(len(counts)) % 2 == 1
    flat = np.repeat(values, counts)
    return flat.reshape((height, width), order="F")


def area(rle: Rle) -> int:
    """Pixels set, without decoding the mask."""
    return sum(_unpack(rle["counts"])[1::2])


def _pack(counts: list[int]) -> str:
    """Each count (minus the one two before it, after the first three) as 5-bit groups with a
    continuation bit, offset into printable ASCII (pycocotools' ``rleToString``)."""
    out = []
    for i, count in enumerate(counts):
        x = count - counts[i - 2] if i > 2 else count
        more = True
        while more:
            c = x & 0x1F
            x >>= 5
            more = x != -1 if c & 0x10 else x != 0
            if more:
                c |= 0x20
            out.append(chr(c + 48))
    return "".join(out)


def _unpack(text: str) -> list[int]:
    """The inverse of ``_pack`` (pycocotools' ``rleFrString``)."""
    counts: list[int] = []
    p = 0
    while p < len(text):
        x = k = 0
        more = True
        while more:
            if p >= len(text):
                raise ValueError("RLE string ends in the middle of a count")
            c = ord(text[p]) - 48
            x |= (c & 0x1F) << (5 * k)
            more = bool(c & 0x20)
            p += 1
            k += 1
            if not more and c & 0x10:
                x |= -1 << (5 * k)
        if len(counts) > 2:
            x += counts[-2]
        if x < 0:
            raise ValueError("RLE string has a negative run")
        counts.append(x)
    return counts
