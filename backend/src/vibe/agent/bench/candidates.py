"""The R-1 candidates: provider, model ID, settings, and what each costs to run.

R-1b has no budget, so only ``local`` (our own hardware) and ``free`` (a hosted free tier)
candidates are run and decided between. ``paid`` ones stay listed so a later, funded run is a
flag away (``run --allow-paid``), but nothing runs them by accident.

Every candidate runs at the lowest reasoning setting it allows, since a plan is short and
reasoning adds latency. Sampling stays at each provider's default, so all are treated alike.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from vibe.agent.providers import (
    AnthropicProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAIProvider,
    Provider,
    Usage,
)

FREE_TIERS_AS_OF = "2026-11-12"
"""When the free tiers below were read from these pages:
- Groq, https://console.groq.com/docs/rate-limits and /docs/structured-outputs: the free plan has
  ``openai/gpt-oss-120b``, ``openai/gpt-oss-20b`` and ``qwen/qwen3.8-27b``, each 30 RPM, 1K RPD,
  8K TPM and 200K TPD, all three with strict structured outputs. /docs/reasoning: reasoning
  effort ``none`` exists only for Qwen 3.8; GPT-OSS's lowest is ``low``.
- Gemini, https://aistudio.google.com/docs/pricing: ``gemini-3.8-flash``, ``gemini-3.6-flash``,
  ``gemini-3.5-flash-lite`` and ``gemini-3.1-flash-lite`` are free; free-tier content is used to
  improve Google's products. Per-model limits are only shown per project in AI Studio.
- OpenRouter, https://openrouter.ai/api/v1/models: ``:free`` models that list
  ``structured_outputs`` (Nemotron 3.5 Super VL) or only ``response_format`` (Gemma 4 31B).
  The daily cap is per account: ``GET /api/v1/key`` reports ``free_model_daily_requests``.
- Ollama, https://ollama.com/library/qwen3.5/tags and /gemma4/tags: download sizes of the
  default quantizations are 3.3 GB (``qwen3.5:4b``), 6.6 GB (``qwen3.5:9b``, ``gemma4:e4b``)
  and 7.7 GB (``gemma4:12b``, tight on an 8 GB card).
"""

PRICES_AS_OF = "2026-10-31"
"""When the paid prices below were read (paid candidates only):
- https://platform.claude.com/docs/en/about-claude/pricing
- https://developers.openai.com/api/docs/models/gpt-5.6-terra
"""

GROQ_URL = "https://api.groq.com/openai/v1"
OPENROUTER_URL = "https://openrouter.ai/api/v1"

Access = Literal["local", "free", "paid"]


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0

    def cost(self, usage: Usage) -> float:
        return (
            usage.input * self.input
            + usage.output * self.output
            + usage.cache_read * self.cache_read
            + usage.cache_write * self.cache_write
        ) / 1e6

    def uncached_cost(self, usage: Usage) -> float:
        """What the call would cost with no prompt caching at all."""
        prompt = usage.input + usage.cache_read + usage.cache_write
        return (prompt * self.input + usage.output * self.output) / 1e6


@dataclass(frozen=True)
class Candidate:
    name: str
    build: Callable[[], Provider]
    access: Access
    price: Price | None = None
    """Paid candidates only: free and local ones cost nothing per call."""
    rpm: float | None = None
    """Requests per minute ``run`` paces itself to by default (free tiers' published limit)."""
    tags: tuple[str, ...] = ()
    """``quality-only``: reported, but its latency doesn't count (an extra proxy hop)."""


def _groq(model: str, **settings: Any) -> Provider:
    return OpenAIProvider(
        model, base_url=GROQ_URL, api_key_env="GROQ_API_KEY", label="groq", **settings
    )


def _openrouter(model: str) -> Provider:
    return OpenAIProvider(
        model, base_url=OPENROUTER_URL, api_key_env="OPENROUTER_API_KEY", label="openrouter"
    )


_CANDIDATES = (
    # --- Free hosted tiers -----------------------------------------------------------------
    Candidate(
        "groq-gpt-oss-120b",
        lambda: _groq("openai/gpt-oss-120b", reasoning_effort="low"),
        "free",
        rpm=30,
    ),
    Candidate(
        "groq-gpt-oss-20b",
        lambda: _groq("openai/gpt-oss-20b", reasoning_effort="low"),
        "free",
        rpm=30,
    ),
    Candidate(
        "groq-qwen3.8-27b",
        lambda: _groq("qwen/qwen3.8-27b", reasoning_effort="none"),
        "free",
        rpm=30,
    ),
    Candidate(
        "gemini-3.8-flash",
        lambda: GeminiProvider("gemini-3.8-flash", thinking_level="minimal"),
        "free",
    ),
    Candidate(
        "gemini-3.5-flash-lite",
        lambda: GeminiProvider("gemini-3.5-flash-lite", thinking_level="minimal"),
        "free",
    ),
    Candidate(
        "openrouter-nemotron-3.5-super-vl",
        lambda: _openrouter("nvidia/nemotron-3.5-super-vl:free"),
        "free",
        tags=("quality-only",),
    ),
    Candidate(
        "openrouter-gemma-4-31b",
        lambda: _openrouter("google/gemma-4-31b-it:free"),
        "free",
        tags=("quality-only",),
    ),
    # --- Our own hardware (Ollama, default 4-bit quantization) -----------------------------
    Candidate("qwen3.5-4b", lambda: OllamaProvider("qwen3.5:4b"), "local"),
    Candidate("qwen3.5-9b", lambda: OllamaProvider("qwen3.5:9b"), "local"),
    Candidate("gemma4-e4b", lambda: OllamaProvider("gemma4:e4b"), "local"),
    Candidate("gemma4-12b", lambda: OllamaProvider("gemma4:12b"), "local"),
    # --- Paid: never run without --allow-paid ----------------------------------------------
    Candidate(
        "opus-5-5",
        lambda: AnthropicProvider("claude-opus-5-5", effort="low"),
        "paid",
        Price(4.00, 20.00, cache_read=0.20, cache_write=5.00),
    ),
    Candidate(
        "sonnet-5-5",
        lambda: AnthropicProvider(
            "claude-sonnet-5-5", effort="low", thinking={"type": "between_tools"}
        ),
        "paid",
        Price(2.00, 10.00, cache_read=0.10, cache_write=2.50),
    ),
    Candidate(
        "haiku-5-5",
        lambda: AnthropicProvider("claude-haiku-5-5", effort="low", thinking={"type": "disabled"}),
        "paid",
        Price(0.10, 0.50, cache_read=0.01, cache_write=0.125),
    ),
    Candidate(
        "gpt-5.6-terra",
        lambda: OpenAIProvider("gpt-5.6-terra", reasoning_effort="none"),
        "paid",
        Price(2.00, 12.00, cache_read=0.20),
    ),
)

CANDIDATES: Mapping[str, Candidate] = {c.name: c for c in _CANDIDATES}


def cost(candidate_name: str, attempts: list[Mapping[str, Any]], uncached: bool = False) -> float:
    candidate = CANDIDATES.get(candidate_name)
    if candidate is None or candidate.price is None:
        return 0.0
    price = candidate.price
    total = 0.0
    for attempt in attempts:
        usage = Usage(**attempt["usage"])
        total += price.uncached_cost(usage) if uncached else price.cost(usage)
    return total
