"""F-13: ``find_by_description``. Unsure tracks are always returned (privacy fails closed)."""

from pathlib import Path

import pytest
from pydantic import TypeAdapter

from vibe.agent import Plan, plan_request
from vibe.contract import AddMask, OpLog, Track
from vibe.toolbox import DescriberError, ToolContext, ToolError, call_tool

EXAMPLES = Path(__file__).parents[2] / "src" / "vibe" / "contract" / "examples"
STREET = TypeAdapter(list[Track]).validate_json((EXAMPLES / "street.tracks.json").read_bytes())


def person(n: int) -> Track:
    return STREET[0].model_copy(update={"track_id": f"trk_p{n}", "label": "person"})


TRACKS = [person(1), person(2), person(3), STREET[0].model_copy(update={"label": "face"})]
PEOPLE = ["trk_p1", "trk_p2", "trk_p3"]
ARGS = {"label": "person", "description": "in red"}


class FakeDescriber:
    def __init__(self, verdicts: dict[str, str] | None = None, error: Exception | None = None):
        self.verdicts, self.error = verdicts or {}, error
        self.asked: list[tuple[list[str], str]] = []

    def ask(self, tracks, description):
        self.asked.append(([t.track_id for t in tracks], description))
        if self.error is not None:
            raise self.error
        return self.verdicts


def ctx(describer: FakeDescriber | None, log: OpLog | None = None, tracks=TRACKS) -> ToolContext:
    return ToolContext(
        request_id="req_1", source="agent", log=log, tracks=tracks, describer=describer
    )


def test_it_only_asks_about_tracks_with_the_label() -> None:
    describer = FakeDescriber()

    call_tool("find_by_description", ARGS, ctx(describer))

    assert describer.asked == [(PEOPLE, "in red")]


def test_it_returns_matches_and_unsure_tracks() -> None:
    describer = FakeDescriber({"trk_p1": "yes", "trk_p2": "no"})  # p3 gets no answer: unsure

    result = call_tool("find_by_description", ARGS, ctx(describer))

    assert result.track_ids == ["trk_p1", "trk_p3"]
    assert result.unsure == ["trk_p3"]
    assert result.count == 2


def test_exclude_leaves_out_only_the_sure_matches() -> None:
    describer = FakeDescriber({"trk_p1": "yes", "trk_p2": "unsure", "trk_p3": "no"})

    result = call_tool("find_by_description", {**ARGS, "exclude": True}, ctx(describer))

    assert result.track_ids == ["trk_p2", "trk_p3"]
    assert result.unsure == ["trk_p2"]


def test_an_answer_that_isnt_a_verdict_counts_as_unsure() -> None:
    describer = FakeDescriber(dict.fromkeys(PEOPLE, "maybe"))

    for exclude in (False, True):
        result = call_tool("find_by_description", {**ARGS, "exclude": exclude}, ctx(describer))
        assert result.track_ids == PEOPLE
        assert result.unsure == PEOPLE


def test_no_track_with_the_label_asks_nothing() -> None:
    describer = FakeDescriber()

    result = call_tool(
        "find_by_description", {"label": "giraffe", "description": "in red"}, ctx(describer)
    )

    assert result.track_ids == [] and result.count == 0
    assert describer.asked == []


def test_it_fails_without_a_describer_or_an_analysis() -> None:
    with pytest.raises(ToolError, match="nothing here can look at the video"):
        call_tool("find_by_description", ARGS, ctx(None))
    with pytest.raises(ToolError, match="no analysis"):
        call_tool("find_by_description", ARGS, ctx(FakeDescriber(), tracks=None))


def test_a_describer_that_cant_look_is_an_error_not_an_empty_result() -> None:
    describer = FakeDescriber(error=DescriberError("no judge"))

    with pytest.raises(ToolError, match="couldn't check 'in red': no judge"):
        call_tool("find_by_description", ARGS, ctx(describer))


def test_a_blank_description_is_a_bad_argument() -> None:
    with pytest.raises(ToolError, match="bad arguments"):
        call_tool("find_by_description", {"label": "person", "description": ""}, ctx(None))


def test_a_described_request_runs_from_the_template_to_a_mask() -> None:
    plan = plan_request("blur everyone except the person in red")
    assert isinstance(plan, Plan)
    log = OpLog()
    context = ctx(FakeDescriber({"trk_p1": "yes", "trk_p2": "no", "trk_p3": "no"}), log)

    found = call_tool(plan.calls[0].tool, plan.calls[0].args, context)
    call_tool("add_mask", {**plan.calls[1].args, "targets": found.track_ids}, context)

    [op] = log.ops
    assert isinstance(op, AddMask)
    assert op.targets == ["trk_p2", "trk_p3"]
