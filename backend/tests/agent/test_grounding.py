"""The guard on LLM cuts: a cut or keep at a time the user didn't give is asked about first."""

import re
from collections.abc import Mapping
from typing import Any

import pytest

from vibe.agent import Clarify, Plan, plan_request
from vibe.agent.grounding import InventedCut, clock, confirm_question, invented_cuts, written_times
from vibe.agent.plan import ref
from vibe.agent.planner import INVENTED_CUT, plan_with_llm
from vibe.contract import TimeRange
from vibe.toolbox import TOOLS, ToolSpec


def plan(*calls: tuple[str, str, Mapping[str, Any]]) -> Plan:
    return Plan.model_validate(
        {"calls": [{"step_id": s, "tool": t, "args": dict(a)} for s, t, a in calls]}
    )


def cut(start: float, end: float | None) -> Plan:
    return plan(("s1", "cut_range", {"time_range": {"start": start, "end": end}}))


@pytest.mark.parametrize(
    ("text", "times"),
    [
        ("cut 0:10 to 0:20", [10, 20]),
        ("silence everything after 1:30", [90]),
        ("the first 30 seconds", [30]),
        ("keep 2 minutes", [120]),
        ("cut 10 to 20", []),  # bare numbers: seconds or minutes?
        ("cut the boring part", []),
    ],
)
def test_written_times(text: str, times: list[float]) -> None:
    assert written_times(text) == times


@pytest.mark.parametrize(("seconds", "text"), [(0, "0:00"), (70, "1:10"), (65.5, "1:05.5")])
def test_clock(seconds: float, text: str) -> None:
    assert clock(seconds) == text


@pytest.mark.parametrize(
    ("request_text", "planned"),
    [
        ("cut 0:10 to 0:20", cut(10, 20)),
        ("take out 0:10-0:20", cut(10, 20)),
        ("cut everything after 1:30", cut(90, None)),
        ("cut before 0:20", cut(0, 20)),  # the start of the video needs no written time
        (
            "cut the last third please",
            plan(
                ("s1", "resolve_time", {"phrase": "last third"}),
                ("s2", "cut_range", {"time_range": ref("s1")}),
            ),
        ),
        (  # not a cut: only cuts and keeps lose footage
            "mute the swearing",
            plan(("s1", "mute", {"time_range": {"start": 30, "end": 35}})),
        ),
    ],
)
def test_times_the_user_gave_pass(request_text: str, planned: Plan) -> None:
    assert invented_cuts(request_text, planned) == []


@pytest.mark.parametrize(
    ("request_text", "planned", "invented"),
    [
        ("cut the boring part", cut(30, 50), InventedCut("cut_range", "0:30 to 0:50")),
        ("cut the intro", cut(0, 15), InventedCut("cut_range", "0:00 to 0:15")),
        ("cut 10 to 20", cut(10, 20), InventedCut("cut_range", "0:10 to 0:20")),
        ("remove the ending", cut(100, None), InventedCut("cut_range", "after 1:40")),
        (
            "get rid of the first ten seconds",  # number words: confirmed, not guessed
            plan(
                ("s1", "resolve_time", {"phrase": "first 10 seconds"}),
                ("s2", "cut_range", {"time_range": ref("s1")}),
            ),
            InventedCut("cut_range", "the first 10 seconds"),
        ),
        (
            "keep the good bit",
            plan(("s1", "keep_range", {"time_range": {"start": 10, "end": 40}})),
            InventedCut("keep_range", "0:10 to 0:40"),
        ),
    ],
)
def test_a_cut_at_a_time_nobody_gave_is_invented(
    request_text: str, planned: Plan, invented: InventedCut
) -> None:
    assert invented_cuts(request_text, planned) == [invented]


@pytest.mark.parametrize(
    "planned",
    [
        cut(30, 50),
        cut(100, None),
        plan(("s1", "keep_range", {"time_range": {"start": 10, "end": 40}})),
        plan(
            ("s1", "resolve_time", {"phrase": "middle third"}),
            ("s2", "cut_range", {"time_range": ref("s1")}),
        ),
    ],
)
def test_the_confirmation_the_user_is_told_to_say_makes_the_same_cut(planned: Plan) -> None:
    question = confirm_question(invented_cuts("cut the boring part", planned), planned)
    saying = re.search(r"say '([^']+)'", question)
    assert saying is not None

    again = plan_request(saying.group(1))

    assert isinstance(again, Plan)
    assert _same(again) == _same(planned)


def _same(p: Plan) -> list[tuple[str, dict[str, Any]]]:
    """The calls with literal ranges as ``TimeRange`` dumps: an omitted end is a null end."""
    out = []
    for call in p.calls:
        args = dict(call.args)
        if isinstance(args.get("time_range"), dict) and "start" in args["time_range"]:
            args["time_range"] = TimeRange.model_validate(args["time_range"]).model_dump()
        out.append((call.tool, args))
    return out


def test_the_question_says_the_other_edits_need_asking_again() -> None:
    planned = plan(
        ("s1", "mute", {}),
        ("s2", "cut_range", {"time_range": {"start": 30, "end": 50}}),
    )

    question = confirm_question(invented_cuts("mute it and cut the boring part", planned), planned)

    assert question == (
        "I'd cut 0:30 to 0:50, but you didn't give that time, so it's my guess. Is it right? "
        "To go ahead, say 'cut 0:30 to 0:50'. Then ask for the other edits again."
    )


class Writes:
    """A fake LLM that writes one plan."""

    def __init__(self, planned: Plan) -> None:
        self.planned = planned

    def plan(
        self, request: str, tools: Mapping[str, ToolSpec], error: str | None
    ) -> Mapping[str, Any]:
        return self.planned.model_dump()


def test_the_planner_asks_before_an_invented_cut() -> None:
    result = plan_with_llm("cut the boring part", TOOLS, Writes(cut(30, 50)))

    assert isinstance(result, Clarify)
    assert result.reason == INVENTED_CUT


def test_the_benchmark_can_score_the_plan_itself() -> None:
    result = plan_with_llm("cut the boring part", TOOLS, Writes(cut(30, 50)), confirm_cuts=False)

    assert result == cut(30, 50)


def test_plan_request_goes_through_the_guard_too() -> None:
    result = plan_request("cut out the boring part", llm=Writes(cut(30, 50)))

    assert isinstance(result, Clarify)
    assert result.reason == INVENTED_CUT
