"""The chat's plain-words account of a request: only what the steps that ran say happened."""

import pytest

from vibe.agent import Plan, ToolCall, execute_plan, plan_request
from vibe.agent.describe import clock, describe, explain_failure
from vibe.contract import OpLog, Track


def track(track_id: str, label: str) -> Track:
    keyframes = [{"t": 0, "box": (0.1, 0.1, 0.3, 0.3), "conf": 0.9, "state": "detected"}]
    return Track.model_validate(
        {"track_id": track_id, "label": label, "source": "detector", "keyframes": keyframes}
    )


TRACKS = [track("trk_1", "person"), track("trk_2", "person"), track("trk_3", "face")]
"""Two people, one face, no license plate."""


def said(text: str, log: OpLog | None = None, **facts: object) -> tuple[bool, str]:
    """Plan and run ``text`` on a 90 s video; ``(applied, what the chat would say)``."""
    facts = {"duration": 90.0, "tracks": TRACKS} | facts
    plan = plan_request(text)
    assert isinstance(plan, Plan), plan
    done = execute_plan(plan, OpLog() if log is None else log, request_id="r_1", **facts)
    return done.ok, describe(plan, done) if done.ok else explain_failure(plan, done)


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "0:00"), (5, "0:05"), (59.96, "1:00"), (83.5, "1:23.5"), (600, "10:00"), (3725, "62:05")],
)
def test_clock_is_minutes_and_seconds(seconds: float, text: str) -> None:
    assert clock(seconds) == text


@pytest.mark.parametrize(
    ("request_text", "reply"),
    [
        ("mute the first 5 seconds", "Muted 0:00–0:05."),
        ("mute the audio", "Muted the whole video."),
        ("mute after 1:00", "Muted 1:00 to the end."),
        ("cut 0:10 to 0:20", "Cut 0:10–0:20."),
        ("cut the last third", "Cut 1:00 to the end."),
        ("keep only 0:10 to 0:40", "Kept only 0:10–0:40; the rest is cut."),
        ("reduce the noise", "Added noise reduction to the whole video."),
        ("denoise 0:10 to 0:20", "Added noise reduction to 0:10–0:20."),
        ("blur people", "Blurred 2 people (the whole video)."),
        ("pixelate faces from 0:10 to 0:20", "Pixelated 1 face (0:10–0:20)."),
        ("black box everyone for the first half", "Covered 2 people with a black box (0:00–0:45)."),
        ("mute 0:10 to 0:20 and cut 0:30 to 0:40", "Muted 0:10–0:20. Cut 0:30–0:40."),
    ],
)  # fmt: skip
def test_each_edit_is_one_plain_sentence(request_text: str, reply: str) -> None:
    assert said(request_text) == (True, reply)


def test_unmute_says_who_is_still_muted() -> None:
    log = OpLog()
    for speaker in (None, "spk_2"):
        args = {"time_range": {"start": 10, "end": 20}, "speaker": speaker}
        mute = Plan(calls=(ToolCall(step_id="s1", tool="mute", args=args),))
        done = execute_plan(mute, log, request_id=f"r_{speaker}", duration=90.0)
        assert describe(mute, done) == f"Muted{' speaker spk_2' if speaker else ''} 0:10–0:20."

    assert said("unmute 0:12 to 0:15", log) == (
        True,
        "Unmuted 0:12–0:15. Still muted there: spk_2.",
    )


def test_undo_and_redo_say_so() -> None:
    log = OpLog()
    said("mute the audio", log)

    assert said("undo", log) == (True, "Undid the last edit.")
    assert said("redo", log) == (True, "Brought back the last undone edit.")


@pytest.mark.parametrize(
    ("request_text", "facts", "reply"),
    [
        (
            "blur faces",
            {"tracks": None},
            "I couldn't do that: no analysis for this video, so I can't tell where any 'face' "
            "is. Nothing was changed.",
        ),
        (
            "blur license plates",
            {},
            "I found no license plates in this video, so there was nothing to hide. Nothing "
            "was changed.",
        ),
        (
            "cut the last 2 minutes",
            {},
            "I couldn't do that: 'last 2 minutes' is as long as the whole video (90 seconds). "
            "Nothing was changed.",
        ),
        ("undo", {}, "I couldn't do that: nothing to undo. Nothing was changed."),
    ],
)
def test_a_refusal_gives_the_reason_without_the_tool_name(
    request_text: str, facts: dict, reply: str
) -> None:
    assert said(request_text, **facts) == (False, reply)


def test_a_reply_only_covers_the_edit_steps_that_ran() -> None:
    plan = plan_request("blur people for the first half")
    done = execute_plan(plan, OpLog(), request_id="r_1", duration=90.0, tracks=TRACKS)

    assert [s.tool for s in done.steps] == ["find_objects", "resolve_time", "add_mask"]
    assert describe(plan, done).count(".") == 1  # one sentence: the query steps aren't edits
