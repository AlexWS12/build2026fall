"""LLM providers for the planner: one adapter per API behind one small interface.

The SDKs are an optional dependency group (``uv sync --group llm``), imported only when an
adapter is built, so the rest of the backend and its tests never need them. Keys come from the
environment (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, ``GEMINI_API_KEY``,
``OPENROUTER_API_KEY``). Every provider failure (network, rate limit, refused request) becomes
``LlmError``, so the planner asks the user to retry; anything else is a bug and raises. A rate
limit (HTTP 429) is the ``RateLimited`` subclass, so the benchmark can stop and resume later
instead of scoring a free tier's daily cap as the model failing.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Protocol

from vibe.agent.planner import LlmError

TIMEOUT_SECONDS = 120.0
MAX_RETRIES = 0
"""No silent SDK retries: a backoff would hide inside the measured latency. A transient
failure shows up as ``LlmError`` instead (the benchmark scores it ``provider_error``)."""
MAX_OUTPUT_TOKENS = 4096
"""A plan is a few hundred tokens; the rest is room for models that think before answering."""


class RateLimited(LlmError):
    """The provider said 429: too many requests for now, or a free tier's daily cap."""


def _status(exc: BaseException) -> int | None:
    """The HTTP status of an SDK or httpx error, if it has one."""
    for value in (
        getattr(exc, "status_code", None),  # anthropic, openai
        getattr(exc, "code", None),  # google-genai
        getattr(getattr(exc, "response", None), "status_code", None),  # httpx
    ):
        if isinstance(value, int):
            return value
    return None


def provider_error(name: str, exc: BaseException) -> LlmError:
    """``exc`` from provider ``name`` as the planner's error type."""
    cls = RateLimited if _status(exc) == 429 else LlmError
    return cls(f"{name}: {exc}")


@dataclass(frozen=True)
class Usage:
    """Tokens for one call, split the way providers bill them."""

    input: int = 0
    """Input tokens at the full price (not served from or written to a cache)."""
    output: int = 0
    """Output tokens, reasoning included."""
    cache_read: int = 0
    cache_write: int = 0
    reasoning: int = 0
    """The part of ``output`` spent on reasoning, where the provider reports it."""


@dataclass(frozen=True)
class Reply:
    text: str
    usage: Usage
    seconds: float
    """Wall time of the call, network included."""
    stop_reason: str | None = None
    """The provider's reason, except a safety refusal is always ``"refusal"``."""
    meta: Mapping[str, Any] = field(default_factory=dict)


class Provider(Protocol):
    name: str
    """``<provider>:<model>``, e.g. ``anthropic:claude-haiku-5-5``."""
    model: str
    settings: Mapping[str, Any]
    """Everything that changes the model's behavior, recorded with each benchmark result."""

    def complete(self, system: str, user: str, schema: Mapping[str, Any]) -> Reply:
        """One call whose text output must follow ``schema``."""
        ...

    def describe(self) -> dict[str, Any]:
        """What the provider reports about the model (it exists, its capabilities)."""
        ...


class AnthropicProvider:
    """Claude through ``output_config.format``.

    No server-side fallbacks: a refused request would be re-run on another model, hiding the
    refusal and mixing that model's cost and latency into this one's. No ``temperature``
    either: Claude 5.x models reject it.
    """

    def __init__(
        self,
        model: str,
        *,
        effort: str | None = None,
        thinking: Mapping[str, Any] | None = None,
        max_tokens: int = MAX_OUTPUT_TOKENS,
    ) -> None:
        import anthropic

        self._errors = anthropic.APIError
        self.client = anthropic.Anthropic(timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES)
        self.model = model
        self.name = f"anthropic:{model}"
        self.effort = effort
        self.thinking = dict(thinking) if thinking else None
        self.max_tokens = max_tokens
        self.settings = {"effort": effort, "thinking": self.thinking, "max_tokens": max_tokens}

    def complete(self, system: str, user: str, schema: Mapping[str, Any]) -> Reply:
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if self.effort:
            output_config["effort"] = self.effort
        extra = {"thinking": self.thinking} if self.thinking else {}
        start = perf_counter()
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
                **extra,
            )
        except self._errors as exc:
            raise provider_error(self.name, exc) from exc
        seconds = perf_counter() - start
        u = message.usage
        details = u.output_tokens_details
        return Reply(
            text="".join(b.text for b in message.content if b.type == "text"),
            usage=Usage(
                input=u.input_tokens,
                output=u.output_tokens,
                cache_read=u.cache_read_input_tokens or 0,
                cache_write=u.cache_creation_input_tokens or 0,
                reasoning=(details.thinking_tokens or 0) if details else 0,
            ),
            seconds=seconds,
            stop_reason=message.stop_reason,
            meta={"id": message.id, "model": message.model},
        )

    def describe(self) -> dict[str, Any]:
        info = self.client.models.retrieve(self.model)
        capabilities = getattr(info, "capabilities", None)
        return {
            "id": info.id,
            "capabilities": capabilities.model_dump() if capabilities is not None else None,
        }


class OpenAIProvider:
    """OpenAI's Chat Completions with strict Structured Outputs. With ``base_url`` it also
    serves OpenAI-compatible APIs such as OpenRouter (quality runs only: the extra hop makes
    its latency not comparable)."""

    def __init__(
        self,
        model: str,
        *,
        reasoning_effort: str | None = None,
        base_url: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        label: str = "openai",
        max_tokens: int = MAX_OUTPUT_TOKENS,
    ) -> None:
        import openai

        self._errors = openai.APIError
        self.client = openai.OpenAI(
            # Not .get(): given no key, the SDK sends OPENAI_API_KEY to whatever base_url is set.
            api_key=os.environ[api_key_env],
            base_url=base_url,
            timeout=TIMEOUT_SECONDS,
            max_retries=MAX_RETRIES,
        )
        self.model = model
        self.name = f"{label}:{model}"
        self.reasoning_effort = reasoning_effort
        self.max_tokens = max_tokens
        self.settings = {
            "reasoning_effort": reasoning_effort,
            "base_url": base_url,
            "max_tokens": max_tokens,
        }

    def complete(self, system: str, user: str, schema: Mapping[str, Any]) -> Reply:
        extra = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}
        start = perf_counter()
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "plan", "strict": True, "schema": schema},
                },
                max_completion_tokens=self.max_tokens,
                **extra,
            )
        except self._errors as exc:
            raise provider_error(self.name, exc) from exc
        seconds = perf_counter() - start
        choice = response.choices[0]
        u = response.usage
        cached = (
            (u.prompt_tokens_details.cached_tokens or 0) if u and u.prompt_tokens_details else 0
        )
        reasoning = (
            (u.completion_tokens_details.reasoning_tokens or 0)
            if u and u.completion_tokens_details
            else 0
        )
        refused = bool(choice.message.refusal) or choice.finish_reason == "content_filter"
        return Reply(
            text=choice.message.content or "",
            usage=Usage(
                input=(u.prompt_tokens - cached) if u else 0,
                output=u.completion_tokens if u else 0,
                cache_read=cached,
                reasoning=reasoning,
            ),
            seconds=seconds,
            stop_reason="refusal" if refused else choice.finish_reason,
            meta={"id": response.id, "model": response.model},
        )

    def describe(self) -> dict[str, Any]:
        try:
            info = self.client.models.retrieve(self.model)
            return {"id": info.id, "owned_by": info.owned_by}
        except self._errors:  # OpenRouter has no per-model endpoint: look it up in the list
            ids = [m.id for m in self.client.models.list()]
            if self.model not in ids:
                raise
            return {"id": self.model}


class GeminiProvider:
    """Gemini through ``response_json_schema``. On the free tier, Google may use what's sent to
    improve its products. Only text goes (the request and the tool list), which D-017 allows."""

    _REFUSALS = frozenset({"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"})

    def __init__(
        self,
        model: str,
        *,
        thinking_level: str | None = None,
        thinking_budget: int | None = None,
        max_tokens: int = MAX_OUTPUT_TOKENS,
    ) -> None:
        import httpx
        from google import genai
        from google.genai import errors, types

        self._types = types
        # The SDK lets httpx's errors through on network failures and timeouts.
        self._errors = (errors.APIError, httpx.HTTPError)
        self.client = genai.Client(
            api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"),
            http_options=types.HttpOptions(timeout=int(TIMEOUT_SECONDS * 1000)),
        )
        self.model = model
        self.name = f"gemini:{model}"
        self.thinking_level = thinking_level
        self.thinking_budget = thinking_budget
        self.max_tokens = max_tokens
        self.settings = {
            "thinking_level": thinking_level,
            "thinking_budget": thinking_budget,
            "max_tokens": max_tokens,
        }

    def complete(self, system: str, user: str, schema: Mapping[str, Any]) -> Reply:
        types = self._types
        thinking = None
        if self.thinking_level is not None or self.thinking_budget is not None:
            thinking = types.ThinkingConfig(
                thinking_level=self.thinking_level, thinking_budget=self.thinking_budget
            )
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=dict(schema),
            max_output_tokens=self.max_tokens,
            thinking_config=thinking,
        )
        start = perf_counter()
        try:
            response = self.client.models.generate_content(
                model=self.model, contents=user, config=config
            )
        except self._errors as exc:
            raise provider_error(self.name, exc) from exc
        seconds = perf_counter() - start
        u = response.usage_metadata
        cached = (u.cached_content_token_count or 0) if u else 0
        thoughts = (u.thoughts_token_count or 0) if u else 0
        candidate = response.candidates[0] if response.candidates else None
        finish = candidate.finish_reason.name if candidate and candidate.finish_reason else None
        blocked = response.prompt_feedback is not None and response.prompt_feedback.block_reason
        return Reply(
            text=response.text or "",
            usage=Usage(
                input=((u.prompt_token_count or 0) - cached) if u else 0,
                output=((u.candidates_token_count or 0) + thoughts) if u else 0,
                cache_read=cached,
                reasoning=thoughts,
            ),
            seconds=seconds,
            stop_reason="refusal" if blocked or finish in self._REFUSALS else finish,
            meta={"model": response.model_version},
        )

    def describe(self) -> dict[str, Any]:
        info = self.client.models.get(model=self.model)
        return {"id": info.name, "thinking": getattr(info, "thinking", None)}


class OllamaProvider:
    """An open-weight model on this machine through Ollama's native API (``format`` = schema).

    Free per call, so ``meta`` records what the local option costs instead: decode speed, load
    time and the VRAM the model holds.
    """

    def __init__(
        self,
        model: str,
        *,
        host: str | None = None,
        think: bool = False,
        num_ctx: int = 8192,
    ) -> None:
        import httpx

        self._errors = httpx.HTTPError
        self.host = host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.http = httpx.Client(base_url=self.host, timeout=TIMEOUT_SECONDS * 3)
        self.model = model
        self.name = f"ollama:{model}"
        self.think = think
        self.num_ctx = num_ctx
        self.settings = {"think": think, "num_ctx": num_ctx}

    def complete(self, system: str, user: str, schema: Mapping[str, Any]) -> Reply:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": schema,
            "stream": False,
            "think": self.think,
            "options": {"num_ctx": self.num_ctx},
        }
        start = perf_counter()
        try:
            response = self.http.post("/api/chat", json=body)
            response.raise_for_status()
        except self._errors as exc:
            raise provider_error(self.name, exc) from exc
        seconds = perf_counter() - start
        data = response.json()
        eval_count = data.get("eval_count", 0)
        eval_seconds = data.get("eval_duration", 0) / 1e9
        return Reply(
            text=data.get("message", {}).get("content", ""),
            usage=Usage(input=data.get("prompt_eval_count", 0), output=eval_count),
            seconds=seconds,
            stop_reason=data.get("done_reason"),
            meta={
                "tokens_per_second": eval_count / eval_seconds if eval_seconds else None,
                "load_seconds": data.get("load_duration", 0) / 1e9,
                **self._vram(),
            },
        )

    def _vram(self) -> dict[str, Any]:
        try:
            running = self.http.get("/api/ps").json().get("models", [])
        except self._errors:
            return {}
        for m in running:
            if m.get("name") == self.model or m.get("model") == self.model:
                return {"vram_bytes": m.get("size_vram"), "model_bytes": m.get("size")}
        return {}

    def describe(self) -> dict[str, Any]:
        try:
            response = self.http.post("/api/show", json={"model": self.model})
            response.raise_for_status()
        except self._errors as exc:
            raise provider_error(self.name, exc) from exc
        return {"id": self.model, "details": response.json().get("details")}
