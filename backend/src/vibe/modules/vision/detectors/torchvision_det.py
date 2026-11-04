"""torchvision Faster R-CNN, COCO classes (BSD-3 code, weights from torchvision). The baseline:
it installs with PyTorch alone and runs on a CPU, so it's the floor every other model must beat.
"""

import numpy as np

from vibe.modules.vision.detection import Detection, NotInstalled, normalize_box
from vibe.modules.vision.detectors._torch import TORCH_INSTALL, device, import_torch

MODELS = {
    "frcnn": ("fasterrcnn_resnet50_fpn_v2", "FasterRCNN_ResNet50_FPN_V2_Weights"),
    "frcnn-mobile": (
        "fasterrcnn_mobilenet_v3_large_fpn",
        "FasterRCNN_MobileNet_V3_Large_FPN_Weights",
    ),
}


class TorchvisionDetector:
    def __init__(self, model: str = "frcnn", threshold: float = 0.1, device_name: str = "auto"):
        if model not in MODELS:
            raise ValueError(f"model must be one of {sorted(MODELS)}, got {model!r}")
        torch = import_torch("torchvision")
        try:
            from torchvision.models import detection
        except ImportError as e:
            raise NotInstalled(f"torchvision isn't installed: {TORCH_INSTALL}") from e
        build, weights_name = MODELS[model]
        weights = getattr(detection, weights_name).DEFAULT
        self.device = device(torch, device_name)
        self._torch = torch
        self._model = getattr(detection, build)(weights=weights, box_score_thresh=threshold)
        self._model.eval().to(self.device)
        self._categories: list[str] = weights.meta["categories"]
        self.name = f"tv-{model}"
        self.labels = frozenset(c for c in self._categories if c not in {"__background__", "N/A"})
        self.settings = {"threshold": threshold, "weights": str(weights)}

    def detect(self, image: np.ndarray) -> list[Detection]:
        torch = self._torch
        height, width = image.shape[:2]
        tensor = torch.from_numpy(image).permute(2, 0, 1).to(self.device).float() / 255  # RGB
        with torch.inference_mode():
            out = self._model([tensor])[0]
        found = []
        for (x1, y1, x2, y2), label, score in zip(
            out["boxes"].tolist(), out["labels"].tolist(), out["scores"].tolist(), strict=True
        ):
            box = normalize_box(x1, y1, x2, y2, width, height)
            if box is not None:
                found.append(Detection(self._categories[label], box, float(score)))
        return found
