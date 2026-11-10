"""Shared by the PyTorch detectors: importing torch with a useful error, and picking a device."""

from types import ModuleType

from vibe.modules.vision.detection import NotInstalled

TORCH_INSTALL = (
    "uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128 "
    "(NVIDIA GPU; needs a driver for CUDA 12.8+), or `uv pip install torch torchvision` (CPU)"
)


def import_torch(what: str) -> ModuleType:
    try:
        import torch
    except ImportError as e:
        raise NotInstalled(f"{what} needs PyTorch: {TORCH_INSTALL}") from e
    return torch


def device(torch: ModuleType, wanted: str) -> str:
    """``auto`` means the GPU if PyTorch can see one, else the CPU."""
    if wanted == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return wanted
