"""Every detector F-10 can run, by name, with its license (D-015 keeps AGPL off the default path).

A detector is picked with a spec string: ``name`` or ``name:option=value,option=value``, e.g.
``rfdetr-l``, ``yolo:weights=yolo11m.pt``, ``egoblur-face:weights=C:/models/face.jit``,
``yoloe:prompts=coffee mug|poster``. Each module imports its model package only when built, so
listing detectors needs nothing installed.
"""

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import get_args

from vibe.modules.vision.detection import Detector
from vibe.modules.vision.detectors.egoblur_det import EgoBlurDetector
from vibe.modules.vision.detectors.rfdetr_det import RFDetrDetector
from vibe.modules.vision.detectors.stub import ColorDetector
from vibe.modules.vision.detectors.torchvision_det import TorchvisionDetector
from vibe.modules.vision.detectors.ultralytics_det import LICENSE_NOTE, UltralyticsDetector
from vibe.modules.vision.detectors.yunet_det import YuNetDetector


@dataclass(frozen=True)
class DetectorInfo:
    make: Callable[..., Detector]
    finds: str
    license: str
    needs: str
    defaults: dict[str, object] = field(default_factory=dict)

    @property
    def permissive(self) -> bool:
        return "AGPL" not in self.license and "GPL" not in self.license


def _rfdetr(size: str) -> DetectorInfo:
    return DetectorInfo(
        RFDetrDetector, "COCO classes", "Apache 2.0", "rfdetr", defaults={"size": size}
    )


DETECTORS: dict[str, DetectorInfo] = {
    "stub": DetectorInfo(
        ColorDetector,
        "one block of color= red|green|blue|white, as label= (tests, no model)",
        "ours",
        "nothing",
    ),
    "tv-frcnn": DetectorInfo(
        TorchvisionDetector, "COCO classes", "BSD-3", "torch, torchvision", {"model": "frcnn"}
    ),
    "tv-frcnn-mobile": DetectorInfo(
        TorchvisionDetector,
        "COCO classes, faster on CPU",
        "BSD-3",
        "torch, torchvision",
        {"model": "frcnn-mobile"},
    ),
    "rfdetr-n": _rfdetr("n"),
    "rfdetr-s": _rfdetr("s"),
    "rfdetr-m": _rfdetr("m"),
    "rfdetr-l": _rfdetr("l"),
    "egoblur-face": DetectorInfo(
        EgoBlurDetector,
        "face (needs weights=PATH)",
        "Apache 2.0 (gated download)",
        "torch, torchvision",
        {"kind": "face"},
    ),
    "egoblur-plate": DetectorInfo(
        EgoBlurDetector,
        "license_plate (needs weights=PATH)",
        "Apache 2.0 (gated download)",
        "torch, torchvision",
        {"kind": "plate"},
    ),
    "yunet": DetectorInfo(
        YuNetDetector, "face (needs weights=PATH to the .onnx)", "MIT", "opencv-python"
    ),
    "yolo": DetectorInfo(
        UltralyticsDetector,
        "COCO classes (weights=yolo11s.pt by default)",
        LICENSE_NOTE,
        "ultralytics",
    ),
    "yoloe": DetectorInfo(
        UltralyticsDetector,
        "anything named in prompts=a|b|c (open vocabulary)",
        LICENSE_NOTE,
        "ultralytics",
        {"weights": "yoloe-11s-seg.pt"},
    ),
}


def parse_spec(spec: str) -> tuple[str, dict[str, str]]:
    """``"yolo:weights=yolo11m.pt,threshold=0.2"`` -> ``("yolo", {"weights": "yolo11m.pt",
    "threshold": "0.2"})``. Values stay text: only the detector knows what type each option is."""
    name, _, rest = spec.partition(":")
    options: dict[str, str] = {}
    for part in rest.split(","):
        if not part.strip():
            continue
        key, eq, value = part.partition("=")
        if not eq or not key.strip():
            raise ValueError(f"detector option {part!r} in {spec!r} must look like name=value")
        options[key.strip()] = value.strip()
    return name.strip(), options


def _convert(detector: str, option: str, text: str, annotation: object) -> object:
    """``text`` as the type the detector's parameter is declared with (``str | None`` is str)."""
    kinds = [a for a in get_args(annotation) if a is not type(None)] or [annotation]
    kind = str if kinds[0] is inspect.Parameter.empty else kinds[0]
    if kind is str:
        return text  # a prompt or a weights file named "1984" is still text
    try:
        if kind is bool:
            if text.lower() not in {"true", "false"}:
                raise ValueError(text)
            return text.lower() == "true"
        return kind(text)
    except ValueError:
        raise ValueError(
            f"bad options for detector {detector!r}: {option}={text!r} is not a {kind.__name__}"
        ) from None


def resolve_options(name: str, raw: dict[str, str]) -> dict[str, object]:
    """Convert a spec's text options by the declared types of the detector's parameters."""
    info = DETECTORS[name]
    params = inspect.signature(info.make).parameters
    unknown = [k for k in raw if k not in params]
    if unknown:
        raise ValueError(
            f"bad options for detector {name!r}: unknown {', '.join(unknown)} "
            f"(options: {', '.join(params)})"
        )
    return {k: _convert(name, k, v, params[k].annotation) for k, v in raw.items()}


def make_detector(spec: str) -> Detector:
    """Build a detector from a spec. Raises ``NotInstalled`` if its package or weights are
    missing, ``ValueError`` for an unknown name or a bad option."""
    name, raw = parse_spec(spec)
    info = DETECTORS.get(name)
    if info is None:
        raise ValueError(f"unknown detector {name!r}; known: {', '.join(DETECTORS)}")
    return info.make(**{**info.defaults, **resolve_options(name, raw)})
