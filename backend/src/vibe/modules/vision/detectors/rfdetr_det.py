"""RF-DETR, COCO classes (Apache 2.0 code and N/S/M/L weights). D-015's proposed detector.

Install: ``uv pip install rfdetr`` (pulls PyTorch; on a GPU install the CUDA wheel first, see
``_torch.TORCH_INSTALL``). XL/2XL are under a different license (PML 1.0) and aren't offered.
**Written against the rfdetr 1.x API and not run in CI:** check class names and ``predict``
against the installed version.
"""

import numpy as np

from vibe.modules.vision.detection import Detection, NotInstalled, normalize_box

SIZES = {"n": "RFDETRNano", "s": "RFDETRSmall", "m": "RFDETRMedium", "l": "RFDETRLarge"}


class RFDetrDetector:
    def __init__(self, size: str = "l", threshold: float = 0.1) -> None:
        if size not in SIZES:
            raise ValueError(f"size must be one of {sorted(SIZES)}, got {size!r}")
        try:
            import rfdetr
            from PIL import Image
        except ImportError as e:
            raise NotInstalled(f"rf-detr needs `uv pip install rfdetr` ({e})") from e
        try:
            from rfdetr.assets.coco_classes import COCO_CLASSES  # rfdetr >= 1.9
        except ImportError:
            try:
                from rfdetr.util.coco_classes import COCO_CLASSES  # older 1.x
            except ImportError as e:
                raise NotInstalled(f"this rfdetr version has no COCO class names ({e})") from e
        model_class = getattr(rfdetr, SIZES[size], None)
        if model_class is None:
            raise NotInstalled(f"this rfdetr version has no {SIZES[size]}; upgrade rfdetr")
        self._model = model_class()
        # rfdetr picks CUDA itself and moves the weights on the first predict; report it so the
        # CLI doesn't claim "cpu" for an adapter that has no `device` attribute.
        self.device = str(getattr(getattr(self._model, "model", None), "device", "cpu"))
        self._image = Image
        self._classes = COCO_CLASSES
        self.name = f"rfdetr-{size}"
        self.labels = frozenset(COCO_CLASSES.values())
        self.settings = {"threshold": threshold, "rfdetr": getattr(rfdetr, "__version__", "?")}
        self._threshold = threshold

    def detect(self, image: np.ndarray) -> list[Detection]:
        height, width = image.shape[:2]
        # A PIL image is unambiguously RGB, which is what our frames are.
        out = self._model.predict(self._image.fromarray(image), threshold=self._threshold)
        found = []
        for (x1, y1, x2, y2), score, cls in zip(
            out.xyxy.tolist(), out.confidence.tolist(), out.class_id.tolist(), strict=True
        ):
            box = normalize_box(x1, y1, x2, y2, width, height)
            if box is not None:
                found.append(Detection(self._classes[int(cls)], box, float(score)))
        return found
