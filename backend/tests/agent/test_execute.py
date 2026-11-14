"""The plan executor: refs, all-or-nothing writes, one request id. No GPU, no network, no HTTP.

The clip for the ``execute_request`` tests is encoded with PyAV, so they don't need the ffmpeg
CLI.
"""

import dataclasses
from collections.abc import Sequence
from pathlib import Path

import av
import numpy as np
import pytest
from pydantic import TypeAdapter

from vibe.agent import Execution, Plan, PlanError, ToolCall, execute_plan, execute_request
from vibe.agent import plan_request as planned
from vibe.agent.execute import UNREADABLE_ANALYSIS
from vibe.contract import AddMask, Cut, Mute, Op, OpLog, TimeRange, Track, Undo
from vibe.modules.vision import save_tracks
from vibe.storage import Project, Storage
from vibe.toolbox import TOOLS

EXAMPLES = Path(__file__).parents[2] / "src" / "vibe" / "contract" / "examples"
TRACKS = TypeAdapter(list[Track]).validate_json((EXAMPLES / "street.tracks.json").read_bytes())
PEOPLE = [t.track_id for t in TRACKS if t.label == "person"]


def plan(*calls: tuple[str, str, dict]) -> Plan:
    return Plan(calls=tuple(ToolCall(step_id=s, tool=t, args=a) for s, t, a in calls))


def from_text(text: str) -> Plan:
    result = planned(text)
    assert isinstance(result, Plan), result
    return result


def run(what: Plan | str, timeline: object, request_id: str = "r_1", **facts: object) -> Execution:
    facts = {"duration": 60.0, "tracks": TRACKS} | facts
    what = from_text(what) if isinstance(what, str) else what
    return execute_plan(what, timeline, request_id=request_id, **facts)


class RecordingTimeline:
    """A timeline in memory that records each write, and can fail the way a disk does."""

    def __init__(self, error: Exception | None = None) -> None:
        self.stored, self.error = OpLog(), error
        self.batches: list[list[Op]] = []

    def log(self) -> OpLog:
        return OpLog(self.stored.ops)

    def append_many(self, ops: Sequence[Op]) -> list[Op]:
        if self.error is not None:
            raise self.error
        self.batches.append(list(ops))
        return self.stored.append_many(ops)


# --- Refs ---------------------------------------------------------------------------------


def test_refs_are_filled_with_the_earlier_steps_results() -> None:
    log = OpLog()

    done = run("blur people for the first half", log)

    (op,) = log.ops
    assert isinstance(op, AddMask)
    assert op.targets == PEOPLE and len(PEOPLE) > 0
    assert op.time_range == TimeRange(start=0, end=30)
    assert done.ok and done.ops == (op,)


def test_step_reports_show_the_resolved_arguments_and_compact_results() -> None:
    done = run("blur people for the first half", OpLog())

    find, resolve, mask = done.steps
    assert (find.step_id, find.tool, find.ok) == ("s1", "find_objects", True)
    assert find.result == {"track_ids": PEOPLE, "count": len(PEOPLE)}
    assert resolve.result == {"time_range": {"start": 0.0, "end": 30.0}}
    assert mask.args == {
        "targets": PEOPLE,
        "effect": {"type": "blur"},
        "time_range": {"start": 0.0, "end": 30.0},
    }
    assert mask.result == {"op_ids": [done.ops[0].op_id]}


def test_an_open_ended_range_stays_open_through_a_ref() -> None:
    log = OpLog()

    done = run("mute the second half", log)

    assert done.steps[-1].args == {"time_range": {"start": 30.0}}
    assert log.ops[0].time_range == TimeRange(start=30)


def test_the_same_ref_can_be_used_by_several_steps() -> None:
    log = OpLog()
    twice = plan(
        ("s1", "resolve_time", {"phrase": "first 5 seconds"}),
        ("s2", "mute", {"time_range": {"$ref": "s1"}}),
        ("s3", "cut_range", {"time_range": {"$ref": "s1"}}),
    )

    run(twice, log)

    assert [op.time_range for op in log] == [TimeRange(start=0, end=5)] * 2


# --- All or nothing ---------------------------------------------------------------------------

MUTE_THEN_BAD_CUT = plan(
    ("s1", "mute", {"time_range": {"start": 0, "end": 5}}),
    ("s2", "cut_range", {"time_range": {"start": 100, "end": 110}}),  # past the end of 60 s
)


def test_a_failing_step_writes_nothing_and_names_the_step() -> None:
    log = OpLog()

    done = run(MUTE_THEN_BAD_CUT, log)

    assert len(log) == 0
    assert (done.ok, done.ops, done.failed_step) == (False, (), "s2")
    assert "after the end of the video" in done.error
    assert [(s.step_id, s.ok) for s in done.steps] == [("s1", True), ("s2", False)]
    assert done.steps[-1].result is None


def test_a_failing_step_leaves_a_project_file_untouched(tmp_path: Path) -> None:
    project = fake_project(tmp_path)
    run("mute 0:10 to 0:20", project, request_id="r_0")
    before = project.timeline_path.read_bytes()

    done = run(MUTE_THEN_BAD_CUT, project)

    assert not done.ok
    assert project.timeline_path.read_bytes() == before


def test_steps_after_the_failing_one_dont_run() -> None:
    done = run(
        plan(
            ("s1", "cut_range", {"time_range": {"start": 0}}),  # the whole video: refused
            ("s2", "mute", {}),
        ),
        OpLog(),
    )

    assert [s.step_id for s in done.steps] == ["s1"]
    assert "whole video" in done.error


def test_a_time_the_video_doesnt_have_is_a_refusal_not_a_crash() -> None:
    log = OpLog()

    done = run("cut the last 2 minutes", log)  # of a 60 s video

    assert (done.ok, done.failed_step, len(log)) == (False, "s1", 0)
    assert "whole video" in done.error


def test_masking_a_video_that_was_never_analysed_fails_closed() -> None:
    log = OpLog()

    done = run("blur faces and mute the audio", log, tracks=None)

    assert (done.ok, done.failed_step, len(log)) == (False, "s1", 0)
    assert "no analysis for this video" in done.error


def test_masking_a_label_the_analysis_didnt_find_writes_nothing() -> None:
    log = OpLog()

    done = run("blur faces", log, tracks=[t for t in TRACKS if t.label != "face"])

    assert (done.ok, done.failed_step, len(log)) == (False, "s2", 0)
    assert done.steps[-1].args["targets"] == []


def test_a_later_step_sees_what_an_earlier_one_did() -> None:
    # unmute refuses unless something is muted: it must see the dry run's mute.
    log = OpLog()

    done = run("mute 0:10 to 0:20 then unmute 0:12 to 0:15", log)

    assert done.ok
    assert [(m.start, m.end) for m in log.state().mutes] == [(10, 12), (15, 20)]


# --- One batch, one request id ----------------------------------------------------------------


def test_a_requests_ops_are_written_in_one_batch_with_one_request_id() -> None:
    timeline = RecordingTimeline()
    run("mute the audio", timeline, request_id="r_0")

    done = run("keep 0:10 to 0:40 and mute 0:20 to 0:30", timeline, request_id="r_1")

    assert [len(batch) for batch in timeline.batches] == [1, 3]
    written = timeline.stored.ops[1:]
    assert written == done.ops
    assert [type(op) for op in written] == [Cut, Cut, Mute]
    assert {(op.request_id, op.source) for op in written} == {("r_1", "agent")}
    assert len({op.op_id for op in timeline.stored}) == 4


def test_the_source_is_passed_on_to_every_op() -> None:
    log = OpLog()

    run("keep 0:10 to 0:40", log, source="user")

    assert {op.source for op in log} == {"user"}


def test_an_op_log_in_memory_is_written_to_directly() -> None:
    log = OpLog()
    run("mute 0:10 to 0:20", log, request_id="r_1")

    done = run("cut 0:30 to 0:40", log, request_id="r_2")

    assert [op.request_id for op in log] == ["r_1", "r_2"]
    assert done.ops == log.ops[1:]


def test_undo_runs_as_a_plan_of_its_own() -> None:
    log = OpLog()
    run("mute the audio", log, request_id="r_1")

    done = run(plan(("s1", "undo", {})), log, request_id="r_2", source="user")

    (op,) = done.ops
    assert isinstance(op, Undo) and (op.undo_request_id, op.source) == ("r_1", "user")
    assert done.steps[0].result == {"op_ids": [op.op_id], "undone_request_id": "r_1"}
    assert log.state().mutes == []


def test_nothing_to_undo_is_a_refusal() -> None:
    done = run(plan(("s1", "undo", {})), OpLog())

    assert (done.ok, done.error) == (False, "undo: nothing to undo")


# --- What isn't a refusal propagates ----------------------------------------------------------


def test_an_invalid_plan_is_a_bug_and_writes_nothing() -> None:
    log = OpLog()
    dangling = plan(
        ("s1", "mute", {}),
        ("s2", "add_mask", {"targets": {"$ref": "s9"}, "effect": {"type": "blur"}}),
    )

    with pytest.raises(PlanError, match="s9"):
        run(dangling, log)

    assert len(log) == 0


def test_a_bug_in_a_tool_propagates_and_writes_nothing() -> None:
    def broken(args: object, ctx: object) -> None:
        raise TypeError("a real bug")

    registry = TOOLS | {"cut_range": dataclasses.replace(TOOLS["cut_range"], run=broken)}
    log = OpLog()

    with pytest.raises(TypeError, match="a real bug"):
        run("mute 0:10 to 0:20 and cut 0:30 to 0:40", log, registry=registry)

    assert len(log) == 0


def test_a_write_that_fails_propagates_instead_of_looking_like_a_refusal() -> None:
    timeline = RecordingTimeline(OSError("disk full"))

    with pytest.raises(OSError, match="disk full"):
        run("mute the audio", timeline)

    assert len(timeline.stored) == 0


def test_a_refusal_comes_back_as_a_result_not_an_exception() -> None:
    done = run("unmute the audio", OpLog())  # nothing is muted: the tool raises ToolError

    assert not done.ok and "isn't muted" in done.error


# --- execute_request: a project on disk, with its video's facts -------------------------------

CLIP_SECONDS, CLIP_FPS = 3, 10


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("clips") / "clip.mp4"
    with av.open(str(path), "w") as out:
        stream = out.add_stream("mpeg4", rate=CLIP_FPS)
        stream.width, stream.height, stream.pix_fmt = 32, 32, "yuv420p"
        for i in range(CLIP_SECONDS * CLIP_FPS):
            image = np.full((32, 32, 3), i * 8, dtype=np.uint8)
            out.mux(stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")))
        out.mux(stream.encode())
    return path


def fake_project(tmp_path: Path) -> Project:
    """A project whose "video" is just bytes: enough when the caller gives the duration."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really a video")
    return Storage(tmp_path / "data").create_project(video)


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "data")


def face(track_id: str = "trk_1") -> Track:
    keyframes = [{"t": 0, "box": (0.1, 0.1, 0.3, 0.3), "conf": 0.9, "state": "detected"}]
    return Track.model_validate(
        {"track_id": track_id, "label": "face", "source": "detector", "keyframes": keyframes}
    )


def test_the_duration_comes_from_the_stored_video(storage: Storage, clip: Path) -> None:
    project = storage.create_project(clip)

    done = execute_request(storage, project, from_text("mute the first half"), "r_1")

    assert done.ok
    (mute,) = project.state().mutes
    assert (mute.start, mute.end) == (0, pytest.approx(CLIP_SECONDS / 2))
    assert (mute.request_id, project.log().ops[0].source) == ("r_1", "agent")


def test_a_time_past_the_stored_videos_end_is_refused(storage: Storage, clip: Path) -> None:
    project = storage.create_project(clip)

    done = execute_request(storage, project, from_text("mute 0:10 to 0:20"), "r_1")

    assert not done.ok and "end of the video" in done.error
    assert len(project.log()) == 0


def test_blur_without_an_analysis_fails_closed(storage: Storage, clip: Path) -> None:
    project = storage.create_project(clip)
    project.save_user_tracks([face("usr_1").model_copy(update={"source": "user"})])

    done = execute_request(storage, project, from_text("blur faces"), "r_1")

    # A drawn box alone must not make "blur faces" look done.
    assert not done.ok and "no analysis for this video" in done.error
    assert len(project.log()) == 0


def test_blur_uses_the_cached_tracks_and_the_drawn_ones(storage: Storage, clip: Path) -> None:
    project = storage.create_project(clip)
    save_tracks(storage, project.info.video.sha256, "k-aaaa1111", [face()])
    project.save_user_tracks([face("usr_1").model_copy(update={"source": "user"})])

    done = execute_request(storage, project, from_text("blur faces"), "r_1")

    assert done.ok
    assert set(project.state().masks) == {"trk_aaaa1111_1", "usr_1"}


def test_an_ambiguous_analysis_fails_closed_with_its_own_reason(
    storage: Storage, clip: Path
) -> None:
    project = storage.create_project(clip)
    sha = project.info.video.sha256
    save_tracks(storage, sha, "k-aaaa1111", [face()])
    save_tracks(storage, sha, "k-bbbb2222", [face()])  # two sets, no default

    done = execute_request(storage, project, from_text("blur faces"), "r_1")

    assert (done.ok, done.error) == (False, UNREADABLE_ANALYSIS)
    assert str(storage.root) not in done.error
    assert len(project.log()) == 0


def test_an_ambiguous_analysis_doesnt_block_edits_that_need_no_tracks(
    storage: Storage, clip: Path
) -> None:
    project = storage.create_project(clip)
    sha = project.info.video.sha256
    save_tracks(storage, sha, "k-aaaa1111", [face()])
    save_tracks(storage, sha, "k-bbbb2222", [face()])

    done = execute_request(storage, project, from_text("mute the audio"), "r_1")

    assert done.ok and len(project.state().mutes) == 1


def test_a_missing_video_propagates(storage: Storage, clip: Path) -> None:
    project = storage.create_project(clip)
    storage.video_path(project.info.video).unlink()

    with pytest.raises(FileNotFoundError):
        execute_request(storage, project, from_text("mute the audio"), "r_1")

    assert len(project.log()) == 0
