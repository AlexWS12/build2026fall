"""YuNet face detector from the OpenCV Zoo (MIT). D-015's live face detector: tiny and fast,
weaker on small, turned or hidden faces than EgoBlur. Its boxes are tight (about eyes to mouth),
so ``grow`` scales each box around its centre to cover the whole face for masking.

Install: ``uv pip install opencv-python``, and download ``face_detection_yunet_2023mar.onnx``
from the OpenCV Zoo (keep it outside the repo), passed as ``weights``.
"""

from pathlib import Path

import numpy as np

from vibe.modules.vision.detection import Detection, NotInstalled, normalize_box


class YuNetDetector:
    def __init__(
        self,
        weights: str = "",
        threshold: float = 0.3,
        nms_iou: float = 0.3,
        top_k: int = 5000,
        grow: float = 1.0,
    ) -> None:
        try:
            import cv2
        except ImportError as e:
            raise NotInstalled(f"yunet needs `uv pip install opencv-python` ({e})") from e
        if not weights or not Path(weights).is_file():
            raise NotInstalled(
                "yunet needs face_detection_yunet_2023mar.onnx from the OpenCV Zoo: pass "
                f"weights=PATH (got {weights!r})"
            )
        if not 1.0 <= grow <= 4.0:
            raise ValueError(f"yunet grow must be between 1 and 4, got {grow}")
        self._model = cv2.FaceDetectorYN.create(weights, "", (320, 320), threshold, nms_iou, top_k)
        self._size: tuple[int, int] | None = None
        self.name = f"yunet-{Path(weights).stem}"
        self.labels = frozenset({"face"})
        self.settings: dict[str, object] = {
            "threshold": threshold,
            "nms_iou": nms_iou,
            "top_k": top_k,
        }
        if grow != 1.0:  # only when used, so keys made before this option stay the same
            self.settings["grow"] = grow
        self._grow = grow

    def detect(self, image: np.ndarray) -> list[Detection]:
        height, width = image.shape[:2]
        if self._size != (width, height):
            self._model.setInputSize((width, height))
            self._size = (width, height)
        _, faces = self._model.detect(np.ascontiguousarray(image[..., ::-1]))  # OpenCV wants BGR
        found = []
        for row in faces if faces is not None else []:
            x, y, w, h, score = float(row[0]), float(row[1]), float(row[2]), float(row[3]), row[-1]
            # YuNet's box is the inner face (about eyes to mouth); grow it around its centre so a
            # mask covers forehead, cheeks and chin too. Clipped to the frame below.
            cx, cy = x + w / 2, y + h / 2
            w, h = w * self._grow, h * self._grow
            box = normalize_box(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, width, height)
            if box is not None:
                found.append(Detection("face", box, float(score)))
        return found
