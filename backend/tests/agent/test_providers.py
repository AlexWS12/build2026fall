"""Provider adapters, offline: fake keys and a local port nothing listens on. Each test skips
without its SDK (the optional ``llm`` group), so CI skips most of them; run them with
``uv run --group llm pytest tests/agent/test_providers.py``."""

import socket
from collections.abc import Callable
from typing import Any

import pytest

from vibe.agent.planner import LlmError
from vibe.agent.providers import (
    AnthropicProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAIProvider,
    Provider,
    RateLimited,
    provider_error,
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


@pytest.fixture(autouse=True)
def fake_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setenv(key, "fake")


@pytest.fixture
def dead_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


def anthropic_at(url: str) -> Provider:
    pytest.importorskip("anthropic")
    provider = AnthropicProvider("model")
    provider.client = provider.client.with_options(base_url=url, timeout=1)
    return provider


def openai_at(url: str) -> Provider:
    pytest.importorskip("openai")
    provider = OpenAIProvider("model", base_url=url)
    provider.client = provider.client.with_options(timeout=1)
    return provider


def gemini_at(url: str) -> Provider:
    genai = pytest.importorskip("google.genai")
    provider = GeminiProvider("model")
    options = provider._types.HttpOptions(base_url=url, timeout=1000)
    provider.client = genai.Client(api_key="fake", http_options=options)
    return provider


def ollama_at(url: str) -> Provider:
    pytest.importorskip("httpx")
    provider = OllamaProvider("model", host=url)
    provider.http.timeout = 1
    return provider


@pytest.mark.parametrize("build", [anthropic_at, openai_at, gemini_at, ollama_at])
def test_a_network_failure_is_an_llm_error(build: Callable[[str], Provider], dead_url: str) -> None:
    with pytest.raises(LlmError):
        build(dead_url).complete("system", "user", SCHEMA)


def test_a_missing_key_is_never_replaced_by_the_openai_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("openai")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(KeyError):
        OpenAIProvider("model", base_url="https://example.test", api_key_env="OPENROUTER_API_KEY")


class _Status(Exception):
    def __init__(self, **attrs: Any) -> None:
        super().__init__("boom")
        for name, value in attrs.items():
            setattr(self, name, value)


class _Response:
    status_code = 429


@pytest.mark.parametrize(
    "exc",
    [_Status(status_code=429), _Status(code=429), _Status(response=_Response())],
    ids=["anthropic-openai", "google-genai", "httpx"],
)
def test_a_429_is_rate_limited(exc: Exception) -> None:
    """The benchmark stops on a rate limit instead of scoring a free tier's cap as a failure."""
    error = provider_error("p:m", exc)

    assert isinstance(error, RateLimited)
    assert isinstance(error, LlmError)  # the planner still asks the user to retry


@pytest.mark.parametrize("exc", [_Status(status_code=500), _Status(code="429"), ValueError("x")])
def test_any_other_failure_is_a_plain_llm_error(exc: Exception) -> None:
    assert type(provider_error("p:m", exc)) is LlmError
