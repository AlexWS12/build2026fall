"""F-08: ``doctor`` (tiers, warnings) and ``download`` (hash checks). No GPU, no network."""

import hashlib
import json
from pathlib import Path

import pytest

from vibe.modules import ModuleError, doctor
from vibe.modules.doctor import Gpu, Hardware
from vibe.modules.download import download, manifest


def _hw(**kw) -> Hardware:
    return Hardware(os="test", cpu="test", **kw)


@pytest.mark.parametrize(
    ("hw", "tier"),
    [
        (_hw(), 0),  # integrated graphics
        (_hw(apple_gpu=True, memory_gb=8), 0),  # Apple M2, 8 GB
        (_hw(apple_gpu=True, memory_gb=64), 1),  # Apple M5 Pro, 64 GB
        (_hw(gpus=[Gpu("NVIDIA GeForce RTX 3050 Laptop GPU", 4.0, "8.6")]), 1),
        (_hw(gpus=[Gpu("NVIDIA GeForce RTX 5060 Ti", 8.0, "12.0")]), 1),
        (_hw(gpus=[Gpu("NVIDIA GeForce RTX 5070", 12.0, "12.0")]), 2),
        (_hw(gpus=[Gpu("NVIDIA RTX PRO 2000 Blackwell", 16.0, "12.0")]), 2),
    ],
)
def test_tiers_match_the_survey_machines(hw: Hardware, tier: int) -> None:
    assert doctor.tier(hw)[0] == tier


def test_old_driver_is_flagged() -> None:
    hw = _hw(gpus=[Gpu("NVIDIA GeForce RTX 4050 Laptop GPU", 6.0, "8.9")], driver="555.97")
    assert any("570+" in w for w in doctor.warnings(hw))
    hw.driver = "592.10"
    assert doctor.warnings(hw) == []


def test_blackwell_without_sm120_kernels_is_flagged() -> None:
    hw = _hw(
        gpus=[Gpu("NVIDIA GeForce RTX 5070", 12.0, "12.0")],
        driver="575.0",
        torch="2.6.0+cu124",
        torch_cuda="12.4",
        torch_cuda_ok=True,
        torch_arches=["sm_80", "sm_86", "sm_90"],
    )
    assert any("sm_120" in w for w in doctor.warnings(hw))
    assert doctor.Gpu("NVIDIA GeForce RTX 5070", 12.0).blackwell  # from the name alone


def test_cpu_only_torch_on_a_gpu_machine_is_flagged() -> None:
    hw = _hw(gpus=[Gpu("NVIDIA GeForce RTX 4060 Ti", 8.0, "8.9")], driver="610.60", torch="2.9.0")
    hw.torch_cuda_ok = False
    assert any("--extra cu128" in w for w in doctor.warnings(hw))


def test_doctor_runs_on_this_machine() -> None:
    hw = doctor.probe()
    text = doctor.report(hw)
    assert "Tier" in text
    assert json.dumps(hw.as_dict())  # --json works


def test_nvidia_smi_parsing(monkeypatch) -> None:
    monkeypatch.setattr(doctor, "_run", lambda cmd: "NVIDIA GeForce RTX 4060 Ti, 8188, 610.60, 8.9")
    gpus, driver = doctor.nvidia_smi()
    assert driver == "610.60"
    assert gpus == [Gpu("NVIDIA GeForce RTX 4060 Ti", 8.0, "8.9")]


# --- download -----------------------------------------------------------------------------


def _entry(tmp_path: Path, content: bytes, sha256: str | None):
    src = tmp_path / "weights.onnx"
    src.write_bytes(content)
    text = json.dumps(
        {
            "models": {
                "toy": {
                    "source": "url",
                    "url": src.as_uri(),
                    "file": "toy.onnx",
                    "sha256": sha256,
                    "license": "MIT",
                    "for": "tests",
                    "min_tier": 0,
                }
            }
        }
    )
    return manifest(text)["toy"]


def test_download_checks_the_hash(tmp_path: Path) -> None:
    content = b"weights"
    good = hashlib.sha256(content).hexdigest()
    path, digest = download(_entry(tmp_path, content, good), tmp_path / "models")
    assert path.read_bytes() == content and digest == good

    with pytest.raises(ModuleError, match="expected"):
        download(_entry(tmp_path, content, "0" * 64), tmp_path / "other")
    assert not (tmp_path / "other" / "toy.onnx").exists()
    assert list((tmp_path / "other").iterdir()) == []  # no .part left behind


def test_download_refuses_unpinned_unless_asked(tmp_path: Path) -> None:
    entry = _entry(tmp_path, b"w", None)
    with pytest.raises(ModuleError, match="--pin"):
        download(entry, tmp_path / "models")
    _, digest = download(entry, tmp_path / "models", pin=True)
    assert digest == hashlib.sha256(b"w").hexdigest()


def test_gated_and_package_models_explain_themselves(tmp_path: Path) -> None:
    models = manifest()
    with pytest.raises(ModuleError, match="by hand"):
        download(models["egoblur-face"], tmp_path)
    with pytest.raises(ModuleError, match="uv pip install rfdetr"):
        download(models["rfdetr"], tmp_path)


def test_manifest_is_permissive_only() -> None:
    for entry in manifest().values():
        assert "AGPL" not in entry.license and "non-commercial" not in entry.license.lower()
