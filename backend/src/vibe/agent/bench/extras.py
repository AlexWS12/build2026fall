"""**DRAFT, eval-only** tools: the toolbox (F-06) plus tools it doesn't have yet.

**Not the toolbox and not a shared file.** These exist so R-1 can see how candidate LLMs handle
the requests that need them (descriptive targets, speech, speakers, rules, undo), which the five
real tools can only answer with a question. Names follow docs/ARCHITECTURE.md ("Toolbox");
whoever builds them (F-06, F-13, F-21, F-32, F-51) decides the real names and arguments. Every
``run`` raises: plans using them are scored, never executed.
"""

from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Any, Literal

from pydantic import Field

from vibe.contract.common import Model, TimeRange
from vibe.toolbox import TOOLS, ToolContext, ToolSpec


class _NoResult(Model):
    pass


def _eval_only(args: Any, ctx: ToolContext) -> Model:
    raise NotImplementedError("eval-only tool (R-1 benchmark): scored, never run")


class CheckAttributeArgs(Model):
    targets: list[str] = Field(min_length=1, description="Track ids to ask about.")
    question: str = Field(
        min_length=1, description='A yes/no question about how a target looks: "wearing red?".'
    )
    keep: Literal["yes", "no"] = Field(
        description='"yes" keeps the targets that match; "no" keeps every other target.'
    )


class FindSpeechArgs(Model):
    phrase: str = Field(min_length=1, description="What is being talked about.")


class FindSpeakersArgs(Model):
    description: str = Field(min_length=1, description='Who speaks, e.g. "the woman on the left".')


class DenoiseArgs(Model):
    time_range: TimeRange | None = Field(default=None, description="Null = the whole clip.")


class AddRuleArgs(Model):
    category: Literal["face", "person", "license_plate"] = Field(
        description="What to mask on every frame from now on."
    )
    except_me: bool = Field(description="Keep the enrolled user (the person streaming) visible.")


class NoArgs(Model):
    pass


def _spec(
    name: str,
    kind: Literal["query", "edit"],
    description: str,
    args: type[Model],
    returns: str = "none",
    ref_args: Mapping[str, str] | None = None,
    ref_required: frozenset[str] = frozenset(),
) -> ToolSpec:
    return ToolSpec(
        name=name,
        kind=kind,
        description=description,
        args=args,
        result=_NoResult,
        returns=returns,  # type: ignore[arg-type]  # "speaker_id" is new: eval-only
        run=_eval_only,
        ref_args=dict(ref_args or {}),  # type: ignore[arg-type]
        ref_required=ref_required,
    )


_EXTRA = (
    _spec(
        "check_attribute",
        "query",
        "Ask a yes/no question about each target and keep some of them (unsure targets are "
        "always kept, so they get masked). Use it to pick people out by how they look.",
        CheckAttributeArgs,
        returns="track_ids",
        ref_args={"targets": "track_ids"},
        ref_required=frozenset({"targets"}),
    ),
    _spec(
        "find_speech",
        "query",
        "Find when something is talked about, from the transcript.",
        FindSpeechArgs,
        returns="time_range",
    ),
    _spec(
        "find_speakers",
        "query",
        "Find the speaker that matches a description, from who spoke when.",
        FindSpeakersArgs,
        returns="speaker_id",
    ),
    _spec(
        "denoise",
        "edit",
        "Reduce background noise over a time range.",
        DenoiseArgs,
        ref_args={"time_range": "time_range"},
    ),
    _spec(
        "add_rule",
        "edit",
        "A standing rule from now on, also live: mask every target of a category, except the "
        "enrolled user if asked.",
        AddRuleArgs,
    ),
    _spec("undo", "edit", "Undo the latest request.", NoArgs),
    _spec("redo", "edit", "Redo the request undone last.", NoArgs),
)

EXTRAS: Mapping[str, ToolSpec] = MappingProxyType(
    {
        **TOOLS,
        # mute can take a speaker found by find_speakers instead of a made-up id.
        "mute": replace(
            TOOLS["mute"], ref_args={**TOOLS["mute"].ref_args, "speaker": "speaker_id"}
        ),
        **{spec.name: spec for spec in _EXTRA},
    }
)
"""The five real tools plus the eval-only ones, for the ``extras`` requests."""
