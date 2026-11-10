"""F-12: time-bounded edits. ``resolve_range`` turns "first half", "after 0:30", "between 0:10
and 0:40" into a ``TimeRange`` inside the video; the planner and ``resolve_time`` use it.

The unit tests need no video. The round-trip tests use a 90 s testsrc2 clip from conftest.py.
"""

from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from vibe.agent import Plan, plan_request
from vibe.agent.plan import as_ref
from vibe.agent.timeparse import parse_time_phrase
from vibe.contract import OpLog, TimeRange, Track
from vibe.render import export
from vibe.toolbox import ToolContext, ToolError, call_tool
from vibe.toolbox.time_range import TimeRangeError, check_range, resolve_range
from vibe.video import probe

D = 90.0
"""The video length the unit tests resolve against, in seconds."""

EXAMPLES = Path(__file__).parents[2] / "src" / "vibe" / "contract" / "examples"
TRACKS = TypeAdapter(list[Track]).validate_json((EXAMPLES / "street.tracks.json").read_bytes())
"""People and plates, so "blur people ..." plans can run."""


def ctx(log: OpLog | None = None, duration: float | None = D) -> ToolContext:
    return ToolContext(
        request_id="req_1", source="agent", log=log, duration=duration, tracks=TRACKS
    )


# --- Grammar: each phrase on a 90 s video ---------------------------------------------------


def test_first_half() -> None:
    assert resolve_range("first half", D) == TimeRange(start=0, end=45)


def test_second_half_runs_to_the_end() -> None:
    assert resolve_range("second half", D) == TimeRange(start=45)


def test_first_third() -> None:
    assert resolve_range("the first third", D) == TimeRange(start=0, end=30)


def test_middle_third() -> None:
    assert resolve_range("the middle third", D) == TimeRange(start=30, end=60)


def test_last_third_runs_to_the_end() -> None:
    assert resolve_range("the last third", D) == TimeRange(start=60)


def test_first_n_minutes() -> None:
    assert resolve_range("first 1 minute", D) == TimeRange(start=0, end=60)


def test_first_n_seconds() -> None:
    assert resolve_range("first 5 seconds", D) == TimeRange(start=0, end=5)


def test_last_n_seconds() -> None:
    assert resolve_range("last 10 seconds", D) == TimeRange(start=80)


def test_last_clock_time() -> None:
    assert resolve_range("last 0:10", D) == TimeRange(start=80)


def test_after_m_ss_runs_to_the_end() -> None:
    assert resolve_range("after 0:30", D) == TimeRange(start=30)


def test_from_m_ss_onwards() -> None:
    assert resolve_range("from 0:30 onwards", D) == TimeRange(start=30)


def test_before_m_ss() -> None:
    assert resolve_range("before 0:30", D) == TimeRange(start=0, end=30)


def test_between_m_ss_and_m_ss() -> None:
    assert resolve_range("between 0:10 and 0:40", D) == TimeRange(start=10, end=40)


def test_m_ss_to_m_ss() -> None:
    assert resolve_range("0:10 to 0:40", D) == TimeRange(start=10, end=40)


def test_case_and_spacing_dont_matter() -> None:
    assert resolve_range("  The   FIRST half ", D) == TimeRange(start=0, end=45)


def test_non_integer_minutes() -> None:
    assert resolve_range("first 1.5 minutes", 120.0) == TimeRange(start=0, end=90)


# --- Edge cases -----------------------------------------------------------------------------


@pytest.mark.parametrize("duration", [0.0, -5.0, float("nan"), float("inf")])
def test_zero_length_or_invalid_duration_raises(duration: float) -> None:
    with pytest.raises(TimeRangeError, match="duration"):
        resolve_range("first half", duration)


def test_one_frame_clip_still_has_halves() -> None:
    frame = 1 / 30
    assert resolve_range("first half", frame) == TimeRange(start=0, end=frame / 2)
    assert resolve_range("second half", frame) == TimeRange(start=frame / 2)


def test_one_frame_clip_refuses_a_span_longer_than_itself() -> None:
    with pytest.raises(TimeRangeError, match="whole video"):
        resolve_range("first 1 second", 1 / 30)


def test_end_exactly_at_the_duration_is_allowed() -> None:
    assert resolve_range("between 0:30 and 1:30", D) == TimeRange(start=30, end=90)
    assert resolve_range("before 1:30", D) == TimeRange(start=0, end=90)


def test_end_one_frame_past_the_duration_raises() -> None:
    with pytest.raises(TimeRangeError, match="after the end"):
        resolve_range("between 0:30 and 1:30.1", D)  # one frame at 10 fps


def test_check_range_one_frame_past_the_duration_raises() -> None:
    assert check_range(TimeRange(start=0, end=D), D) == TimeRange(start=0, end=D)
    with pytest.raises(TimeRangeError, match="after the end"):
        check_range(TimeRange(start=0, end=D + 1 / 30), D)


@pytest.mark.parametrize("phrase", ["after 1:30", "after 2:00", "from 1:31 on"])
def test_start_at_or_after_the_end_raises(phrase: str) -> None:
    with pytest.raises(TimeRangeError, match="end of the video"):
        resolve_range(phrase, D)


@pytest.mark.parametrize("phrase", ["first 2 minutes", "last 1:30", "first 90 seconds"])
def test_clip_shorter_than_the_request_raises(phrase: str) -> None:
    # "cut the first 2 minutes" of a 90 s clip must ask, not cut everything.
    with pytest.raises(TimeRangeError, match="whole video"):
        resolve_range(phrase, D)


@pytest.mark.parametrize("phrase", ["last -5 seconds", "after -0:10", "first -1 minute"])
def test_negative_numbers_raise(phrase: str) -> None:
    with pytest.raises(TimeRangeError):
        resolve_range(phrase, D)


@pytest.mark.parametrize("phrase", ["between 0:40 and 0:10", "0:40 to 0:10"])
def test_swapped_bounds_raise(phrase: str) -> None:
    with pytest.raises(TimeRangeError, match="before or at the start"):
        resolve_range(phrase, D)


@pytest.mark.parametrize(
    "phrase", ["between 0:10 and 0:10", "before 0:00", "first 0 seconds", "last 0:00"]
)
def test_zero_length_ranges_raise(phrase: str) -> None:
    with pytest.raises(TimeRangeError, match="empty"):
        resolve_range(phrase, D)


@pytest.mark.parametrize("phrase", ["first 5", "after 30", "between 10 and 20"])
def test_bare_numbers_ask_seconds_or_minutes(phrase: str) -> None:
    with pytest.raises(TimeRangeError, match="seconds or minutes"):
        resolve_range(phrase, D)


@pytest.mark.parametrize("phrase", ["last half", "the middle", "sometime", "0:61 to 0:70"])
def test_phrases_it_cant_resolve_raise(phrase: str) -> None:
    with pytest.raises(TimeRangeError):
        resolve_range(phrase, D)


def test_time_range_error_is_a_tool_error_not_a_validation_error() -> None:
    with pytest.raises(TimeRangeError) as caught:
        resolve_range("after 2:00", D)

    assert isinstance(caught.value, ToolError)
    assert not isinstance(caught.value, ValidationError)


def test_time_range_is_frozen_and_rejects_unknown_fields() -> None:
    r = resolve_range("first half", D)
    with pytest.raises(ValidationError):
        r.start = 1.0  # type: ignore[misc]
    with pytest.raises(ValidationError):
        TimeRange(start=0, end=1, until="end")  # type: ignore[call-arg]


def test_overlapping_ranges_merge_in_the_timeline() -> None:
    log = OpLog()
    for phrase in ("first third", "between 0:20 and 0:50"):
        call_tool("cut_range", {"time_range": resolve_range(phrase, D).model_dump()}, ctx(log))

    assert log.state().cuts == [TimeRange(start=0, end=50)]


# --- resolve_time tool and the planner ------------------------------------------------------


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("middle third", TimeRange(start=30, end=60)),
        ("after 0:30", TimeRange(start=30)),
        ("between 0:10 and 0:40", TimeRange(start=10, end=40)),
    ],
)
def test_resolve_time_tool_uses_resolve_range(phrase: str, expected: TimeRange) -> None:
    assert call_tool("resolve_time", {"phrase": phrase}, ctx()).time_range == expected


def test_resolve_time_errors_name_the_tool() -> None:
    with pytest.raises(TimeRangeError, match=r"^resolve_time: .*end of the video"):
        call_tool("resolve_time", {"phrase": "after 2:00"}, ctx())


@pytest.mark.parametrize("third", ["first third", "middle third", "last third"])
def test_planner_sends_thirds_to_resolve_time(third: str) -> None:
    plan = plan_request(f"blur faces for the {third}")

    assert isinstance(plan, Plan), plan
    assert {"step_id": "s2", "tool": "resolve_time", "args": {"phrase": third}} in [
        c.model_dump() for c in plan.calls
    ]


def _run_plan(text: str, context: ToolContext) -> None:
    """Plan ``text`` and run it, filling each ``$ref`` with that step's result (like the
    helper in test_tools.py; the real executor is a follow-up)."""
    plan = plan_request(text)
    assert isinstance(plan, Plan), plan
    results: dict[str, object] = {}
    for c in plan.calls:
        args = {k: results[v["$ref"]] if as_ref(v) else v for k, v in c.args.items()}
        out = call_tool(c.tool, args, context)
        results[c.step_id] = getattr(out, "time_range", None) or getattr(out, "track_ids", None)


def test_planner_and_toolbox_cut_the_middle_third() -> None:
    log = OpLog()

    _run_plan("cut the middle third", ctx(log))

    assert log.state().cuts == [TimeRange(start=30, end=60)]


@pytest.mark.parametrize(
    "text",
    [
        "blur people after 2:00",
        "mute between 1:00 and 2:00",
        "mute 1:00 to 1:30.1",
        "reduce the noise after 1:30",
        "cut 1:00 to 2:00",
        "pixelate plates before 2:00",
    ],
)
def test_planner_ranges_past_the_end_are_refused_when_the_plan_runs(text: str) -> None:
    # The planner writes these as literal ranges (it doesn't know the duration), so the edit
    # tools check them; nothing is written.
    log = OpLog()

    with pytest.raises(TimeRangeError, match="end of the video"):
        _run_plan(text, ctx(log))

    assert len(log) == 0


def test_planner_range_ending_exactly_at_the_end_is_written() -> None:
    log = OpLog()

    _run_plan("mute between 1:00 and 1:30", ctx(log))

    assert len(log) == 1


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("mute", {"time_range": {"start": 120}}),
        ("denoise", {"time_range": {"start": 0, "end": 91}}),
        ("add_mask", {"targets": ["t1"], "effect": {"type": "blur"}, "time_range": {"start": 95}}),
        ("cut_range", {"time_range": {"start": 30, "end": 120}}),
    ],
)
def test_hand_edits_past_the_end_are_refused(tool: str, args: dict) -> None:
    log = OpLog()

    with pytest.raises(TimeRangeError, match=rf"^{tool}: .*end of the video"):
        call_tool(tool, args, ctx(log))

    assert len(log) == 0


def test_unmute_past_the_end_is_refused() -> None:
    log = OpLog()
    call_tool("mute", {}, ctx(log))

    with pytest.raises(TimeRangeError, match="end of the video"):
        call_tool("unmute", {"time_range": {"start": 120}}, ctx(log))

    assert len(log) == 1


def test_edits_without_a_known_duration_cant_be_checked_and_are_written() -> None:
    log = OpLog()

    call_tool("mute", {"time_range": {"start": 120}}, ctx(log, duration=None))

    assert len(log) == 1


def test_planner_and_resolver_agree_on_second_third() -> None:
    assert parse_time_phrase("the second third") == "second third"
    assert resolve_range("second third", D) == TimeRange(start=30, end=60)


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("during the first half", TimeRange(start=0, end=45)),
        ("for the last third", TimeRange(start=60)),
        ("everything after 0:30", TimeRange(start=30)),
        ("over 0:10 to 0:40", TimeRange(start=10, end=40)),
    ],
)
def test_resolver_ignores_the_planners_leading_words(phrase: str, expected: TimeRange) -> None:
    assert resolve_range(phrase, D) == expected


def test_resolve_time_without_a_duration_is_a_tool_error_not_a_time_question() -> None:
    with pytest.raises(ToolError, match="duration") as caught:
        call_tool("resolve_time", {"phrase": "first half"}, ctx(duration=None))

    assert not isinstance(caught.value, TimeRangeError)


# --- Round trip on a real 90 s clip ---------------------------------------------------------


def test_first_half_of_a_real_90s_clip(long_clip: Path) -> None:
    info = probe(long_clip)
    frame = 1 / float(info.fps)

    r = resolve_range("first half", info.duration)

    assert r.start == 0.0
    assert r.end == pytest.approx(45.0, abs=frame)


def test_cut_the_second_half_of_a_real_90s_clip_renders_45s(
    long_clip: Path, tmp_path: Path
) -> None:
    info = probe(long_clip)
    frame = 1 / float(info.fps)
    log = OpLog()

    _run_plan("cut the second half", ctx(log, duration=info.duration))
    result = export(long_clip, log.state(), [], tmp_path / "out.mp4")

    assert result.duration == pytest.approx(45.0, abs=frame)
    assert probe(result.path).duration == pytest.approx(45.0, abs=frame)
