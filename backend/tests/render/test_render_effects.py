"""Effects: each one hides its rectangle, even at strength 0, and leaves the rest alone."""

import numpy as np
import pytest

from vibe.contract import Effect
from vibe.render.effects import PIXELATE_BLOCKS, apply_effect

RECT = (40, 30, 200, 150)  # 160 × 120 px


def noise(height: int = 240, width: int = 320, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, (height, width, 3), dtype=np.uint8)


def detail(image: np.ndarray) -> float:
    """Mean difference between neighbouring pixels: high for texture, low once blurred."""
    a = image.astype(np.float64)
    return float(np.abs(np.diff(a, axis=0)).mean() + np.abs(np.diff(a, axis=1)).mean())


def inside(image: np.ndarray) -> np.ndarray:
    x1, y1, x2, y2 = RECT
    return image[y1:y2, x1:x2]


def outside_mask(image: np.ndarray) -> np.ndarray:
    mask = np.ones(image.shape[:2], dtype=bool)
    x1, y1, x2, y2 = RECT
    mask[y1:y2, x1:x2] = False
    return mask


def apply(effect: Effect, image: np.ndarray) -> np.ndarray:
    out = image.copy()
    apply_effect(out, RECT, effect)
    return out


@pytest.mark.parametrize("kind", ["blur", "pixelate", "box"])
def test_only_the_rectangle_changes(kind: str) -> None:
    image = noise()
    out = apply(Effect(type=kind), image)
    keep = outside_mask(image)
    assert np.array_equal(out[keep], image[keep])
    assert not np.array_equal(inside(out), inside(image))


def test_box_is_solid_black() -> None:
    assert not inside(apply(Effect(type="box"), noise())).any()


@pytest.mark.parametrize("strength", [0.0, None, 1.0])
def test_blur_removes_detail_even_at_strength_0(strength: float | None) -> None:
    image = noise()
    out = apply(Effect(type="blur", strength=strength), image)
    assert detail(inside(out)) < 0.1 * detail(inside(image))


def test_stronger_blur_removes_more() -> None:
    image = noise()
    light = detail(inside(apply(Effect(type="blur", strength=0.0), image)))
    strong = detail(inside(apply(Effect(type="blur", strength=1.0), image)))
    assert strong < light


@pytest.mark.parametrize("strength", [0.0, None, 1.0])
def test_pixelate_makes_flat_blocks(strength: float | None) -> None:
    out = inside(apply(Effect(type="pixelate", strength=strength), noise()))
    # Count runs of equal colour along the shorter side (120 px): at most PIXELATE_BLOCKS[0].
    column = out[:, 0, 0]
    runs = 1 + int(np.count_nonzero(np.diff(column.astype(int))))
    assert 2 <= runs <= PIXELATE_BLOCKS[0]
    # Inside a block every pixel has the same colour.
    assert len(np.unique(out[:5, :5].reshape(-1, 3), axis=0)) == 1


def test_stronger_pixelate_has_bigger_blocks() -> None:
    def blocks(strength: float) -> int:
        column = inside(apply(Effect(type="pixelate", strength=strength), noise()))[:, 0, 0]
        return 1 + int(np.count_nonzero(np.diff(column.astype(int))))

    assert blocks(1.0) < blocks(0.0)


def test_flat_colour_stays_the_same_colour() -> None:
    image = np.full((240, 320, 3), 77, dtype=np.uint8)
    for kind in ("blur", "pixelate"):
        assert np.array_equal(apply(Effect(type=kind), image), image)


@pytest.mark.parametrize("kind", ["blur", "pixelate", "box"])
def test_effects_are_deterministic(kind: str) -> None:
    image = noise(seed=3)
    assert np.array_equal(apply(Effect(type=kind), image), apply(Effect(type=kind), image))


@pytest.mark.parametrize("rect", [(5, 5, 5, 50), (5, 5, 50, 5)])
def test_empty_rectangle_changes_nothing(rect: tuple[int, int, int, int]) -> None:
    image = noise()
    out = image.copy()
    apply_effect(out, rect, Effect(type="box"))
    assert np.array_equal(out, image)


@pytest.mark.parametrize("kind", ["blur", "pixelate"])
def test_tiny_rectangles_work(kind: str) -> None:
    image = noise()
    out = image.copy()
    apply_effect(out, (10, 10, 11, 12), Effect(type=kind))  # 1 × 2 px
    assert out.shape == image.shape
