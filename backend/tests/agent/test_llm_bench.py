"""R-1 benchmark, offline: the request set, the scorer, a run with a fake provider, the report."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from vibe.agent import Plan, plan_request, validate_plan
from vibe.agent.bench.candidates import Candidate
from vibe.agent.bench.cases import Case, load_cases, registry_for
from vibe.agent.bench.report import load_records, report
from vibe.agent.bench.run import run_candidate
from vibe.agent.bench.score import KEYWORDS, request_times, score, template_claims
from vibe.agent.llm import _EXAMPLES
from vibe.agent.plan import ref
from vibe.agent.providers import Reply, Usage

CASES = load_cases()
BY_REQUEST = {c.request: c for c in CASES}


def _as_text(value: Any) -> Any:
    """Gold free text written as keywords, turned into a string so the plan validates."""
    if isinstance(value, dict) and value.keys() == {KEYWORDS}:
        return " ".join(w if isinstance(w, str) else w[0] for w in value[KEYWORDS])
    if isinstance(value, dict):
        return {k: _as_text(v) for k, v in value.items()}
    return value


# --- Request set ----------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_every_gold_plan_is_valid_for_its_tools(case: Case) -> None:
    for gold in case.golds:
        plan = Plan.model_validate({"calls": [_as_text(c) for c in gold]})
        assert validate_plan(plan, registry_for(case)) == []


def test_every_gold_plan_scores_correct_against_itself() -> None:
    for case in CASES:
        for gold in case.golds:
            result = {"type": "plan", "calls": [_as_text(c) for c in gold]}
            assert score(case, result).outcome == "correct", case


LLM_ROUTED = [c for c in CASES if c.source == "llm"]


@pytest.mark.parametrize("case", LLM_ROUTED, ids=[c.id for c in LLM_ROUTED])
def test_the_template_parser_never_claims_an_llm_request(case: Case) -> None:
    result = plan_request(case.request)

    assert not isinstance(result, Plan)
    assert result.reason == "no template matched"


def test_few_shot_examples_are_not_in_the_scored_set() -> None:
    requests = {c.request.lower() for c in CASES}

    assert not {r.lower() for r, _ in _EXAMPLES} & requests


def test_both_sets_load_with_both_tool_sets() -> None:
    assert {c.source for c in CASES} == {"draft", "llm"}
    assert {c.tools for c in CASES} == {"core", "extras"}


# --- Scorer ---------------------------------------------------------------------------------


def plan(*calls: tuple[str, str, Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "type": "plan",
        "calls": [{"step_id": s, "tool": t, "args": dict(a)} for s, t, a in calls],
    }


def clarify(reason: str = "LLM asked") -> dict[str, Any]:
    return {"type": "clarify", "question": "Which one?", "reason": reason}


def test_a_literal_range_equals_the_resolve_time_step_it_stands_for() -> None:
    case = BY_REQUEST["get rid of the first ten seconds"]

    result = plan(("a", "cut_range", {"time_range": {"start": 0, "end": 10}}))

    assert score(case, result).outcome == "correct"


def test_step_names_and_independent_order_dont_matter() -> None:
    case = BY_REQUEST["silence 1:05 to 1:20, and blur the faces the whole time"]

    result = plan(
        ("x", "find_objects", {"label": "face"}),
        ("y", "add_mask", {"targets": ref("x"), "effect": {"type": "blur"}, "time_range": None}),
        ("z", "mute", {"time_range": {"start": 65, "end": 80}}),
    )

    assert score(case, result).outcome == "correct"


def test_the_whole_clip_can_be_written_three_ways() -> None:
    case = BY_REQUEST["kill the audio"]

    for args in ({}, {"time_range": None}, {"time_range": {"start": 0}}):
        assert score(case, plan(("s1", "mute", args))).outcome == "correct"


def test_free_text_matches_by_keywords() -> None:
    case = BY_REQUEST["blur the guy in the red hoodie"]

    def with_question(question: str) -> dict[str, Any]:
        return plan(
            ("s1", "find_objects", {"label": "person"}),
            ("s2", "check_attribute", {"targets": ref("s1"), "question": question, "keep": "yes"}),
            ("s3", "add_mask", {"targets": ref("s2"), "effect": {"type": "blur"}}),
        )

    assert score(case, with_question("Is this person wearing a red hoodie?")).outcome == "correct"
    assert score(case, with_question("Is this person wearing red?")).outcome == "wrong_plan"


def test_one_of_several_words_is_enough_in_a_keyword_group() -> None:
    case = BY_REQUEST["blur everyone except me, I'm in the black tee"]

    result = plan(
        ("s1", "find_objects", {"label": "person"}),
        (
            "s2",
            "check_attribute",
            {"targets": ref("s1"), "question": "black t-shirt?", "keep": "no"},
        ),
        ("s3", "add_mask", {"targets": ref("s2"), "effect": {"type": "blur"}}),
    )

    assert score(case, result).outcome == "correct"


def test_an_alternative_plan_counts_as_correct() -> None:
    case = BY_REQUEST["can you make the license plates unreadable"]

    result = plan(
        ("s1", "find_objects", {"label": "license_plate"}),
        ("s2", "add_mask", {"targets": ref("s1"), "effect": {"type": "box"}}),
    )

    assert score(case, result).outcome == "correct"


@pytest.mark.parametrize(
    ("request_text", "result", "outcome"),
    [
        ("blur him", clarify(), "correct"),
        ("kill the audio", clarify(), "over_asked"),
        ("blur him", plan(("s1", "mute", {})), "unsafe"),
        (
            "get rid of the first ten seconds",
            plan(("s1", "cut_range", {"time_range": {"start": 0, "end": 15}})),
            "unsafe",
        ),
        (
            "pixelate faces from 30 seconds in until the end",
            plan(
                ("s1", "find_objects", {"label": "faces"}),
                ("s2", "add_mask", {"targets": ref("s1"), "effect": {"type": "pixelate"}}),
            ),
            "unknown_label",
        ),
        (
            "hide everyone's faces in the second half",
            plan(
                ("s1", "find_objects", {"label": "face"}),
                ("s2", "resolve_time", {"phrase": "the back half"}),
                (
                    "s3",
                    "add_mask",
                    {"targets": ref("s1"), "effect": {"type": "blur"}, "time_range": ref("s2")},
                ),
            ),
            "unresolvable_time",
        ),
        (
            "blurr all the faces pls",
            plan(
                ("s1", "find_objects", {"label": "face"}),
                ("s2", "add_mask", {"targets": ref("s1"), "effect": {"type": "pixelate"}}),
            ),
            "wrong_plan",
        ),
        ("kill the audio", clarify("LLM plan invalid twice: x | y"), "invalid"),
        ("kill the audio", clarify("LLM call failed"), "provider_error"),
    ],
)
def test_each_outcome(request_text: str, result: dict[str, Any], outcome: str) -> None:
    assert score(BY_REQUEST[request_text], result).outcome == outcome


def test_a_refused_call_is_scored_as_a_refusal() -> None:
    attempts = [{"stop_reason": "refusal"}]

    assert score(BY_REQUEST["kill the audio"], clarify("LLM call failed"), attempts).outcome == (
        "refusal"
    )


def test_a_wrong_plan_says_what_differs() -> None:
    result = plan(
        ("s1", "find_objects", {"label": "face"}),
        ("s2", "add_mask", {"targets": ref("s1"), "effect": {"type": "pixelate"}}),
    )

    assert score(BY_REQUEST["blurr all the faces pls"], result).detail == "effect"


def test_request_times_reads_clock_times_and_both_units_of_a_bare_number() -> None:
    assert request_times("silence 1:05 to 1:20") == {65, 80}
    assert {10, 20, 600, 1200} <= request_times("cut 10 to 20")
    assert request_times("after the 1 minute mark") >= {60}


def test_template_claims_its_plans_and_its_time_questions_but_not_no_match() -> None:
    assert template_claims({"type": "plan", "calls": []})
    assert template_claims(clarify("time range can't be used"))
    assert not template_claims(clarify("no template matched"))


# --- A run and its report, with a fake provider ---------------------------------------------


class AlwaysAsks:
    name = "fake:asks"
    model = "asks"
    settings: Mapping[str, Any] = {"fake": True}

    def complete(self, system: str, user: str, schema: Mapping[str, Any]) -> Reply:
        text = json.dumps({"clarify": "Could you say more?", "calls": []})
        return Reply(text, Usage(input=100, output=10, cache_read=900), 0.25, "end_turn")

    def describe(self) -> dict[str, Any]:
        return {}


def test_a_run_saves_every_reply_and_the_report_scores_it(tmp_path: Path) -> None:
    cases = [BY_REQUEST["blur him"], BY_REQUEST["kill the audio"], BY_REQUEST["undo that"]]
    candidate = Candidate("fake-asks", AlwaysAsks, None)

    path = run_candidate(
        candidate, cases, 2, max_usd=None, hardware_label="test", network="none", out_dir=tmp_path
    )
    records = load_records([path])
    text = report(records, CASES, detail=True)

    assert len(records) == 6
    # The first request of each tool set sends a new schema and prompt.
    assert [r["first_call"] for r in records] == [True, False, True, False, False, False]
    assert all(r["attempts"][0]["usage"]["cache_read"] == 900 for r in records)
    row = next(line for line in text.splitlines() if line.startswith("| fake-asks | 3 x 2"))
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[5] == "33%"  # correct: only "blur him" should be a question
    assert cells[7] == "4"  # over-asked: the other two, twice each
    assert "Candidate zero" in text
    assert "hardware test, network none" in text
