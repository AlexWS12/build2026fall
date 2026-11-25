"""``config.transcribe_backend``: VIBE_TRANSCRIBE_BACKEND, else VIBE_BACKEND."""

import pytest

from vibe import config


@pytest.mark.parametrize(
    ("backend", "override", "expected"),
    [
        ("remote", None, "remote"),
        ("remote", "", "remote"),
        ("remote", "local", "local"),
        ("stub", "local", "local"),
        ("local", "stub", "stub"),
    ],
)
def test_transcribe_backend(
    monkeypatch: pytest.MonkeyPatch, backend: str, override: str | None, expected: str
) -> None:
    monkeypatch.setenv("VIBE_BACKEND", backend)
    if override is None:
        monkeypatch.delenv("VIBE_TRANSCRIBE_BACKEND", raising=False)
    else:
        monkeypatch.setenv("VIBE_TRANSCRIBE_BACKEND", override)
    assert config.transcribe_backend() == expected


def test_a_bad_transcribe_backend_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBE_TRANSCRIBE_BACKEND", "cloud")
    with pytest.raises(ValueError, match="VIBE_TRANSCRIBE_BACKEND"):
        config.transcribe_backend()
