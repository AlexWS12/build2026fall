"""Claude as the planner's LLM (R-1b; D-023, Proposed): plans requests the templates can't read.

``plan_request`` tries the template parser first, so known commands stay instant and free; only a
request no template matches comes here. Claude gets the request text and the toolbox's tool
descriptions, never media (D-017: text may go to a cloud LLM), and answers with a plan in the same
JSON shape the templates write, or with a question when no tool can do what was asked. The
planner then validates the plan against the registry and retries once with the error.

Opt-in: ``VIBE_PLANNER_LLM=claude`` (``config.planner_llm``), the ``llm`` extra
(``uv sync --extra llm``) and Anthropic credentials (``ANTHROPIC_API_KEY`` in ``.env``, or an
``ant auth login`` profile). Without them the app plans with templates only, as before.
"""

import json
import logging
from collections.abc import Mapping
from typing import Any

from vibe.agent.planner import LlmError
from vibe.toolbox.spec import ToolSpec

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "low"
"""Planning is a short structured answer, asked live in a demo: low effort keeps it quick."""
MAX_TOKENS = 16000

_RULES = """\
You plan video edits for a chat-driven video editor. The user writes a request; you answer with \
a plan: an ordered list of tool calls from the catalogue below, or a question.

How a plan works:
- Each call has a unique step_id ("s1", "s2", ...), a tool name, and its arguments.
- An argument may be {"$ref": "<step_id>"}: the result of an earlier step. Refs are whole \
argument values only.
- Masks must target tracks found by find_objects or find_by_description: add_mask's "targets" is \
always a $ref to one of those steps, never literal ids.
- Times: use resolve_time with a phrase ("first half", "last 10 seconds", "after 0:30") and \
$ref its result, or a literal {"start": seconds, "end": seconds}. find_speech finds when words \
are said.
- When hiding found faces, people or license plates, set "while_tracked": true so each mask \
follows its target. For "everyone except <description>", leave it false.
- undo and redo are always a plan's only call. A plan has at most one keep_range.
- Detector labels are "face", "person", "license_plate", and other COCO names.

Answer with a question instead of a plan when:
- the request needs something no tool can do (for example a position like "the person in the \
middle", or an effect that isn't listed): say what you can do instead;
- it's ambiguous in a way that changes the edit ("mute it", "cut that part"): ask which.
Never guess. A wrong edit is worse than a question.

Output: JSON with "calls" (each call's arguments as a JSON object encoded in the string \
"args_json") and "question" ("" when you give calls; calls empty when you ask)."""


def tool_catalogue(tools: Mapping[str, ToolSpec]) -> str:
    """Every tool the plan may call, from the registry itself, so it can't drift from the code."""
    parts = []
    for name in sorted(tools):
        spec = tools[name]
        entry: dict[str, Any] = {
            "tool": name,
            "kind": spec.kind,
            "description": spec.description,
            "args_schema": spec.args.model_json_schema(),
            "returns": spec.returns,
        }
        if spec.ref_args:
            entry["args_that_may_be_a_ref"] = dict(spec.ref_args)
        if spec.ref_required:
            entry["args_that_must_be_a_ref"] = sorted(spec.ref_required)
        parts.append(json.dumps(entry, sort_keys=True))
    return "Tool catalogue (one JSON object per tool):\n" + "\n".join(parts)


def output_schema(tools: Mapping[str, ToolSpec]) -> dict[str, Any]:
    call = {
        "type": "object",
        "properties": {
            "step_id": {"type": "string"},
            "tool": {"type": "string", "enum": sorted(tools)},
            "args_json": {"type": "string"},
        },
        "required": ["step_id", "tool", "args_json"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "calls": {"type": "array", "items": call},
            "question": {"type": "string"},
        },
        "required": ["calls", "question"],
        "additionalProperties": False,
    }


def to_plan(answer: Mapping[str, Any]) -> dict[str, Any]:
    """Claude's answer in the planner's shape: ``{"calls": [...]}`` or ``{"question": "..."}``.

    Arguments that aren't a JSON object are passed on as they are, so the plan fails validation
    and the planner retries with the error instead of this guessing.
    """
    question = str(answer.get("question") or "").strip()
    calls = answer.get("calls") or []
    if question and not calls:
        return {"question": question}
    out = []
    for call in calls:
        try:
            args: Any = json.loads(call.get("args_json") or "{}")
        except json.JSONDecodeError:
            args = call.get("args_json")
        out.append({"step_id": call.get("step_id"), "tool": call.get("tool"), "args": args})
    return {"calls": out}


class ClaudePlanner:
    """An ``LlmPlanner`` backed by the Claude API. ``client`` is injectable for tests."""

    def __init__(
        self, client: Any = None, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT
    ) -> None:
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise LlmError("the llm extra isn't installed: uv sync --extra llm") from e
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort

    def plan(
        self, request: str, tools: Mapping[str, ToolSpec], error: str | None
    ) -> Mapping[str, Any]:
        content = f"Request: {request}"
        if error:
            content += f"\n\nYour previous plan was rejected: {error}\nFix it, or ask a question."
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                # Stable first (rules, then the catalogue), so the prefix caches across requests.
                system=[
                    {"type": "text", "text": _RULES},
                    {
                        "type": "text",
                        "text": tool_catalogue(tools),
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
                messages=[{"role": "user", "content": content}],
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": output_schema(tools)},
                },
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except Exception as e:  # noqa: BLE001 - any client failure: ask the user to retry
            if _is_api_error(e):
                logger.warning("Claude planner call failed: %s", e)
                raise LlmError(str(e)) from e
            raise
        if response.stop_reason == "refusal":
            raise LlmError("the request was declined")
        if response.stop_reason == "max_tokens":
            raise LlmError("the plan was cut off")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LlmError("no plan in the answer")
        try:
            return to_plan(json.loads(text))
        except (json.JSONDecodeError, AttributeError, TypeError) as e:
            raise LlmError(f"the answer wasn't a plan: {e}") from e


def _is_api_error(error: Exception) -> bool:
    try:
        import anthropic
    except ImportError:
        return False
    return isinstance(error, anthropic.APIError)
