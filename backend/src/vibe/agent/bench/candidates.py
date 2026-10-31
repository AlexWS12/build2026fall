"""The R-1 candidates: provider, model ID, settings and price.

Every candidate runs at the lowest reasoning setting it allows, since a plan is short and
reasoning adds latency and cost. Sampling stays at each provider's default (Claude 5.x rejects
``temperature``), so all candidates are treated the same way.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from vibe.agent.providers import (
    AnthropicProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAIProvider,
    Provider,
    Usage,
)

PRICES_AS_OF = "2026-10-31"
"""When the prices below were read from these pages:
- https://platform.claude.com/docs/en/about-claude/pricing
- https://developers.openai.com/api/docs/models/gpt-5.6-terra
- https://ai.google.dev/gemini-api/docs/pricing (Gemini 3.8 Flash doubles on 2027-01-01)
"""


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
    price: Price | None
    """None for a model on our own hardware (no per-call cost)."""
    note: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


_CANDIDATES = (
    Candidate(
        "opus-5-5",
        lambda: AnthropicProvider("claude-opus-5-5", effort="low"),
        Price(4.00, 20.00, cache_read=0.20, cache_write=5.00),
        "ceiling: thinking can't be turned off, so the lowest effort",
        ("ceiling",),
    ),
    Candidate(
        "sonnet-5-5",
        lambda: AnthropicProvider(
            "claude-sonnet-5-5", effort="low", thinking={"type": "between_tools"}
        ),
        Price(2.00, 10.00, cache_read=0.10, cache_write=2.50),
        "no up-front thinking (between_tools), lowest effort",
    ),
    Candidate(
        "haiku-5-5",
        lambda: AnthropicProvider("claude-haiku-5-5", effort="low", thinking={"type": "disabled"}),
        Price(0.10, 0.50, cache_read=0.01, cache_write=0.125),
        "thinking off, lowest effort; prices for prompts up to 100k tokens",
    ),
    Candidate(
        "gpt-5.6-terra",
        lambda: OpenAIProvider("gpt-5.6-terra", reasoning_effort="none"),
        Price(2.00, 12.00, cache_read=0.20),
        "reasoning effort none",
    ),
    Candidate(
        "gemini-3.8-flash",
        lambda: GeminiProvider("gemini-3.8-flash", thinking_level="minimal"),
        Price(0.75, 3.75, cache_read=0.075),
        "lowest thinking level; promotional price until 2026-12-31, then double",
    ),
    Candidate(
        "qwen3.5-9b",
        lambda: OllamaProvider("qwen3.5:9b"),
        None,
        "local, Ollama default 4-bit quantization",
        ("local",),
    ),
    Candidate(
        "gemma4-12b",
        lambda: OllamaProvider("gemma4:12b"),
        None,
        "local, Ollama default 4-bit quantization",
        ("local",),
    ),
    Candidate(
        "openrouter-qwen3.5-9b",
        lambda: OpenAIProvider(
            "qwen/qwen3.5-9b",
            base_url="https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
            label="openrouter",
        ),
        None,
        "quality only: full-precision weights and a proxy hop, so no latency or cost claims",
        ("quality-only",),
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
