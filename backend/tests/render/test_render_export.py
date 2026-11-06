"""The export path, and the F-04 exit check: a generated clip rendered with a blur, a cut and a
mute from an example timeline (``render_session.jsonl``), checked frame by frame and by ear."""

import os
from pathlib import Path

import numpy as np
import pytest
from pydantic import TypeAdapter
from render_clip import FPS, FRAMES, HEIGHT, TONE_RMS, WIDTH

from vibe.contract import (
    AddMask,
    AddRule,
    Cut,
    Effect,
    Mute,
    Op,
    OpLog,
    Rule,
    TimelineState,
    TimeRange,
    Track,
)
from vibe.render import RenderError, RenderResult, UnsupportedEdit, export
from vibe.video import VideoReader, extract_audio, probe

HERE = Path(__file__).parent
SESSION = OpLog.from_jsonl((HERE / "render_session.jsonl").read_text(encoding="utf-8")).state()
TRACKS = TypeAdapter(list[Track]).validate_json((HERE / "render_session.tracks.json").read_bytes())

# The session: blur trk_1 (the middle of the frame) over 0–2 s, cut 1.0–1.5 s, mute 2.0–2.5 s.
CUT = 0.5
INNER = (slice(80, 160), slice(100, 220))  # well inside trk_1's box (rows, columns)
CORNER = (slice(0, 40), slice(0, 50))  # outside the box and its padding
LOSSY = 6.0  # mean per-pixel difference H.264 at CRF 18 may add to an untouched region
AAC_SLACK = 2048 / 48000  # encoder delay and the last partial frame


def frame_at(path: Path, t: float) -> np.ndarray:
    with VideoReader(path) as reader:
        return reader.read_at(t).image.astype(np.float64)


def detail(image: np.ndarray) -> float:
    """Mean difference between neighbouring pixels: high for the test pattern, low once blurred."""
    return float(np.abs(np.diff(image, axis=0)).mean() + np.abs(np.diff(image, axis=1)).mean())


def rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def state_of(*ops: Op) -> TimelineState:
    log = OpLog()
    for i, op in enumerate(ops):
        log.append(op.model_copy(update={"request_id": f"r_{i}"}))
    return log.state()


@pytest.fixture(scope="module")
def rendered(pattern_clip: Path, tmp_path_factory: pytest.TempPathFactory) -> RenderResult:
    return export(pattern_clip, SESSION, TRACKS, tmp_path_factory.mktemp("export") / "out.mp4")


# --- The exit check ------------------------------------------------------------------------


def test_exit_check_cut_shortens_the_output(rendered: RenderResult) -> None:
    kept = FRAMES - round(CUT * FPS)
    assert rendered.frames == kept
    assert rendered.duration == pytest.approx(3.0 - CUT)
    info = probe(rendered.path)
    assert info.duration == pytest.approx(3.0 - CUT, abs=1 / FPS)
    with VideoReader(rendered.path) as reader:
        assert len(reader) == kept
    assert (info.width, info.height) == (WIDTH, HEIGHT)


def test_exit_check_masked_region_changed(rendered: RenderResult, pattern_clip: Path) -> None:
    out, src = frame_at(rendered.path, 0.5), frame_at(pattern_clip, 0.5)
    assert np.abs(out[INNER] - src[INNER]).mean() > 30  # changed a lot (about 90 measured)...
    assert detail(out[INNER]) < 0.5 * detail(src[INNER])  # ...and smoothed, not just shifted
    assert np.abs(out[CORNER] - src[CORNER]).mean() < LOSSY  # the rest is untouched


def test_exit_check_mask_ends_on_time_after_the_cut(
    rendered: RenderResult, pattern_clip: Path
) -> None:
    # Output 1.6 s is source 2.1 s (the cut removed 0.5 s), after the blur ended at 2.0 s. If
    # frames weren't dropped for the cut, it would show source 1.6 s, which is blurred.
    out, src = frame_at(rendered.path, 1.6), frame_at(pattern_clip, 2.1)
    assert np.abs(out[INNER] - src[INNER]).mean() < LOSSY


def test_exit_check_muted_audio_is_silent(rendered: RenderResult) -> None:
    samples, rate = extract_audio(rendered.path)
    assert len(samples) / rate == pytest.approx(3.0 - CUT, abs=AAC_SLACK)

    def span(start: float, end: float) -> np.ndarray:
        return samples[round(start * rate) : round(end * rate)]

    # Source 2.0–2.5 s is muted; after the 0.5 s cut it plays at output 1.5–2.0 s. A margin
    # each side keeps the AAC encoder's smearing at the edges out of the check.
    assert rms(span(1.6, 1.9)) < 0.005
    assert rms(span(0.2, 0.8)) == pytest.approx(TONE_RMS, rel=0.2)
    assert rms(span(2.1, 2.4)) == pytest.approx(TONE_RMS, rel=0.2)


def test_exit_check_reports_no_issues(rendered: RenderResult) -> None:
    assert rendered.issues == ()
    assert not list(rendered.path.parent.glob(".*"))  # no temporary file left behind


# --- The rest of the export path -----------------------------------------------------------


def test_clip_without_audio_exports_without_audio(
    silent_pattern_clip: Path, tmp_path: Path
) -> None:
    result = export(silent_pattern_clip, SESSION, TRACKS, tmp_path / "out.mp4")
    assert probe(result.path).audio is None
    assert result.frames == FRAMES - round(CUT * FPS)


@pytest.mark.parametrize(
    ("clip", "channels"),
    [("mono_clip", 1), ("unnamed_layout_clip", 2), ("surround_clip", 2), ("pattern_clip", 2)],
)
def test_audio_comes_out_mono_or_stereo(
    clip: str, channels: int, request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    result = export(request.getfixturevalue(clip), state_of(), [], tmp_path / "out.mp4")
    assert probe(result.path).audio.channels == channels
    assert rms(extract_audio(result.path)[0]) > 0.01  # and the sound survived


def test_odd_size_is_cropped_to_even(odd_size_clip: Path, tmp_path: Path) -> None:
    result = export(odd_size_clip, state_of(), [], tmp_path / "out.mp4")
    info = probe(result.path)
    assert (info.width, info.height) == (WIDTH, HEIGHT)
    assert result.frames == FPS


def test_mask_without_a_track_hides_the_whole_frame(pattern_clip: Path, tmp_path: Path) -> None:
    effect = Effect(type="box")
    state = state_of(AddMask(request_id="r", source="agent", targets=["trk_gone"], effect=effect))
    result = export(pattern_clip, state, [], tmp_path / "out.mp4")
    [issue] = result.issues
    assert issue.track_id == "trk_gone"
    assert frame_at(result.path, 1.0).mean() < 5  # black, give or take the encoder


def test_speaker_mute_is_refused_and_writes_nothing(pattern_clip: Path, tmp_path: Path) -> None:
    state = state_of(Mute(request_id="r", source="agent", speaker="spk_1"))
    with pytest.raises(UnsupportedEdit, match="F-32"):
        export(pattern_clip, state, [], tmp_path / "out.mp4")
    assert list(tmp_path.iterdir()) == []


def test_standing_rule_is_refused_and_writes_nothing(pattern_clip: Path, tmp_path: Path) -> None:
    rule = Rule(rule_id="rule_1", category="person", effect=Effect(type="blur"))
    state = state_of(AddRule(request_id="r", source="agent", rule=rule))
    with pytest.raises(UnsupportedEdit, match="F-51"):
        export(pattern_clip, state, [], tmp_path / "out.mp4")
    assert list(tmp_path.iterdir()) == []


def test_cutting_everything_is_an_error_and_leaves_no_file(
    pattern_clip: Path, tmp_path: Path
) -> None:
    state = state_of(Cut(request_id="r", source="agent", time_range=TimeRange(start=0)))
    with pytest.raises(RenderError, match="nothing to render"):
        export(pattern_clip, state, [], tmp_path / "out.mp4")
    assert list(tmp_path.iterdir()) == []


def test_exports_are_mp4(pattern_clip: Path, tmp_path: Path) -> None:
    with pytest.raises(RenderError, match=".mp4"):
        export(pattern_clip, SESSION, TRACKS, tmp_path / "out.webm")


@pytest.mark.parametrize("spelling", ["same path", "relative path", "hard link"])
def test_export_refuses_to_overwrite_its_source(
    spelling: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"the only copy of someone's video")
    out = clip
    if spelling == "relative path":
        monkeypatch.chdir(tmp_path)
        out = Path("clip.mp4")
    elif spelling == "hard link":
        out = tmp_path / "link.mp4"
        os.link(clip, out)
    with pytest.raises(RenderError, match="overwrite"):
        export(clip, SESSION, TRACKS, out)
    assert clip.read_bytes() == b"the only copy of someone's video"


def test_missing_source_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        export(tmp_path / "nope.mp4", SESSION, TRACKS, tmp_path / "out.mp4")
    assert list(tmp_path.iterdir()) == []
