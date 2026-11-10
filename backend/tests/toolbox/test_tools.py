"""F-06: the toolbox's tools. No GPU, no network, no video files."""

import dataclasses
import os
import sys
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from vibe.agent import Plan, plan_request
from vibe.agent.plan import as_ref
from vibe.contract import AddAudioFilter, AddMask, Cut, Mute, OpLog, TimeRange, Track, Unmute
from vibe.storage import Storage
from vibe.toolbox import TOOLS, ToolContext, ToolError, call_tool
from vibe.toolbox.tools import FindObjectsResult

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


def test_unmute_lifts_part_of_an_earlier_mute() -> None:
    log = OpLog()
    call_tool("mute", {"time_range": {"start": 10, "end": 20}}, ctx(log))

    call_tool("unmute", {"time_range": {"start": 12, "end": 15}}, ctx(log))

    op = log.ops[-1]
    assert isinstance(op, Unmute) and op.speaker is None
    assert [(m.start, m.end) for m in log.state().mutes] == [(10, 12), (15, 20)]


def test_unmute_without_a_time_range_lifts_every_all_audio_mute() -> None:
    log = OpLog()
    call_tool("mute", {"time_range": {"start": 1, "end": 2}}, ctx(log))
    call_tool("mute", {"speaker": "spk_1"}, ctx(log))

    result = call_tool("unmute", {}, ctx(log))

    assert [m.speaker for m in log.state().mutes] == ["spk_1"]  # a speaker's mute stays
    assert result.still_muted == ["spk_1"]  # and the agent is told so


def test_unmute_says_nothing_is_still_muted_when_all_the_sound_is_back() -> None:
    log = OpLog()
    call_tool("mute", {"time_range": {"start": 10, "end": 20}}, ctx(log))
    call_tool("mute", {"speaker": "spk_1", "time_range": {"start": 30, "end": 40}}, ctx(log))

    result = call_tool("unmute", {"time_range": {"start": 10, "end": 20}}, ctx(log))

    assert result.still_muted == []  # spk_1's mute is outside the range


@pytest.mark.parametrize(
    "args",
    [
        {"time_range": {"start": 30, "end": 40}},  # after the mute
        {"time_range": {"start": 5, "end": 10}},  # right before it: the end is excluded
        {"speaker": "spk_2"},  # nobody muted spk_2
    ],
)
def test_unmute_with_nothing_to_lift_is_an_error(args: dict) -> None:
    log = OpLog()
    call_tool("mute", {"time_range": {"start": 10, "end": 20}}, ctx(log))

    with pytest.raises(ToolError, match="isn't muted"):
        call_tool("unmute", args, ctx(log))
    assert len(log.ops) == 1  # nothing written


def test_unmute_all_audio_where_only_a_speaker_is_muted_names_the_speaker() -> None:
    log = OpLog()
    call_tool("mute", {"speaker": "spk_1", "time_range": {"start": 10, "end": 20}}, ctx(log))

    with pytest.raises(ToolError, match="only spk_1 muted there"):
        call_tool("unmute", {"time_range": {"start": 12, "end": 15}}, ctx(log))


def test_unmute_on_an_empty_timeline_is_an_error() -> None:
    with pytest.raises(ToolError, match="all audio isn't muted anywhere"):
        call_tool("unmute", {}, ctx(OpLog()))


def test_denoise_appends_an_audio_filter_op() -> None:
    log = OpLog()

    call_tool("denoise", {"time_range": {"start": 5}, "strength": 0.5}, ctx(log))

    (op,) = log.ops
    assert isinstance(op, AddAudioFilter)
    assert (op.filter, op.strength, op.time_range) == ("denoise", 0.5, TimeRange(start=5))
    (span,) = log.state().audio_filters
    assert (span.filter, span.start, span.end, span.strength) == ("denoise", 5, None, 0.5)


def test_denoise_without_arguments_denoises_the_whole_clip_at_the_default_strength() -> None:
    log = OpLog()

    call_tool("denoise", {}, ctx(log))

    (op,) = log.ops
    assert op.time_range is None and op.strength is None


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
        ("denoise", {"strength": 1.5}),
        ("denoise", {"strength": -0.1}),
        ("denoise", {"filter": "reverb"}),
        ("cut_range", {}),
        ("cut_range", {"time_range": {"start": 5, "end": 1}}),
        ("keep_range", {}),
        ("keep_range", {"time_range": {"start": float("inf"), "end": float("inf")}}),
        ("keep_range", {"time_range": {"start": 5, "end": 1}}),
        ("keep_range", {"time_range": {"start": 1, "end": 5}, "speaker": "a"}),
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


# --- Fail closed when the video hasn't been analysed --------------------------------------


def test_find_objects_without_analysis_is_an_error_not_no_matches() -> None:
    no_analysis = ToolContext(request_id="req_1", source="agent")

    with pytest.raises(ToolError, match="no analysis"):
        call_tool("find_objects", {"label": "face"}, no_analysis)


def test_find_objects_after_analysis_that_found_nothing_is_an_empty_list() -> None:
    analysed = ToolContext(request_id="req_1", source="agent", tracks=[])

    assert call_tool("find_objects", {"label": "face"}, analysed).count == 0


# --- Never cut the whole video --------------------------------------------------------------


@pytest.mark.parametrize(
    "time_range", [{"start": 0}, {"start": 0, "end": 60}, {"start": 0, "end": 120}]
)
def test_cut_range_refuses_to_cut_the_whole_video(time_range: dict) -> None:
    log = OpLog()

    with pytest.raises(ToolError, match="whole video"):
        call_tool("cut_range", {"time_range": time_range}, ctx(log, duration=60.0))

    assert len(log) == 0


def test_cut_range_without_a_known_duration_cant_check_and_writes() -> None:
    log = OpLog()

    call_tool("cut_range", {"time_range": {"start": 0, "end": 120}}, ctx(log, duration=None))

    assert len(log) == 1


def _run_plan(text: str, context: ToolContext) -> None:
    """Plan ``text`` and run its calls in order, filling each ``$ref`` with that step's
    result. Just enough for these tests; the real executor is a follow-up."""
    plan = plan_request(text)
    assert isinstance(plan, Plan), plan
    results: dict[str, object] = {}
    for c in plan.calls:
        args = {k: results[v["$ref"]] if as_ref(v) else v for k, v in c.args.items()}
        out = call_tool(c.tool, args, context)
        results[c.step_id] = getattr(out, "time_range", None) or getattr(out, "track_ids", None)


@pytest.mark.parametrize(
    "text", ["cut the first 2 minutes", "cut the last 2 minutes", "cut before 2:00"]
)
def test_planner_and_toolbox_never_cut_a_1_minute_video_entirely(text: str) -> None:
    log = OpLog()

    with pytest.raises(ToolError, match="whole video"):
        _run_plan(text, ctx(log, duration=60.0))

    assert len(log) == 0


def test_planner_and_toolbox_cut_part_of_the_video() -> None:
    log = OpLog()

    _run_plan("cut the first 5 seconds", ctx(log, duration=60.0))

    assert log.state().cuts == [TimeRange(start=0, end=5)]


@pytest.mark.parametrize(
    "text",
    [
        "just keep 0:10 to 0:20",
        "mute 0:12 to 0:14 and only keep 0:10 to 0:20",
        "keep 0:10 to 0:20 but mute 0:12 to 0:14",
    ],
)
def test_planner_and_toolbox_keep_a_range_from_a_chat_request(text: str) -> None:
    log = OpLog()

    _run_plan(text, ctx(log, duration=60.0))

    assert log.state().cuts == [TimeRange(start=0, end=10), TimeRange(start=20)]


def test_planner_and_toolbox_keep_the_tail_of_a_clip_shown_rounded_up() -> None:
    log = OpLog()

    _run_plan("keep 0:50 to 1:00", ctx(log, duration=59.97))

    assert log.state().cuts == [TimeRange(start=0, end=50)]


def test_planner_and_toolbox_refuse_a_keep_an_earlier_cut_empties() -> None:
    log = OpLog()
    _run_plan("cut 0:00 to 0:30", ctx(log, duration=60.0))

    with pytest.raises(ToolError, match="leave nothing"):
        _run_plan("keep only 0:10 to 0:20", dataclasses.replace(ctx(log), request_id="req_2"))

    assert log.state().cuts == [TimeRange(start=0, end=30)]


# --- keep_range: cuts around a range -------------------------------------------------------


def _keep(log, start: float, end: float | None, duration: float | None = 60.0):
    return call_tool("keep_range", {"time_range": {"start": start, "end": end}}, ctx(log, duration))


def test_keep_a_middle_range_cuts_before_and_after_it() -> None:
    log = OpLog()

    result = _keep(log, 10, 20)

    assert log.state().cuts == [TimeRange(start=0, end=10), TimeRange(start=20)]
    assert len(result.op_ids) == 2


def test_keep_writes_the_head_cut_before_the_tail_cut() -> None:
    log = OpLog()

    _keep(log, 10, 20)

    ops = list(log.ops)
    assert [(o.time_range.start, o.time_range.end) for o in ops] == [(0, 10), (20, None)]
    assert all(isinstance(o, Cut) for o in ops)
    assert {o.request_id for o in ops} == {"req_1"}


def test_keep_from_the_start_cuts_only_the_tail() -> None:
    log = OpLog()

    result = _keep(log, 0, 20)

    assert log.state().cuts == [TimeRange(start=20)]
    assert len(result.op_ids) == 1


@pytest.mark.parametrize("end", [None, 60])
def test_keep_to_the_end_cuts_only_the_head(end: float | None) -> None:
    log = OpLog()

    result = _keep(log, 10, end)

    assert log.state().cuts == [TimeRange(start=0, end=10)]
    assert len(result.op_ids) == 1


def test_keep_after_an_earlier_cut_keeps_only_what_both_leave() -> None:
    log = OpLog()
    call_tool("cut_range", {"time_range": {"start": 15, "end": 18}}, ctx(log))

    _keep(log, 10, 20)

    assert log.state().cuts == [
        TimeRange(start=0, end=10),
        TimeRange(start=15, end=18),
        TimeRange(start=20),
    ]


@pytest.mark.parametrize(
    ("start", "end"),
    [(0, None), (0, 60), (-0.0, 60)],
)
def test_keeping_the_whole_video_is_refused(start: float, end: float | None) -> None:
    log = OpLog()

    with pytest.raises(ToolError, match="whole video"):
        _keep(log, start, end)

    assert len(log) == 0


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (60, None, "starts at or after the end"),
        (75, 80, "starts at or after the end"),
        (10, 5 * 60, "past the end"),
        (10, 61, "past the end"),
        (0, 90, "past the end"),
    ],
)
def test_keeping_beyond_the_video_is_refused_not_clamped(
    start: float, end: float | None, message: str
) -> None:
    log = OpLog()

    with pytest.raises(ToolError, match=message):
        _keep(log, start, end)

    assert len(log) == 0


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (float("inf"), None),
        (0, float("nan")),
        (0, float("inf")),
    ],
)
def test_keep_refuses_times_that_are_not_finite(start: float, end: float | None) -> None:
    log = OpLog()

    with pytest.raises(ToolError, match="keep_range: .*finite"):
        _keep(log, start, end)

    assert len(log) == 0


@pytest.mark.parametrize("duration", [None, 0, -1, float("nan"), float("inf")])
def test_keep_needs_a_real_video_duration(duration: float | None) -> None:
    log = OpLog()

    with pytest.raises(ToolError, match="keep_range: .*duration"):
        _keep(log, 10, 20, duration=duration)

    assert len(log) == 0


def test_keep_needs_a_timeline_to_write_to() -> None:
    with pytest.raises(ToolError, match="timeline"):
        call_tool("keep_range", {"time_range": {"start": 1, "end": 2}}, ctx(log=None))


class _RecordingSink:
    """An in-memory timeline that records each write, and can fail the way a disk does."""

    def __init__(self, error: Exception | None = None) -> None:
        self.log, self.error = OpLog(), error
        self.batches: list[list] = []

    def append(self, op):
        return self.append_many([op])[0]

    def append_many(self, ops):
        if self.error is not None:
            raise self.error
        self.batches.append(list(ops))
        return self.log.append_many(ops)

    def state(self):
        return self.log.state()


def test_keep_writes_both_cuts_in_one_batch() -> None:
    sink = _RecordingSink()

    _keep(sink, 10, 20)

    assert [len(b) for b in sink.batches] == [2]


def test_keep_whose_write_fails_writes_nothing_and_says_why() -> None:
    sink = _RecordingSink(OSError("disk full"))

    with pytest.raises(ToolError, match="keep_range: can't read or write the timeline: disk full"):
        _keep(sink, 10, 20)

    assert len(sink.log) == 0


def test_a_bug_in_the_timeline_is_not_dressed_up_as_a_tool_error() -> None:
    sink = _RecordingSink(TypeError("a real bug"))

    with pytest.raises(TypeError, match="a real bug"):
        _keep(sink, 10, 20)


def test_keep_works_on_a_project_on_disk(tmp_path: Path) -> None:
    project = _project(tmp_path)

    result = _keep(project, 10, 20)

    assert len(result.op_ids) == 2
    assert project.state().cuts == [TimeRange(start=0, end=10), TimeRange(start=20)]


def test_keep_the_contract_refuses_writes_neither_cut_to_a_project(tmp_path: Path) -> None:
    """Another request's op between two of this request's ops breaks the contract; with one
    batch, the timeline is never left holding only the head cut."""
    project = _project(tmp_path)
    call_tool("mute", {}, ctx(project))  # req_1
    call_tool("mute", {}, dataclasses.replace(ctx(project), request_id="req_2"))
    before = project.timeline_path.read_bytes()

    with pytest.raises(ToolError, match="keep_range: couldn't apply the edit"):
        _keep(project, 10, 20)  # req_1 again, after req_2: not contiguous

    assert project.timeline_path.read_bytes() == before


# --- keep_range after earlier edits -----------------------------------------------------------


@pytest.mark.parametrize(
    ("earlier", "keep"),
    [
        ((0, 30), (10, 20)),  # the user read 0:10-0:20 off the preview, already cut
        ((5, None), (10, 20)),
        ((10, 20), (10, 20)),
        ((10, 15), (10, 15)),
    ],
)
def test_keep_that_would_leave_nothing_after_earlier_cuts_is_refused(
    earlier: tuple[float, float | None], keep: tuple[float, float]
) -> None:
    log = OpLog()
    start, end = earlier
    call_tool("cut_range", {"time_range": {"start": start, "end": end}}, ctx(log))
    before = len(log)

    with pytest.raises(ToolError, match="leave nothing"):
        _keep(log, *keep)

    assert len(log) == before


def test_keep_that_overlaps_an_earlier_cut_but_leaves_something_goes_ahead() -> None:
    log = OpLog()
    call_tool("cut_range", {"time_range": {"start": 0, "end": 15}}, ctx(log))

    _keep(log, 10, 20)

    assert log.state().cuts == [TimeRange(start=0, end=15), TimeRange(start=20)]


def test_keep_on_an_unreadable_timeline_is_a_tool_error(tmp_path: Path) -> None:
    project = _project(tmp_path)
    project.timeline_path.write_text('{"schema_version": 99}\n')

    with pytest.raises(ToolError, match="keep_range: .*schema_version"):
        _keep(project, 10, 20)


# --- keep_range at the end of the clip --------------------------------------------------------


@pytest.mark.parametrize("end", [59.97, 60.0, 60.05])
def test_keep_to_just_past_the_end_keeps_to_the_end(end: float) -> None:
    """A 59.97 s clip shows as 1:00, so "keep 0:50 to 1:00" means the tail."""
    log = OpLog()

    _keep(log, 50, end, duration=59.97)

    assert log.state().cuts == [TimeRange(start=0, end=50)]


@pytest.mark.parametrize(
    ("duration", "start", "end"),
    [(0.08, 0.01, 0.05), (0.15, 0.0, 0.1), (5.0, 1.0, 4.97)],
)
def test_on_a_short_video_the_end_tolerance_never_drops_a_real_tail(
    duration: float, start: float, end: float
) -> None:
    log = OpLog()

    _keep(log, start, end, duration=duration)

    assert TimeRange(start=end) in log.state().cuts


@pytest.mark.parametrize("end", [59.8, 59.95])
def test_keep_ending_before_the_end_cuts_the_tail_however_short(end: float) -> None:
    log = OpLog()

    _keep(log, 10, end, duration=59.97)

    assert log.state().cuts == [TimeRange(start=0, end=10), TimeRange(start=end)]


def test_keep_well_past_the_end_names_the_length_and_how_to_say_it() -> None:
    log = OpLog()

    with pytest.raises(ToolError, match=r"59\.97s.*to the end"):
        _keep(log, 50, 61, duration=59.97)

    assert len(log) == 0


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions, not root"
)
def test_a_keep_on_a_read_only_project_timeline_writes_nothing_and_says_so(tmp_path: Path) -> None:
    project = _project(tmp_path)
    project.timeline_path.chmod(0o444)
    before = project.timeline_path.read_bytes()

    try:
        with pytest.raises(ToolError, match="can't read or write the timeline"):
            _keep(project, 10, 20)
    finally:
        project.timeline_path.chmod(0o644)

    assert project.timeline_path.read_bytes() == before


# --- Errors from a project's timeline file ---------------------------------------------------


def _project(tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really a video")
    return Storage(tmp_path / "data").create_project(video)


def test_missing_project_timeline_is_a_tool_error(tmp_path: Path) -> None:
    project = _project(tmp_path)
    project.timeline_path.unlink()

    with pytest.raises(ToolError, match="can't read or write the timeline"):
        call_tool("mute", {}, ctx(project))


def test_corrupt_project_timeline_is_a_tool_error_that_doesnt_blame_the_edit(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    project.timeline_path.write_text('{"schema_version": 99}\n')

    with pytest.raises(ToolError, match="schema_version") as caught:
        call_tool("mute", {}, ctx(project))

    assert "refused" not in str(caught.value)


def test_a_tool_returning_the_wrong_type_is_a_bug() -> None:
    broken = {
        "mute": dataclasses.replace(
            TOOLS["mute"], run=lambda args, c: FindObjectsResult(track_ids=[], count=0)
        )
    }

    with pytest.raises(TypeError, match="returned FindObjectsResult"):
        call_tool("mute", {}, ctx(OpLog()), registry=broken)
