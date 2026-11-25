"""SAM 2.1 through Hugging Face transformers, prompted with each track's box (F-15, D-015).

Code and weights are Apache 2.0. One image-encoder pass per frame is shared by every box in it;
each box then gets one mask from the decoder. ``facebook/sam2.1-hiera-tiny`` fits a 6 GB laptop
GPU; ``-small`` and ``-base-plus`` are the next sizes, ``-large`` is for a bigger GPU.

transformers and torch are installed by hand, like the detectors' packages (vision docstring).
The weights download into the Hugging Face cache the first time a model is built.
"""

from collections.abc import Sequence

import numpy as np

from vibe.modules.vision.detection import Box, NotInstalled
from vibe.modules.vision.detectors._torch import device, import_torch

TRANSFORMERS_INSTALL = "uv pip install 'transformers>=4.56' (it has Sam2Model)"


class Sam2Segmenter:
    def __init__(
        self, model: str = "facebook/sam2.1-hiera-tiny", device_name: str = "auto"
    ) -> None:
        torch = import_torch("SAM 2")
        try:
            import transformers
            from transformers import Sam2Model, Sam2Processor
        except ImportError as e:
            raise NotInstalled(f"SAM 2 needs Sam2Model: {TRANSFORMERS_INSTALL}") from e
        self._torch = torch
        self.device = device(torch, device_name)
        self._processor = Sam2Processor.from_pretrained(model)
        self._model = Sam2Model.from_pretrained(model).to(self.device).eval()
        self.name = model.rsplit("/", 1)[-1]
        self.settings = {"model": model, "transformers": transformers.__version__}

    def segment(self, image: np.ndarray, boxes: Sequence[Box]) -> list[np.ndarray | None]:
        if not boxes:
            return []
        torch = self._torch
        height, width = image.shape[:2]
        prompts = [[x1 * width, y1 * height, x2 * width, y2 * height] for x1, y1, x2, y2 in boxes]
        inputs = self._processor(images=image, input_boxes=[prompts], return_tensors="pt").to(
            self.device
        )
        with torch.inference_mode():
            out = self._model(**inputs, multimask_output=False)
        masks = self._processor.post_process_masks(
            out.pred_masks.cpu(), inputs["original_sizes"].cpu()
        )[0]  # (boxes, 1, height, width)
        found: list[np.ndarray | None] = []
        for mask in masks:
            m = mask[0].numpy().astype(bool)
            found.append(m if m.any() else None)
        return found
