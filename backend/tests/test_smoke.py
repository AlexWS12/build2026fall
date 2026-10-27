"""Smoke tests: the package imports, the API starts, and the default backend is stub (no GPU)."""

import importlib

import pytest
from fastapi.testclient import TestClient

from vibe import config
from vibe.api.app import app

LAYERS = ["agent", "api", "contract", "modules", "render", "storage", "toolbox", "video"]


@pytest.mark.parametrize("layer", LAYERS)
def test_layer_imports(layer: str) -> None:
    importlib.import_module(f"vibe.{layer}")


def test_health_defaults_to_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIBE_BACKEND", raising=False)
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "backend": "stub"}


def test_rejects_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBE_BACKEND", "gpu")
    with pytest.raises(ValueError, match="VIBE_BACKEND"):
        config.model_backend()
