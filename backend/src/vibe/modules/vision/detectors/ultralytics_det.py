"""Ultralytics YOLO and YOLOE. **AGPL-3.0: for the F-10 comparison only, never the default path.**

D-015 (proposed) keeps Ultralytics off the default path, because loading anything through it
puts AGPL terms on the app. This adapter exists to measure what that choice costs:

- YOLO (``yolo11s.pt``, ...): the industry-default closed-set detector, COCO classes;
- YOLOE with ``prompts``: open vocabulary in real time ("coffee mug"), the Knight Hacks model.

So ``ultralytics`` is deliberately **not** in ``pyproject.toml``: nothing imports it unless this
detector is asked for, and it's installed by hand only on the machine running the comparison
(``uv pip install ultralytics``). Using it beyond that is a team decision.

Written against the ultralytics 8.3 API and not run in CI; check ``set_classes`` for YOLOE
against the installed version.
"""

from pathlib import Path

import numpy as np

from vibe.modules.vision.detection import Detection, NotInstalled, normalize_box

LICENSE_NOTE = "AGPL-3.0, comparison only (D-015)"


class UltralyticsDetector:
    def __init__(
        self,
        weights: str = "yolo11s.pt",
        threshold: float = 0.1,
        prompts: str | None = None,
        device_name: str = "auto",
    ) -> None:
        try:
            import ultralytics
            from PIL import Image
        except ImportError as e:
            raise NotInstalled(
                f"YOLO needs `uv pip install ultralytics` ({LICENSE_NOTE}) ({e})"
            ) from e
        names = [p.strip() for p in prompts.split("|") if p.strip()] if prompts else []
        if names:
            self._model = ultralytics.YOLOE(weights)
            try:
                self._model.set_classes(names, self._model.get_text_pe(names))
            except (AttributeError, TypeError):
                self._model.set_classes(names)  # newer versions embed the prompts themselves
        else:
            self._model = ultralytics.YOLO(weights)
        self._image = Image
        self._threshold = threshold
        self._device = None if device_name == "auto" else device_name
        self.name = Path(weights).stem
        self.labels = frozenset(names) if names else frozenset(self._model.names.values())
        self.settings = {
            "threshold": threshold,
            "prompts": names,
            "ultralytics": ultralytics.__version__,
        }

    def detect(self, image: np.ndarray) -> list[Detection]:
        # Ultralytics reads a NumPy array as BGR (OpenCV order); a PIL image is RGB, like ours.
        result = self._model.predict(
            self._image.fromarray(image), conf=self._threshold, device=self._device, verbose=False
        )[0]
        boxes = result.boxes
        found = []
        for (x1, y1, x2, y2), score, cls in zip(
            boxes.xyxyn.tolist(), boxes.conf.tolist(), boxes.cls.tolist(), strict=True
        ):
            box = normalize_box(x1, y1, x2, y2, 1, 1)  # already normalized: clip and drop empty
            if box is not None:
                found.append(Detection(result.names[int(cls)], box, float(score)))
        return found
