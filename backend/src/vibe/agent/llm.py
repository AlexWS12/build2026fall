"""The LLM side of the planner: the reply schema, the system prompt, and reading the reply.

One call writes the whole plan as JSON (D-006, D-016). Both the schema and the prompt are built
from the tool registry, so a new tool reaches the LLM without editing this file.

The schema keeps to the JSON Schema subset every provider's structured-output mode accepts
(Claude, OpenAI strict mode, Gemini, Ollama): no recursion, no numeric or length limits,
``additionalProperties: false`` on every object, and every property required (optional ones are
nullable). Limits the schema can't express are checked afterwards by ``validate_plan``.
"""

import json
from collections.abc import Iterable, Mapping
from typing import Any

from vibe.agent.plan import REF_KEY
from vibe.agent.planner import LlmError
from vibe.agent.providers import Provider, Reply
from vibe.toolbox import ToolSpec

KNOWN_LABELS: tuple[str, ...] = ("face", "person", "license_plate")
"""Detector labels ``find_objects`` can find. An assumption until F-10 settles the detectors'
labels: an LLM can't guess them, and a near miss ("plates") would find nothing."""

RENDERER_FIELDS = frozenset({"padding", "strength"})
"""Effect settings the renderer owns. The LLM only picks the effect type."""

_UNSUPPORTED = frozenset(
    {
        "title",
        "default",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "pattern",
        "format",
    }
)
"""JSON Schema keywords some structured-output modes reject. ``validate_plan`` checks them."""

_NULL: dict[str, Any] = {"type": "null"}


def ref_schema(ref_key: str = REF_KEY) -> dict[str, Any]:
    """A reference to an earlier step's result, ``{"$ref": "s1"}``."""
    return _object({ref_key: {"type": "string", "description": "The step_id of an earlier step."}})


def reply_schema(
    registry: Mapping[str, ToolSpec],
    ref_key: str = REF_KEY,
    labels: Iterable[str] = KNOWN_LABELS,
) -> dict[str, Any]:
    """The JSON schema the LLM's reply must follow: a question, or a plan of calls."""
    calls = [_call_schema(spec, ref_key, tuple(labels)) for spec in registry.values()]
    return _object(
        {
            "clarify": {
                "anyOf": [{"type": "string"}, _NULL],
                "description": "A question for the user instead of a plan; null when planning.",
            },
            "calls": {"type": "array", "items": {"anyOf": calls}},
        }
    )


def parse_reply(text: str, ref_key: str = REF_KEY) -> dict[str, Any]:
    """The LLM's reply as ``{"clarify": question}`` or ``{"calls": [...]}``.

    Null arguments are dropped, so the tool's default applies (every nullable argument defaults
    to null, and some fields reject an explicit null). A reply that isn't a JSON object comes
    back as one that fails plan validation, so the planner retries it with the error.
    """
    try:
        reply = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"invalid_reply": f"not JSON ({exc.msg}): {text[:300]}"}
    if not isinstance(reply, dict):
        return {"invalid_reply": f"not a JSON object: {text[:300]}"}
    question = reply.get("clarify")
    if isinstance(question, str) and question.strip():
        return {"clarify": question.strip()}  # asking wins over a plan sent alongside it
    return {"calls": _clean(reply.get("calls"), ref_key)}


def _clean(value: Any, ref_key: str) -> Any:
    if isinstance(value, list):
        return [_clean(v, ref_key) for v in value]
    if not isinstance(value, dict):
        return value
    if value.keys() == {ref_key} and ref_key != REF_KEY:
        return {REF_KEY: value[ref_key]}
    return {k: _clean(v, ref_key) for k, v in value.items() if v is not None}


# --- Prompt ---------------------------------------------------------------------------------

_RULES = """\
You plan video edits. You turn one chat request into a JSON plan of tool calls, which code then \
runs. You never touch pixels or audio yourself.

Reply with JSON only, in one of two shapes:
- a plan: {"clarify": null, "calls": [...]}
- a question for the user: {"clarify": "<question>", "calls": []}

How a plan works:
- Each call is {"step_id": "s1", "tool": "<name>", "args": {...}}. Step ids are unique: s1, s2, ...
- An argument {"$ref": "s1"} means "the result of step s1". Only an earlier step can be used.
- Query tools look at the video and return results. Edit tools change the video. Every plan \
needs at least one edit.

Rules:
1. Targets to hide always come from find_objects, passed on with {"$ref": ...}. Never write track \
ids yourself. find_objects knows exactly these labels: {labels}.
2. Times are seconds in the original video. A time the user wrote out ("0:10 to 0:20", \
"after 1:30", "from 30 seconds in") becomes a literal time_range like {"start": 10, "end": 20}. \
"end": null means until the end of the video. "1:30" is 90 seconds.
3. A time that depends on the video's length goes through resolve_time, and the edit uses its \
result. resolve_time understands exactly: "first half", "second half", "first <N> seconds", \
"last <N> seconds", "first <N> minutes", "last <N> minutes", with N in digits (e.g. \
"last 10 seconds"), or a clock time like "last 0:10".
4. No time means the whole video: time_range is null.
5. Effects are "blur", "pixelate" or "box" (a solid black box). "Hide", "censor" and "cover" mean \
blur unless the user names another effect.
6. Ask instead of guessing, with the question in "clarify" and no calls, when:
   - it isn't clear who or what to edit ("blur him"), or the request is vague ("make it nicer");
   - a number has no unit and could be seconds or minutes ("cut 10 to 20");
   - a cut has no clear time range: never make up a time to cut;
   - only some of the actions have a time and it's unclear whether it covers the others \
("blur faces and mute 0:10 to 0:20"); "for the whole video" or "the whole time" settles it;
   - no tool can do what was asked (for example removing a person from the video). Say what you \
can do instead.
7. Several actions in one request go in one plan, in the order they were asked."""

_EXAMPLES = (
    (
        "pixelate everyone after 2:05",
        {
            "clarify": None,
            "calls": [
                {"step_id": "s1", "tool": "find_objects", "args": {"label": "person"}},
                {
                    "step_id": "s2",
                    "tool": "add_mask",
                    "args": {
                        "targets": {REF_KEY: "s1"},
                        "effect": {"type": "pixelate"},
                        "time_range": {"start": 125, "end": None},
                    },
                },
            ],
        },
    ),
    (
        "silence from 45 seconds to a minute",
        {
            "clarify": None,
            "calls": [
                {
                    "step_id": "s1",
                    "tool": "mute",
                    "args": {"time_range": {"start": 45, "end": 60}, "speaker": None},
                }
            ],
        },
    ),
    (
        "mute the last 15 seconds and box out the plates for the whole clip",
        {
            "clarify": None,
            "calls": [
                {"step_id": "s1", "tool": "resolve_time", "args": {"phrase": "last 15 seconds"}},
                {
                    "step_id": "s2",
                    "tool": "mute",
                    "args": {"time_range": {REF_KEY: "s1"}, "speaker": None},
                },
                {"step_id": "s3", "tool": "find_objects", "args": {"label": "license_plate"}},
                {
                    "step_id": "s4",
                    "tool": "add_mask",
                    "args": {
                        "targets": {REF_KEY: "s3"},
                        "effect": {"type": "box"},
                        "time_range": None,
                    },
                },
            ],
        },
    ),
    (
        "make it look cinematic",
        {
            "clarify": "I can't change the look of the video. I can blur, pixelate or box "
            "faces, people or license plates, cut a time range, or mute. What would you like?",
            "calls": [],
        },
    ),
)
"""Few-shot examples. None of them is in the benchmark's request set, so scores aren't inflated."""


def _prompt(tool_lines: str, labels: str, ref_key: str) -> str:
    examples = "\n\n".join(
        f"Request: {request}\nReply: {json.dumps(reply).replace(REF_KEY, ref_key)}"
        for request, reply in _EXAMPLES
    )
    rules = _RULES.replace("{labels}", labels).replace('{"$ref"', '{"' + ref_key + '"')
    return f"{rules}\n\nTools:\n{tool_lines}\n\nExamples:\n\n{examples}"


def system_prompt(
    registry: Mapping[str, ToolSpec],
    ref_key: str = REF_KEY,
    labels: Iterable[str] = KNOWN_LABELS,
) -> str:
    """The fixed system prompt for ``registry``. Same bytes every call, so providers cache it."""
    labels = tuple(labels)
    lines = "\n".join(_tool_line(spec, ref_key, labels) for spec in registry.values())
    return _prompt(lines, ", ".join(labels), ref_key)


def user_message(request: str, error: str | None, previous: str | None) -> str:
    """The request, plus the rejected reply and why on the retry."""
    if error is None:
        return f"Request: {request}"
    return (
        f"Request: {request}\n\nYour previous reply was:\n{previous}\n\n"
        f"It was rejected: {error}\nReply again with a corrected plan, or ask the user."
    )


def _tool_line(spec: ToolSpec, ref_key: str, labels: tuple[str, ...]) -> str:
    args = _args_schema(spec, ref_key, labels)["properties"]
    params = ", ".join(f"{name}: {_describe(schema, ref_key)}" for name, schema in args.items())
    returns = "" if spec.returns == "none" else f" Returns {spec.returns}."
    refs = "".join(
        f' {name} may be {{"{ref_key}": <step>}} for a step returning {kind}.'
        for name, kind in spec.ref_args.items()
        if name not in spec.ref_required
    )
    must = "".join(
        f' {name} must be {{"{ref_key}": <step>}} for a step returning {spec.ref_args[name]}.'
        for name in sorted(spec.ref_required)
    )
    return f"- {spec.name}({params}) [{spec.kind}]: {spec.description}{returns}{refs}{must}"


def _describe(schema: Mapping[str, Any], ref_key: str) -> str:
    if "anyOf" in schema:
        return " | ".join(_describe(s, ref_key) for s in schema["anyOf"])
    if "enum" in schema:
        return "|".join(json.dumps(v) for v in schema["enum"])
    kind = schema.get("type")
    if kind == "object":
        props = schema.get("properties", {})
        if props.keys() == {ref_key}:
            return f'{{"{ref_key}": <step>}}'
        return "{" + ", ".join(f"{k}: {_describe(v, ref_key)}" for k, v in props.items()) + "}"
    if kind == "array":
        return f"list of {_describe(schema.get('items', {}), ref_key)}"
    return str(kind)


# --- Schema building ------------------------------------------------------------------------


def _call_schema(spec: ToolSpec, ref_key: str, labels: tuple[str, ...]) -> dict[str, Any]:
    return _object(
        {
            "step_id": {"type": "string"},
            # A one-value enum is accepted more widely than "const".
            "tool": {"type": "string", "enum": [spec.name]},
            "args": _args_schema(spec, ref_key, labels),
        }
    )


def _args_schema(spec: ToolSpec, ref_key: str, labels: tuple[str, ...]) -> dict[str, Any]:
    raw = spec.args.model_json_schema()
    schema = _portable(raw, raw.get("$defs", {}))
    props = schema["properties"]
    for name in props:
        if name in spec.ref_required:
            props[name] = ref_schema(ref_key)
        elif name in spec.ref_args:
            props[name] = _union(props[name], ref_schema(ref_key))
    if "label" in props and labels:
        props["label"] = {"type": "string", "enum": list(labels)}
    return schema


def _portable(node: Any, defs: Mapping[str, Any]) -> Any:
    """``node`` with ``$defs`` inlined, unsupported keywords dropped, and every object closed
    with all its properties required (optional ones made nullable)."""
    if isinstance(node, list):
        return [_portable(n, defs) for n in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:  # a JSON Schema reference to $defs, not a plan's {"$ref": step}
        target = dict(defs[node["$ref"].rsplit("/", 1)[-1]])
        target.pop("description", None)  # the field's own description is enough
        siblings = {k: v for k, v in node.items() if k != "$ref"}
        return _portable({**target, **siblings}, defs)
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _UNSUPPORTED or key == "$defs":
            continue
        if key == "properties":
            required = set(node.get("required", ()))
            out[key] = {
                name: _portable(prop, defs)
                if name in required
                else _nullable(_portable(prop, defs))
                for name, prop in value.items()
                if name not in RENDERER_FIELDS
            }
        else:
            out[key] = _portable(value, defs)
    if out.get("type") == "object":
        out.setdefault("properties", {})
        out["required"] = list(out["properties"])
        out["additionalProperties"] = False
    return out


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _options(schema: Mapping[str, Any]) -> list[Any]:
    return list(schema["anyOf"]) if set(schema) <= {"anyOf", "description"} else [dict(schema)]


def _union(*schemas: Mapping[str, Any]) -> dict[str, Any]:
    """One ``anyOf`` of all the options, with null (if any) last and the first description."""
    options: list[Any] = []
    for schema in schemas:
        for option in _options(schema):
            if option not in options:
                options.append(option)
    options.sort(key=lambda o: o == _NULL)
    out: dict[str, Any] = {"anyOf": options}
    if description := next((s["description"] for s in schemas if "description" in s), None):
        out["description"] = description
    return out


def _nullable(schema: Mapping[str, Any]) -> dict[str, Any]:
    if _NULL in _options(schema):
        return dict(schema)
    return _union(schema, _NULL)


# --- The planner's LLM slot -----------------------------------------------------------------


class SchemaPlanner:
    """``LlmPlanner`` backed by a provider's schema-constrained JSON mode.

    ``attempts`` holds every ``Reply`` for the current request (a request starts with
    ``error=None``), so cost and latency include the retry. One request at a time per instance.
    """

    def __init__(
        self,
        provider: Provider,
        ref_key: str = REF_KEY,
        labels: Iterable[str] = KNOWN_LABELS,
    ) -> None:
        self.provider = provider
        self.ref_key = ref_key
        self.labels = tuple(labels)
        self.attempts: list[Reply] = []

    def plan(
        self, request: str, tools: Mapping[str, ToolSpec], error: str | None
    ) -> Mapping[str, Any]:
        system = system_prompt(tools, self.ref_key, self.labels)
        schema = reply_schema(tools, self.ref_key, self.labels)
        if error is None:
            self.attempts = []
        previous = self.attempts[-1].text if error is not None and self.attempts else None
        reply = self.provider.complete(system, user_message(request, error, previous), schema)
        self.attempts.append(reply)
        if reply.stop_reason == "refusal":
            raise LlmError(f"{self.provider.name} refused the request")
        return parse_reply(reply.text, self.ref_key)
