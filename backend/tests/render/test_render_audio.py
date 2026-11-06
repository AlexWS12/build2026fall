"""Audio: mutes silence exactly their samples, cuts remove exactly theirs."""

import numpy as np
import pytest

from vibe.contract import AddAudioFilter, Cut, Mute, Op, OpLog, TimelineState, TimeRange
from vibe.render import UnsupportedEdit
from vibe.render.audio import apply_audio_edits, render_audio

RATE = 10  # 10 samples per second keeps the sample numbers easy to read


def state_of(*ops: Op) -> TimelineState:
    log = OpLog()
    for i, op in enumerate(ops):
        log.append(op.model_copy(update={"request_id": f"r_{i}"}))
    return log.state()


def cut(start: float, end: float | None) -> Cut:
    return Cut(request_id="r", source="agent", time_range=TimeRange(start=start, end=end))


def mute(start: float, end: float | None, speaker: str | None = None) -> Mute:
    return Mute(
        request_id="r", source="agent", speaker=speaker, time_range=TimeRange(start=start, end=end)
    )


SAMPLES = np.arange(1, 51, dtype=np.float32)  # 5 s; sample i holds i + 1, so 0 means silenced


def test_nothing_to_do_returns_a_copy() -> None:
    out = render_audio(SAMPLES, RATE, state_of())
    assert np.array_equal(out, SAMPLES)
    assert out is not SAMPLES


def test_mute_silences_exactly_its_samples() -> None:
    out = render_audio(SAMPLES, RATE, state_of(mute(1.0, 2.5)))
    assert np.flatnonzero(out == 0).tolist() == list(range(10, 25))
    assert len(out) == len(SAMPLES)


def test_cut_removes_exactly_its_samples() -> None:
    out = render_audio(SAMPLES, RATE, state_of(cut(1.0, 2.0)))
    assert out.tolist() == SAMPLES[:10].tolist() + SAMPLES[20:].tolist()


def test_cut_to_the_end() -> None:
    out = render_audio(SAMPLES, RATE, state_of(cut(4.0, None)))
    assert out.tolist() == SAMPLES[:40].tolist()


def test_mute_then_cut_line_up_in_source_time() -> None:
    # Mute 1–3 s, cut 2–4 s: what's left of the mute is 1–2 s, right before the cut point.
    out = render_audio(SAMPLES, RATE, state_of(mute(1.0, 3.0), cut(2.0, 4.0)))
    assert len(out) == 30
    assert np.flatnonzero(out == 0).tolist() == list(range(10, 20))
    assert out[20:].tolist() == SAMPLES[40:].tolist()


def test_spans_past_the_end_are_clamped() -> None:
    out = render_audio(SAMPLES, RATE, state_of(mute(4.5, 99.0), cut(3.0, 4.0)))
    assert len(out) == 40
    assert np.flatnonzero(out == 0).tolist() == list(range(35, 40))


def test_stereo_mutes_and_cuts_every_channel() -> None:
    stereo = np.stack([SAMPLES, -SAMPLES], axis=1)
    out = render_audio(stereo, RATE, state_of(mute(0.0, 1.0), cut(4.0, None)))
    assert out.shape == (40, 2)
    assert not out[:10].any()
    assert out[10:].all()


@pytest.mark.parametrize("size", [1, 3, 7, 10, 50])
def test_chunk_by_chunk_gives_the_same_as_all_at_once(size: int) -> None:
    state = state_of(mute(1.0, 3.0), cut(2.0, 4.0), mute(4.5, None))
    chunks = [
        apply_audio_edits(SAMPLES[i : i + size], RATE, state, first_sample=i)
        for i in range(0, len(SAMPLES), size)
    ]
    assert np.concatenate(chunks).tolist() == render_audio(SAMPLES, RATE, state).tolist()


def test_a_chunk_entirely_inside_a_cut_comes_out_empty() -> None:
    state = state_of(cut(1.0, 4.0))
    assert len(apply_audio_edits(SAMPLES[15:25], RATE, state, first_sample=15)) == 0


def test_input_samples_are_not_changed() -> None:
    samples = SAMPLES.copy()
    render_audio(samples, RATE, state_of(mute(0.0, None)))
    assert np.array_equal(samples, SAMPLES)


def test_speaker_mute_is_refused() -> None:
    with pytest.raises(UnsupportedEdit, match="F-32"):
        render_audio(SAMPLES, RATE, state_of(mute(0.0, 1.0, speaker="spk_1")))


def test_audio_filter_is_refused() -> None:
    denoise = AddAudioFilter(request_id="r", source="agent", filter="denoise")
    with pytest.raises(UnsupportedEdit, match="F-33"):
        render_audio(SAMPLES, RATE, state_of(denoise))
