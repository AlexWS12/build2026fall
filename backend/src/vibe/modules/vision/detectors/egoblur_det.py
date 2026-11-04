"""EgoBlur face or license-plate detector (Meta; Apache 2.0, gated download). D-015's offline
privacy detector: built for anonymization, high recall, heavy.

The weights are TorchScript files from Meta's EgoBlur page (accept the terms, download, keep
them outside the repo) and are passed as ``weights``. The call follows the EgoBlur repo's
script: a (3, H, W) ``uint8`` tensor in **BGR** order in, ``(boxes, labels, scores, sizes)`` in
pixel boxes out, then NMS. **Not run in CI and not checked against the Gen2 files:** confirm the
channel order and output on a known image before trusting its numbers.
"""

from pathlib import Path

import numpy as np

from vibe.modules.vision.detection import Detection, NotInstalled, normalize_box
from vibe.modules.vision.detectors._torch import device, import_torch

KINDS = {"face": "face", "plate": "license_plate"}


class EgoBlurDetector:
    def __init__(
        self,
        kind: str = "face",
        weights: str = "",
        threshold: float = 0.1,
        nms_iou: float = 0.3,
        bgr: bool = True,
        device_name: str = "auto",
    ) -> None:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {sorted(KINDS)}, got {kind!r}")
        if not weights or not Path(weights).is_file():
            raise NotInstalled(
                f"egoblur-{kind} needs its TorchScript weights: download them from Meta's EgoBlur "
                f"page and pass weights=PATH (got {weights!r}). Never commit them."
            )
        torch = import_torch(f"egoblur-{kind}")
        try:
            import torchvision.ops
        except ImportError as e:
            raise NotInstalled(f"egoblur-{kind} needs torchvision for NMS ({e})") from e
        self._torch, self._nms = torch, torchvision.ops.nms
        self.device = device(torch, device_name)
        self._model = torch.jit.load(weights, map_location=self.device).eval()
        self._label = KINDS[kind]
        self._threshold, self._nms_iou, self._bgr = threshold, nms_iou, bgr
        self.name = f"egoblur-{kind}-{Path(weights).stem}"
        self.labels = frozenset({self._label})
        self.settings = {"threshold": threshold, "nms_iou": nms_iou, "bgr": bgr}

    def detect(self, image: np.ndarray) -> list[Detection]:
        torch = self._torch
        height, width = image.shape[:2]
        pixels = image[..., ::-1] if self._bgr else image
        tensor = torch.from_numpy(np.ascontiguousarray(pixels.transpose(2, 0, 1))).to(self.device)
        with torch.inference_mode():
            boxes, _, scores, _ = self._model(tensor)
            keep = self._nms(boxes, scores, self._nms_iou)
            boxes, scores = boxes[keep], scores[keep]
        found = []
        for (x1, y1, x2, y2), score in zip(boxes.tolist(), scores.tolist(), strict=True):
            box = normalize_box(x1, y1, x2, y2, width, height)
            if score >= self._threshold and box is not None:
                found.append(Detection(self._label, box, float(score)))
        return found
