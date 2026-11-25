"""COCO RLE in numpy (F-15): the strings match pycocotools, and masks round-trip.

The expected strings were made with ``pycocotools.mask.encode`` (2.0.x); a one-off script also
compared 204 random masks, up to 1080×1920, against it. pycocotools isn't a dependency.
"""

import numpy as np
import pytest

from vibe.modules.vision import rle


def _block() -> np.ndarray:
    m = np.zeros((7, 5), bool)
    m[1:4, 2:5] = True
    return m


def _corners() -> np.ndarray:
    m = np.zeros((4, 6), bool)
    m[0, 0] = m[3, 5] = True
    return m


@pytest.mark.parametrize(
    ("mask", "counts"),
    [(_block(), "?34000O"), (np.ones((3, 4), bool), "0<"), (_corners(), "01f00")],
)
def test_strings_match_pycocotools(mask: np.ndarray, counts: str) -> None:
    encoded = rle.encode(mask)
    assert encoded == {"size": list(mask.shape), "counts": counts}
    assert (rle.decode(encoded) == mask).all()
    assert rle.area(encoded) == mask.sum()


def test_random_masks_round_trip() -> None:
    rng = np.random.default_rng(1)
    for _ in range(50):
        h, w = rng.integers(1, 60, 2)
        mask = rng.random((h, w)) < rng.random()
        assert (rle.decode(rle.encode(mask)) == mask).all()


def test_a_large_person_shaped_mask_is_small_as_text() -> None:
    mask = np.zeros((1080, 1920), bool)
    mask[100:900, 700:1100] = True
    mask[200:400, 600:1300] = True
    encoded = rle.encode(mask)
    assert len(encoded["counts"]) < 4000
    assert (rle.decode(encoded) == mask).all()


def test_bad_input_is_refused() -> None:
    with pytest.raises(ValueError, match="height, width"):
        rle.encode(np.zeros((2, 2, 3), bool))
    with pytest.raises(ValueError, match="pixels"):
        rle.decode({"size": [5, 5], "counts": "0<"})
