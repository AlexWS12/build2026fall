"""F-06: the toolbox's tools. No GPU, no network, no video files."""

from pathlib import Path

import pytest
from pydantic import TypeAdapter

from vibe.contract import AddMask, Cut, Mute, OpLog, TimeRange, Track
from vibe.storage import Storage
from vibe.toolbox import ToolContext, ToolError, call_tool

EXAMPLES = Path(__file__).parents[2] / "src" / "vibe" / "contract" / "examples"
TRACKS = TypeAdapter(list[Track]).validate_json((EXAMPLES / "street.tracks.json").read_bytes())


def ctx(log: OpLog | None = None, duration: float | None = 60.0) -> ToolContext:
    return ToolContext(
        request_id="req_1", source="agent", log=log, duration=duration, tracks=TRACKS
    )


# --- find_objects (stub: filters the tracks it's given) -----------------------------------


def test_find_objects_returns_the_tracks_with_that_label() -> None:
    expected = [t.track_id for t in TRACKS if t.label == "person"]

    result = call_tool("find_objects", {"label": "person"}, ctx())

    assert result.track_ids == expected
    assert result.count == len(expected)


def test_find_objects_with_no_match_returns_an_empty_list() -> None:
    result = call_tool("find_objects", {"label": "giraffe"}, ctx())

    assert result.track_ids == []
    assert result.count == 0


# --- resolve_time (real: arithmetic on the duration) --------------------------------------


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("first half", TimeRange(start=0, end=30)),
        ("second half", TimeRange(start=30)),
        ("last 10 seconds", TimeRange(start=50)),
        ("last 0:10", TimeRange(start=50)),
        ("first 5 seconds", TimeRange(start=0, end=5)),
    ],
)
def test_resolve_time_uses_the_video_duration(phrase: str, expected: TimeRange) -> None:
    assert call_tool("resolve_time", {"phrase": phrase}, ctx()).time_range == expected


def test_resolve_time_without_a_duration_is_an_error() -> None:
    with pytest.raises(ToolError, match="duration"):
        call_tool("resolve_time", {"phrase": "first half"}, ctx(duration=None))


@pytest.mark.parametrize("duration", [0.0, -5.0, float("nan"), float("inf")])
def test_resolve_time_rejects_a_duration_that_isnt_a_positive_number(duration: float) -> None:
    with pytest.raises(ToolError, match="duration"):
        call_tool("resolve_time", {"phrase": "first half"}, ctx(duration=duration))


@pytest.mark.parametrize("phrase", ["first 0 seconds", "last 0 seconds", "last 0:00"])
def test_resolve_time_rejects_empty_ranges(phrase: str) -> None:
    with pytest.raises(ToolError, match="empty"):
        call_tool("resolve_time", {"phrase": phrase}, ctx())


@pytest.mark.parametrize(
    "phrase", ["last 2 minutes", "first 2 minutes", "last 60 seconds", "first 1:00"]
)
def test_resolve_time_refuses_a_span_as_long_as_the_video(phrase: str) -> None:
    # On a 60 s video these would cover everything: "cut the last 2 minutes" must not
    # silently cut the whole clip.
    with pytest.raises(ToolError, match="whole video"):
        call_tool("resolve_time", {"phrase": phrase}, ctx(duration=60.0))


@pytest.mark.parametrize("phrase", ["sometime", "last 10", "the middle"])
def test_resolve_time_rejects_phrases_it_cant_resolve(phrase: str) -> None:
    with pytest.raises(ToolError):
        call_tool("resolve_time", {"phrase": phrase}, ctx())


# --- Edit tools write contract ops --------------------------------------------------------


def test_add_mask_appends_an_add_mask_op() -> None:
    log = OpLog()

    result = call_tool(
        "add_mask",
        {"targets": ["trk_1"], "effect": {"type": "blur"}, "time_range": {"start": 0, "end": 5}},
        ctx(log),
    )

    (op,) = log.ops
    assert isinstance(op, AddMask)
    assert result.op_ids == [op.op_id]
    assert (op.request_id, op.source, op.targets) == ("req_1", "agent", ["trk_1"])
    assert op.time_range == TimeRange(start=0, end=5)


def test_cut_range_appends_a_cut_op() -> None:
    log = OpLog()

    call_tool("cut_range", {"time_range": {"start": 1, "end": 2}}, ctx(log))

    assert isinstance(log.ops[0], Cut)
    assert log.state().cuts == [TimeRange(start=1, end=2)]


def test_mute_without_a_time_range_mutes_the_whole_clip() -> None:
    log = OpLog()

    call_tool("mute", {}, ctx(log))

    (op,) = log.ops
    assert isinstance(op, Mute)
    assert op.time_range is None and op.speaker is None


def test_edit_tools_work_on_a_storage_project(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really a video")
    project = Storage(tmp_path / "data").create_project(video)

    call_tool("mute", {"time_range": {"start": 10, "end": 20}}, ctx(project))

    assert len(project.log()) == 1


def test_edit_tool_without_a_timeline_is_an_error() -> None:
    with pytest.raises(ToolError, match="timeline"):
        call_tool("mute", {}, ctx(log=None))


# --- Errors are loud ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("mute", {"volume": 0}),
        ("cut_range", {}),
        ("cut_range", {"time_range": {"start": 5, "end": 1}}),
        ("add_mask", {"targets": [], "effect": {"type": "blur"}}),
        ("add_mask", {"targets": ["trk_1"], "effect": {"type": "sparkles"}}),
        ("find_objects", {"label": ""}),
    ],
)
def test_bad_arguments_raise_and_write_nothing(tool: str, args: dict) -> None:
    log = OpLog()

    with pytest.raises(ToolError, match=tool):
        call_tool(tool, args, ctx(log))

    assert len(log) == 0


def test_unknown_tool_is_an_error() -> None:
    with pytest.raises(ToolError, match="unknown tool 'explode'"):
        call_tool("explode", {}, ctx())


def test_an_op_the_contract_refuses_is_an_error() -> None:
    # req_1 continues after req_2: a request's ops must be contiguous in the log.
    log = OpLog(
        [Mute(request_id="req_1", source="agent"), Mute(request_id="req_2", source="agent")]
    )

    with pytest.raises(ToolError, match="contiguous"):
        call_tool("cut_range", {"time_range": {"start": 0, "end": 1}}, ctx(log))

    assert len(log) == 2


def test_add_mask_drops_repeated_targets() -> None:
    log = OpLog()

    call_tool(
        "add_mask", {"targets": ["trk_2", "trk_1", "trk_2"], "effect": {"type": "blur"}}, ctx(log)
    )

    assert log.ops[0].targets == ["trk_2", "trk_1"]
