"""What can this machine run? GPU, driver, PyTorch build and compute tier (``doctor``).

Works with nothing installed: it asks ``nvidia-smi`` (NVIDIA driver), PyTorch (if installed) and
the OS. Paste its output in the team chat to confirm your row in the GPU survey
(``docs/research/f08-gpu-survey.md``).

Tiers are those of docs/ARCHITECTURE.md, "Compute". The thresholds below are rules of thumb for
planning who runs what, not measurements.
"""

import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass, field

BLACKWELL_MIN_DRIVER = 570
"""PyTorch ``cu128`` wheels need CUDA 12.8, so an NVIDIA driver of 570 or later."""
TIER2_MIN_VRAM_GB = 12.0


@dataclass
class Gpu:
    name: str
    vram_gb: float | None
    compute_capability: str | None = None
    """e.g. ``12.0`` for Blackwell (RTX 50 series, RTX PRO Blackwell)."""

    @property
    def blackwell(self) -> bool:
        if self.compute_capability:
            return self.compute_capability.split(".")[0] == "12"
        return any(s in self.name.upper() for s in ("RTX 50", "BLACKWELL"))


@dataclass
class Hardware:
    os: str
    cpu: str
    gpus: list[Gpu] = field(default_factory=list)
    driver: str | None = None
    apple_gpu: bool = False
    memory_gb: float | None = None
    torch: str | None = None
    torch_cuda: str | None = None
    torch_cuda_ok: bool | None = None
    torch_mps_ok: bool | None = None
    torch_arches: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        out = asdict(self)
        out["tier"], out["tier_reason"] = tier(self)
        out["warnings"] = warnings(self)
        return out


def _run(cmd: list[str]) -> str | None:
    if shutil.which(cmd[0]) is None:
        return None
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def _float(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def nvidia_smi() -> tuple[list[Gpu], str | None]:
    """NVIDIA GPUs and the driver version, or ``([], None)`` without an NVIDIA driver."""
    fields = "name,memory.total,driver_version,compute_cap"
    out = _run(["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"])
    if out is None:  # older drivers don't know compute_cap
        out = _run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ]
        )
    if not out:
        return [], None
    gpus, driver = [], None
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        mib = _float(parts[1])
        cap = parts[3] if len(parts) > 3 and parts[3] not in {"", "[N/A]"} else None
        gpus.append(Gpu(parts[0], round(mib / 1024, 1) if mib else None, cap))
        driver = parts[2]
    return gpus, driver


def _memory_gb() -> float | None:
    if platform.system() == "Darwin":
        out = _run(["sysctl", "-n", "hw.memsize"])
        return round(int(out) / 1024**3, 1) if out and out.isdigit() else None
    try:
        pages, size = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None
    return round(pages * size / 1024**3, 1)


def _cpu() -> str:
    if platform.system() == "Darwin":
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.machine()
    return platform.processor() or platform.machine()


def probe() -> Hardware:
    gpus, driver = nvidia_smi()
    hw = Hardware(
        os=f"{platform.system()} {platform.release()}",
        cpu=_cpu(),
        gpus=gpus,
        driver=driver,
        apple_gpu=platform.system() == "Darwin" and platform.machine() == "arm64",
        memory_gb=_memory_gb(),
    )
    try:
        import torch  # optional: only on machines that installed a GPU extra
    except ImportError:
        return hw
    hw.torch = torch.__version__
    hw.torch_cuda = torch.version.cuda
    hw.torch_cuda_ok = torch.cuda.is_available()
    mps = getattr(torch.backends, "mps", None)
    hw.torch_mps_ok = bool(mps and mps.is_available())
    if hw.torch_cuda_ok:
        hw.torch_arches = list(torch.cuda.get_arch_list())
        if not hw.gpus:  # nvidia-smi missing from PATH, but torch sees the GPU
            for i in range(torch.cuda.device_count()):
                p = torch.cuda.get_device_properties(i)
                hw.gpus.append(
                    Gpu(p.name, round(p.total_memory / 1024**3, 1), f"{p.major}.{p.minor}")
                )
    return hw


def tier(hw: Hardware) -> tuple[int, str]:
    """The compute tier, with the reason. Biggest NVIDIA card wins."""
    vram = max((g.vram_gb or 0 for g in hw.gpus), default=0)
    if vram >= TIER2_MIN_VRAM_GB:
        return 2, f"NVIDIA GPU with {vram:g} GB: full models, benchmarks (state the hardware)"
    if vram > 0:
        return 1, f"NVIDIA GPU with {vram:g} GB: small or quantized models, one at a time"
    if hw.apple_gpu and (hw.memory_gb or 0) >= 16:
        return 1, (
            f"Apple GPU (Metal) with {hw.memory_gb:g} GB unified memory: models with an MPS or "
            "ONNX path; CUDA-only libraries won't run"
        )
    return 0, "no usable GPU: stub backend, cached results, CPU-sized models"


def warnings(hw: Hardware) -> list[str]:
    out = []
    blackwell = [g for g in hw.gpus if g.blackwell]
    major = _float(hw.driver.split(".")[0]) if hw.driver else None
    if hw.gpus and major is not None and major < BLACKWELL_MIN_DRIVER:
        out.append(
            f"driver {hw.driver} is too old for CUDA 12.8 (the cu128 extra): update the NVIDIA "
            f"driver to {BLACKWELL_MIN_DRIVER}+"
        )
    if hw.torch and hw.gpus and not hw.torch_cuda_ok:
        out.append(
            f"PyTorch {hw.torch} can't use the NVIDIA GPU (CPU-only build?): "
            "run `uv sync --extra cu128`"
        )
    if blackwell and hw.torch_cuda_ok and hw.torch_arches and "sm_120" not in hw.torch_arches:
        out.append(
            f"PyTorch {hw.torch} (CUDA {hw.torch_cuda}) has no sm_120 kernels for "
            f"{blackwell[0].name}: install the cu128 build (PyTorch 2.7+)"
        )
    return out


def report(hw: Hardware) -> str:
    t, why = tier(hw)
    lines = [f"OS        {hw.os}", f"CPU       {hw.cpu}"]
    if hw.memory_gb:
        lines.append(f"Memory    {hw.memory_gb:g} GB" + (" (unified)" if hw.apple_gpu else ""))
    for g in hw.gpus:
        cap = f", sm_{g.compute_capability.replace('.', '')}" if g.compute_capability else ""
        vram = f"{g.vram_gb:g} GB" if g.vram_gb else "? GB"
        bw = ", Blackwell" if g.blackwell else ""
        lines.append(f"GPU       {g.name}, {vram}{cap}{bw}")
    if not hw.gpus:
        lines.append("GPU       Apple GPU (Metal)" if hw.apple_gpu else "GPU       no NVIDIA GPU")
    if hw.driver:
        lines.append(f"Driver    {hw.driver}")
    if hw.torch:
        accel = "CUDA" if hw.torch_cuda_ok else "MPS" if hw.torch_mps_ok else "CPU only"
        cuda = f", built for CUDA {hw.torch_cuda}" if hw.torch_cuda else ""
        lines.append(f"PyTorch   {hw.torch}{cuda} -> {accel}")
    else:
        lines.append("PyTorch   not installed (fine for tier 0; see README for the GPU extras)")
    lines.append(f"Tier      {t}: {why}")
    lines += [f"WARNING   {w}" for w in warnings(hw)]
    return "\n".join(lines)
