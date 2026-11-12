"""R-1 benchmark, offline: the request set, the scorer, a run with a fake provider, the report."""

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from vibe.agent import Plan, plan_request, validate_plan
from vibe.agent.bench import DECISION_RUNS
from vibe.agent.bench import run as bench_run
from vibe.agent.bench.candidates import CANDIDATES, Candidate
from vibe.agent.bench.cases import Case, load_cases, registry_for
from vibe.agent.bench.report import decision, load_records, report
from vibe.agent.bench.run import PaidCandidate, run_candidate
from vibe.agent.bench.score import KEYWORDS, Score, score, template_claims
from vibe.agent.llm import _EXAMPLES
from vibe.agent.plan import ref
from vibe.agent.planner import LlmError
from vibe.agent.providers import RateLimited, Reply, Usage

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
            "get rid of the first ten seconds",
            plan(
                ("s1", "resolve_time", {"phrase": "first 20 seconds"}),
                ("s2", "cut_range", {"time_range": ref("s1")}),
            ),
            "unsafe",
        ),
        (
            "take out 0:10-0:20 and also 1:00-1:10",
            plan(("s1", "cut_range", {"time_range": {"start": 10, "end": 70}})),
            "unsafe",
        ),
        (
            "silence 1:05 to 1:20, and blur the faces the whole time",
            plan(("s1", "cut_range", {"time_range": {"start": 65, "end": 80}})),
            "unsafe",
        ),
        (
            "take out 0:10-0:20 and also 1:00-1:10",  # one cut left out only keeps footage
            plan(("s1", "cut_range", {"time_range": {"start": 10, "end": 20}})),
            "wrong_plan",
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
        (  # a keep counts as the cuts it makes on the 120 s fixture
            "keep only the part from one minute to the end",
            plan(("s1", "cut_range", {"time_range": {"start": 0, "end": 60}})),
            "correct",
        ),
        (
            "keep only the part from one minute to the end",
            plan(("s1", "keep_range", {"time_range": {"start": 90}})),
            "unsafe",
        ),
        (
            "kill the audio",
            plan(("s1", "mute", {}), ("s2", "keep_range", {"time_range": {"start": 0, "end": 60}})),
            "unsafe",
        ),
        (  # masks faces for less of the time the gold plan does
            "hide everyone's faces in the second half",
            plan(
                ("s1", "find_objects", {"label": "face"}),
                (
                    "s2",
                    "add_mask",
                    {"targets": ref("s1"), "effect": {"type": "blur"}, "time_range": {"start": 90}},
                ),
            ),
            "unsafe",
        ),
        (  # drops the mask altogether
            "silence 1:05 to 1:20, and blur the faces the whole time",
            plan(("s1", "mute", {"time_range": {"start": 65, "end": 80}})),
            "unsafe",
        ),
        (  # people cover their faces: more is masked, not less
            "hide everyone's faces in the second half",
            plan(
                ("s1", "find_objects", {"label": "person"}),
                ("s2", "resolve_time", {"phrase": "second half"}),
                (
                    "s3",
                    "add_mask",
                    {"targets": ref("s1"), "effect": {"type": "blur"}, "time_range": ref("s2")},
                ),
            ),
            "wrong_plan",
        ),
        (  # two masks that cover the whole clip between them leave nothing visible
            "blurr all the faces pls",
            plan(
                ("s1", "find_objects", {"label": "face"}),
                (
                    "s2",
                    "add_mask",
                    {
                        "targets": ref("s1"),
                        "effect": {"type": "blur"},
                        "time_range": {"start": 0, "end": 60},
                    },
                ),
                (
                    "s3",
                    "add_mask",
                    {"targets": ref("s1"), "effect": {"type": "blur"}, "time_range": {"start": 60}},
                ),
            ),
            "wrong_plan",
        ),
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
    candidate = Candidate("fake-asks", AlwaysAsks, "local")

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


class TimesOut:
    name = "fake:times-out"
    model = "times-out"
    settings: Mapping[str, Any] = {}

    def complete(self, system: str, user: str, schema: Mapping[str, Any]) -> Reply:
        time.sleep(0.01)
        raise LlmError("timed out")

    def describe(self) -> dict[str, Any]:
        return {}


def test_a_failed_call_keeps_the_time_it_took(tmp_path: Path) -> None:
    path = run_candidate(
        Candidate("fake-times-out", TimesOut, "local"),
        [BY_REQUEST["kill the audio"]],
        1,
        max_usd=None,
        hardware_label="test",
        network="none",
        out_dir=tmp_path,
    )

    record = load_records([path])[0]

    assert record["attempts"] == []
    assert record["seconds"] >= 0.01


class RateLimitedAfter:
    """Answers ``ok`` requests, then says 429 like a free tier at its daily cap."""

    name = "fake:capped"
    model = "capped"
    settings: Mapping[str, Any] = {}

    def __init__(self, ok: int) -> None:
        self.ok = ok

    def complete(self, system: str, user: str, schema: Mapping[str, Any]) -> Reply:
        if self.ok <= 0:
            raise RateLimited("429: daily cap")
        self.ok -= 1
        return AlwaysAsks().complete(system, user, schema)

    def describe(self) -> dict[str, Any]:
        return {}


def test_a_rate_limit_stops_the_run_and_resume_finishes_it(tmp_path: Path) -> None:
    cases = [BY_REQUEST["blur him"], BY_REQUEST["kill the audio"], BY_REQUEST["cut it"]]
    common = {"max_usd": None, "hardware_label": "test", "network": "none", "out_dir": tmp_path}

    first = run_candidate(
        Candidate("capped", lambda: RateLimitedAfter(1), "free"), cases, 1, **common
    )
    assert [r["request"] for r in load_records([first])] == ["blur him"]  # no failure scored

    second = run_candidate(
        Candidate("capped", lambda: RateLimitedAfter(9), "free"), cases, 1, resume=True, **common
    )
    assert [r["request"] for r in load_records([second])] == ["kill the audio", "cut it"]


def test_calls_are_paced_to_the_rate_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []
    monkeypatch.setattr(bench_run, "sleep", waits.append)
    cases = [BY_REQUEST["blur him"], BY_REQUEST["kill the audio"]]

    run_candidate(
        Candidate("fake-asks", AlwaysAsks, "free"),
        cases,
        1,
        max_usd=None,
        hardware_label="test",
        network="none",
        out_dir=tmp_path,
        rpm=60,
    )

    assert len(waits) == 1 and 0.5 < waits[0] <= 1.0  # one second between the two calls


def test_a_paid_candidate_never_runs_without_allow_paid(tmp_path: Path) -> None:
    with pytest.raises(PaidCandidate):
        run_candidate(
            CANDIDATES["opus-5-5"],
            [BY_REQUEST["kill the audio"]],
            1,
            max_usd=None,
            hardware_label="test",
            network="none",
            out_dir=tmp_path,
        )
    assert not list(tmp_path.iterdir())


def test_the_report_leaves_out_results_from_an_older_prompt(tmp_path: Path) -> None:
    path = run_candidate(
        Candidate("fake-asks", AlwaysAsks, "local"),
        [BY_REQUEST["blur him"]],
        2,
        max_usd=None,
        hardware_label="test",
        network="none",
        out_dir=tmp_path,
    )
    records = load_records([path])
    records[0]["prompt_hash"] = "0ld"

    text = report(records, CASES)

    assert "Left out, made with an older prompt or schema: fake-asks 1." in text
    assert "| fake-asks | 1 x 1 |" in text


def test_an_empty_candidate_doesnt_crash_the_report(tmp_path: Path) -> None:
    path = run_candidate(
        Candidate("gone", AlwaysAsks, "local"),
        [BY_REQUEST["blur him"]],
        1,
        max_usd=None,
        hardware_label="test",
        network="none",
        out_dir=tmp_path,
    )
    records = load_records([path])
    records[0]["case"] = "core:a request since removed from the set"

    main_table = report(records, CASES).split("### Correct, by split")[0]

    assert "| gone |" not in main_table


# --- Decision rule --------------------------------------------------------------------------


def scored_runs(outcomes: list[str], seconds: float, runs: int = DECISION_RUNS) -> list[Any]:
    """``runs`` results for each request ``core:<i>``, with ``outcomes[i]`` every time."""
    return [
        (
            {
                "case": f"core:{i}",
                "tools": "core",
                "first_call": False,
                "seconds": seconds,
                "attempts": [{"seconds": seconds, "usage": {}}],
            },
            Score(o),
        )
        for _ in range(runs)
        for i, o in enumerate(outcomes)
    ]


LLM_CASES = frozenset(f"core:{i}" for i in range(10))


def test_decision_picks_the_most_correct_free_candidate_passing_every_gate() -> None:
    lines = decision(
        {
            "groq-gpt-oss-120b": scored_runs(["correct"] * 10, 6.0),  # p95 too slow
            "groq-gpt-oss-20b": scored_runs(["correct"] * 9 + ["wrong_plan"], 2.0),  # 90%
            "groq-qwen3.8-27b": scored_runs(["correct"] * 8 + ["wrong_plan"] * 2, 1.0),  # 80%
            "gemini-3.8-flash": scored_runs(["correct"] * 9 + ["unsafe"], 1.0),  # an unsafe
            "opus-5-5": scored_runs(["correct"] * 10, 1.0),  # paid: not decided on
            "openrouter-nemotron-3.5-super-vl": scored_runs(["correct"] * 10, 1.0),
            "qwen3.5-9b": scored_runs(["correct"] * 7 + ["wrong_plan"] * 3, 2.0),  # 70%
        },
        LLM_CASES,
    )
    table = lines[0]

    assert "**Winner: groq-gpt-oss-20b** (free hosted, every gate, most correct then fastest)." in (
        lines
    )
    assert "**Offline fallback: qwen3.5-9b** (2 of 3 gates)." in lines
    assert "| groq-qwen3.8-27b | free | 0 | 80% | 1.00 | pass pass pass |" in table
    assert "| opus-5-5 | paid | 0 | 100% | 1.00 | paid: not in the decision (no budget) |" in table
    assert "pass pass n/a (quality only)" in table


def test_decision_breaks_a_tie_on_p95() -> None:
    lines = decision(
        {
            "groq-gpt-oss-20b": scored_runs(["correct"] * 9 + ["wrong_plan"], 2.0),
            "groq-qwen3.8-27b": scored_runs(["correct"] * 9 + ["wrong_plan"], 1.5),
        },
        LLM_CASES,
    )

    assert "**Winner: groq-qwen3.8-27b** (free hosted, every gate, most correct then fastest)." in (
        lines
    )


def test_decision_only_compares_complete_runs() -> None:
    lines = decision(
        {
            "groq-gpt-oss-120b": scored_runs(["correct"] * 9 + ["unsafe"], 2.0),
            "groq-gpt-oss-20b": scored_runs(["correct"] * 10, 1.0, runs=3),  # no unsafe in 3
        },
        LLM_CASES,
    )

    # The partial run's zero doesn't become the bar: the complete one has the fewest, 1.
    assert "**Winner: groq-gpt-oss-120b**" in lines[-1]
    assert "not compared: 30 results, needs 50 (10 requests x 5 runs)" in lines[0]


def test_decision_counts_only_requests_that_reach_the_llm() -> None:
    template_taken = scored_runs(["unsafe"] * 12, 1.0)[10:12]  # core:10, core:11
    lines = decision(
        {"groq-gpt-oss-20b": scored_runs(["correct"] * 10, 1.0) + template_taken}, LLM_CASES
    )

    assert "**Winner: groq-gpt-oss-20b**" in lines[-1]


def test_decision_waits_for_results() -> None:
    assert decision({}, LLM_CASES) == ["No results on the core requests that reach the LLM yet."]
