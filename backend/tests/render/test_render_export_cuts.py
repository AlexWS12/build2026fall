"""F-20: exports with cuts that ``test_render_export.py`` doesn't cover: a cut that runs to the
end, a cut from the start, and a cut that starts between frames, which keeps picture and sound in
step to within one frame. The mapping itself is tested in ``tests/test_contract.py`` and the audio
side in ``test_render_audio.py``."""

from pathlib import Path

import av
import numpy as np
import pytest
from render_clip import FPS, SECONDS

from vibe.agent import Plan, plan_request
from vibe.agent.plan import as_ref
from vibe.contract import Cut, Op, OpLog, TimelineState, TimeRange
from vibe.render import RenderResult, export
from vibe.toolbox import ToolContext, call_tool
from vibe.video import VideoReader, extract_audio

LOSSY = 6.0  # mean per-pixel difference H.264 at CRF 18 may add (as in test_render_export.py)
AAC_SLACK = 2048 / 48000  # encoder delay and the last partial frame
# The pattern's neighbouring frames differ by about 4.5 (less than LOSSY), so the content checks
# below catch a kept range that is off by roughly half a second or more. Frame-level accuracy
# rests on the frame counts and the stream start times.


def state_of(*ops: Op) -> TimelineState:
    log = OpLog()
    for i, op in enumerate(ops):
        log.append(op.model_copy(update={"request_id": f"r_{i}"}))
    return log.state()


def cut(start: float, end: float | None) -> Cut:
    return Cut(request_id="r", source="user", time_range=TimeRange(start=start, end=end))


def render(source: Path, tmp_path: Path, *ops: Op) -> RenderResult:
    return export(source, state_of(*ops), [], tmp_path / "out.mp4")


def frame_at(path: Path, t: float) -> np.ndarray:
    with VideoReader(path) as reader:
        return reader.read_at(t).image.astype(np.float64)


def stream_starts(path: Path) -> tuple[float, float]:
    """Where the video and the audio stream start, in seconds."""
    with av.open(str(path)) as container:
        video, audio = container.streams.video[0], container.streams.audio[0]
        assert video.start_time is not None and audio.start_time is not None
        return (
            float(video.start_time * video.time_base),
            float(audio.start_time * audio.time_base),
        )


def audio_seconds(path: Path) -> float:
    samples, rate = extract_audio(path)
    return len(samples) / rate


def test_cut_to_the_end_keeps_what_comes_before(pattern_clip: Path, tmp_path: Path) -> None:
    result = render(pattern_clip, tmp_path, cut(2.0, None))

    assert result.frames == 2 * FPS
    assert result.duration == pytest.approx(2.0)
    assert audio_seconds(result.path) == pytest.approx(2.0, abs=AAC_SLACK)
    # The last frame kept is the one just before the cut.
    last = frame_at(result.path, 2.0 - 1 / FPS)
    assert np.abs(last - frame_at(pattern_clip, 2.0 - 1 / FPS)).mean() < LOSSY


def test_cut_from_the_start_begins_at_the_first_kept_frame(
    pattern_clip: Path, tmp_path: Path
) -> None:
    result = render(pattern_clip, tmp_path, cut(0.0, 1.0))

    assert result.frames == (SECONDS - 1) * FPS
    assert result.duration == pytest.approx(SECONDS - 1.0)
    assert audio_seconds(result.path) == pytest.approx(SECONDS - 1.0, abs=AAC_SLACK)
    assert np.abs(frame_at(result.path, 0.0) - frame_at(pattern_clip, 1.0)).mean() < LOSSY
    video_start, audio_start = stream_starts(result.path)
    assert video_start == pytest.approx(0.0, abs=1e-6)
    assert audio_start == pytest.approx(0.0, abs=1e-6)


def test_cut_that_starts_between_frames_keeps_picture_and_sound_within_a_frame(
    pattern_clip: Path, tmp_path: Path
) -> None:
    # Source frames are 1/30 s apart, so 1.016 s falls inside the frame at 1.0 s, which is cut.
    # The first kept frame is the next one, at 1.0333 s, and lands at 0.0173 s in the output
    # (``export.py``: ``source_to_output(frame.time)``). Sound is cut exactly at 1.016 s, so it
    # starts 17 ms before the picture. That sub-frame lead-in is by design, since the picture
    # can't be cut between frames, and it must stay under one frame.
    result = render(pattern_clip, tmp_path, cut(0.0, 1.016), cut(2.0, None))

    assert result.frames == 29
    video_start, audio_start = stream_starts(result.path)
    assert abs(video_start - audio_start) <= 1 / FPS
    assert (
        np.abs(frame_at(result.path, video_start) - frame_at(pattern_clip, 1.0 + 1 / FPS)).mean()
        < LOSSY
    )
    assert audio_seconds(result.path) == pytest.approx(2.0 - 1.016, abs=AAC_SLACK)


# --- F-20 exit check: a request, planned, run through the toolbox and exported ---------------


def export_request(text: str, source: Path, tmp_path: Path) -> RenderResult:
    """Plan ``text``, run its calls in order (filling each ``$ref`` with that step's result) and
    export the timeline they wrote. The loop is test glue, like ``_run_plan`` in
    ``tests/toolbox/test_tools.py``; the real plan executor is a separate card."""
    plan = plan_request(text)
    assert isinstance(plan, Plan), plan
    log = OpLog()
    context = ToolContext(request_id="req_1", source="agent", log=log, duration=float(SECONDS))
    results: dict[str, object] = {}
    for c in plan.calls:
        args = {k: results[v["$ref"]] if as_ref(v) else v for k, v in c.args.items()}
        out = call_tool(c.tool, args, context)
        results[c.step_id] = getattr(out, "time_range", None)
    return export(source, log.state(), [], tmp_path / "out.mp4")


def test_exit_check_keep_a_range_exports_only_that_range(
    pattern_clip: Path, tmp_path: Path
) -> None:
    result = export_request("keep only 0:01 to 0:02", pattern_clip, tmp_path)

    assert result.frames == FPS
    assert result.duration == pytest.approx(1.0)
    assert audio_seconds(result.path) == pytest.approx(1.0, abs=AAC_SLACK)
    assert np.abs(frame_at(result.path, 0.0) - frame_at(pattern_clip, 1.0)).mean() < LOSSY
    last = frame_at(result.path, 1.0 - 1 / FPS)
    assert np.abs(last - frame_at(pattern_clip, 2.0 - 1 / FPS)).mean() < LOSSY


def test_exit_check_keep_the_first_half_resolves_the_range_from_the_duration(
    pattern_clip: Path, tmp_path: Path
) -> None:
    result = export_request("keep the first half", pattern_clip, tmp_path)

    assert result.frames == round(SECONDS / 2 * FPS)
    assert result.duration == pytest.approx(SECONDS / 2)
    assert audio_seconds(result.path) == pytest.approx(SECONDS / 2, abs=AAC_SLACK)
    assert np.abs(frame_at(result.path, 0.0) - frame_at(pattern_clip, 0.0)).mean() < LOSSY
