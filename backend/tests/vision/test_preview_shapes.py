"""``preview_shapes``: cached masks as small PNGs over the preview's padded box (F-15)."""

import base64
import zlib

import numpy as np

from vibe.contract import TimelineState, Track
from vibe.modules.vision import rle
from vibe.modules.vision.masks import MaskEntry, MasksFile, RleMask
from vibe.modules.vision.preview_shapes import MAX_SIDE, preview_shapes

BOX = (0.25, 0.25, 0.75, 0.75)


def track(box=BOX) -> Track:
    return Track.model_validate(
        {
            "track_id": "trk_1",
            "label": "person",
            "source": "detector",
            "keyframes": [{"t": 0.0, "box": list(box), "conf": 0.9, "state": "detected"}],
        }
    )


def state(padding: float = 0.15) -> TimelineState:
    span = {"start": 0, "end": None, "effect": {"type": "blur", "padding": padding},
            "op_id": "op_1", "request_id": "r_1"}  # fmt: skip
    return TimelineState.model_validate({"schema_version": 1, "masks": {"trk_1": [span]}})


def masks_file(box=BOX, size=(200, 400)) -> MasksFile:
    mask = np.zeros(size, dtype=bool)
    h, w = size
    mask[int(0.3 * h) : int(0.7 * h), int(0.4 * w) : int(0.6 * w)] = True  # a narrow person
    encoded = rle.encode(mask)
    entry = MaskEntry(t=0.0, box=box, mask=RleMask(size=size, counts=encoded["counts"]))
    return MasksFile(segmenter="stub", settings={}, track_id="trk_1", entries=[entry])


def png_size(data_url: str) -> tuple[int, int]:
    raw = base64.b64decode(data_url.split(",", 1)[1])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n" and raw[12:16] == b"IHDR"
    return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")


def test_a_cached_mask_becomes_a_small_png_over_the_padded_box() -> None:
    shapes = preview_shapes(state(), [track()], [masks_file()])

    (frame,) = shapes["trk_1"]
    assert frame.t == 0.0
    width, height = png_size(frame.image)
    assert max(width, height) <= MAX_SIDE
    # The padded box is 0.65 of the frame each way: 260 x 130 px, so twice as wide as tall.
    assert abs(width / height - 2) < 0.1


def test_a_mask_for_older_keyframes_is_ignored() -> None:
    moved = (0.2, 0.25, 0.7, 0.75)  # the track was re-analysed: its box is different now
    assert preview_shapes(state(), [track(moved)], [masks_file()]) == {}


def test_a_track_that_isnt_masked_gets_no_shape() -> None:
    unmasked = TimelineState.model_validate({"schema_version": 1})
    assert preview_shapes(unmasked, [track()], [masks_file()]) == {}


def test_the_png_alpha_is_the_shape() -> None:
    (frame,) = preview_shapes(state(padding=0), [track()], [masks_file()])["trk_1"]
    raw = base64.b64decode(frame.image.split(",", 1)[1])
    width, height = png_size(frame.image)
    idat = raw[raw.index(b"IDAT") + 4 : raw.index(b"IEND") - 8]
    rows = np.frombuffer(zlib.decompress(idat), dtype=np.uint8).reshape(height, 1 + width * 4)
    alpha = rows[:, 1:].reshape(height, width, 4)[..., 3]
    # Inside the person (the middle) is opaque; the corners of the box are clear.
    assert alpha[height // 2, width // 2] == 255
    assert alpha[0, 0] == 0 and alpha[-1, -1] == 0
