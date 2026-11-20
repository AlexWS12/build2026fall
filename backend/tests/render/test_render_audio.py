"""Audio: mutes silence exactly their samples, cuts remove exactly theirs, denoise changes only
its spans."""

import numpy as np
import pytest

from vibe.contract import AddAudioFilter, Cut, Mute, Op, OpLog, TimelineState, TimeRange
from vibe.modules.denoise import learn_noise
from vibe.render import UnsupportedEdit
from vibe.render.audio import FADE_SECONDS, DenoiseMix, apply_audio_edits, render_audio

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


# --- Denoise (F-33b) -------------------------------------------------------------------

NOISY_RATE = 16000


def denoise_op(
    start: float = 0.0, end: float | None = None, strength: float | None = None
) -> AddAudioFilter:
    return AddAudioFilter(
        request_id="r",
        source="agent",
        filter="denoise",
        strength=strength,
        time_range=TimeRange(start=start, end=end),
    )


def noisy_tone(seconds: float = 4.0) -> tuple[np.ndarray, np.ndarray]:
    """Stereo: a 440 Hz tone on for half of every second, plus hiss; and the "tone off" mask."""
    t = np.arange(round(seconds * NOISY_RATE)) / NOISY_RATE
    on = (t % 1.0) < 0.5
    tone = 0.3 * np.sin(2 * np.pi * 440 * t) * on
    hiss = 0.03 * np.random.default_rng(0).standard_normal((len(t), 2))
    return (tone[:, None] + hiss).astype(np.float32), ~on


def level(x: np.ndarray) -> float:
    return float(10 * np.log10(np.mean(np.square(x, dtype=np.float64)) + 1e-20))


def test_denoise_turns_the_hiss_down_inside_its_span_only() -> None:
    noisy, pauses = noisy_tone()
    out = render_audio(noisy, NOISY_RATE, state_of(denoise_op(2.0, None)))

    t = np.arange(len(noisy)) / NOISY_RATE
    after, before = pauses & (t >= 2.05), t < 1.95
    assert level(out[after]) < level(noisy[after]) - 6
    np.testing.assert_array_equal(out[before], noisy[before])  # untouched, sample for sample
    assert out.shape == noisy.shape


def test_denoise_eases_in_at_its_edges() -> None:
    """The gated sound is blended in over FADE_SECONDS before the span, so the edge doesn't
    click, and the span itself gets the full effect."""
    noisy, _ = noisy_tone()
    out = render_audio(noisy, NOISY_RATE, state_of(denoise_op(2.0, None)))

    edge, fade = 2 * NOISY_RATE, round(FADE_SECONDS * NOISY_RATE)
    change = np.abs(out - noisy).max(axis=1)
    assert change[: edge - fade].max() == 0
    assert 0 < change[edge - fade // 2] and change[edge - fade + 1 : edge].max() > 0


def test_half_strength_is_between_none_and_full() -> None:
    noisy, pauses = noisy_tone()
    half = render_audio(noisy, NOISY_RATE, state_of(denoise_op(strength=0.5)))
    full = render_audio(noisy, NOISY_RATE, state_of(denoise_op()))

    assert level(full[pauses]) < level(half[pauses]) < level(noisy[pauses])


def test_where_spans_overlap_the_strongest_wins() -> None:
    noisy, _ = noisy_tone()
    both = render_audio(
        noisy, NOISY_RATE, state_of(denoise_op(strength=0.2), denoise_op(strength=1.0))
    )
    full = render_audio(noisy, NOISY_RATE, state_of(denoise_op(strength=1.0)))

    np.testing.assert_allclose(both, full, atol=1e-6)


def test_denoise_runs_before_mutes_and_cuts() -> None:
    noisy, _ = noisy_tone()
    out = render_audio(noisy, NOISY_RATE, state_of(denoise_op(), mute(1.0, 2.0), cut(3.0, None)))

    assert len(out) == 3 * NOISY_RATE
    assert not out[NOISY_RATE : 2 * NOISY_RATE].any()  # the mute is still exactly silent


@pytest.mark.parametrize("size", [1601, 4800])
def test_chunked_like_export_gives_the_whole_clip_result(size: int) -> None:
    """What export does: DenoiseMix a chunk at a time, then mutes and cuts on what comes out,
    numbered by output sample. Must match the whole clip at once, edges and fades included."""
    noisy, _ = noisy_tone()
    state = state_of(denoise_op(0.5, 3.2, strength=0.7), mute(1.0, 1.25), cut(2.0, 2.5))
    mix = DenoiseMix(learn_noise([noisy], NOISY_RATE), state, noisy.dtype)

    outputs = [mix.push(noisy[i : i + size]) for i in range(0, len(noisy), size)]
    parts, first = [], 0
    for samples in [*outputs, mix.finish()]:
        parts.append(apply_audio_edits(samples, NOISY_RATE, state, first_sample=first))
        first += len(samples)

    np.testing.assert_allclose(
        np.concatenate(parts), render_audio(noisy, NOISY_RATE, state), atol=1e-6
    )


# --- Fades at the edges (RATE above is too low for a fade, so these use 1 kHz) ------------

FADE_RATE = 1000  # FADE_SECONDS (10 ms) is 10 samples
TONE = np.ones(3000, dtype=np.float32)  # 3 s of a constant level, so the gain is what's left


def test_mute_fades_out_before_and_in_after_and_stays_silent_inside() -> None:
    out = render_audio(TONE, FADE_RATE, state_of(mute(1.0, 2.0)))
    assert not out[1000:2000].any()  # the muted span is exactly silent
    fade_out, fade_in = out[990:1000], out[2000:2010]
    assert np.all(np.diff(fade_out) < 0) and 0 < fade_out.min() and fade_out.max() < 1
    assert np.all(np.diff(fade_in) > 0) and 0 < fade_in.min() and fade_in.max() < 1
    assert np.all(out[:990] == 1) and np.all(out[2010:] == 1)  # the rest is untouched


def test_cut_join_fades_on_both_sides_and_keeps_the_same_samples() -> None:
    out = render_audio(TONE, FADE_RATE, state_of(cut(1.0, 2.0)))
    assert len(out) == 2000  # the fade doesn't change what's kept
    join = out[990:1010]  # the last 10 kept samples before the cut, then the first 10 after
    assert join.max() < 1 and join.min() > 0
    assert np.all(out[:990] == 1) and np.all(out[1010:] == 1)


def test_no_fade_at_the_ends_of_the_clip() -> None:
    out = render_audio(TONE, FADE_RATE, state_of(mute(0.0, 1.0), mute(2.5, None)))
    assert not out[:1000].any() and not out[2500:].any()
    assert np.all(out[1010:2490] == 1)


def test_back_to_back_mutes_dont_fade_in_between() -> None:
    out = render_audio(TONE, FADE_RATE, state_of(mute(1.0, 1.5), mute(1.5, 2.0)))
    assert not out[1000:2000].any()


def test_fades_are_the_same_chunk_by_chunk() -> None:
    state = state_of(mute(0.5, 1.0), cut(1.2, 1.7), mute(2.4, None))
    whole = render_audio(TONE, FADE_RATE, state)
    for size in (1, 7, 100, 1003):
        chunks = [
            apply_audio_edits(TONE[i : i + size], FADE_RATE, state, first_sample=i)
            for i in range(0, len(TONE), size)
        ]
        assert np.array_equal(np.concatenate(chunks), whole), size


def test_stereo_fades_every_channel_the_same() -> None:
    stereo = np.stack([TONE, -TONE], axis=1)
    out = render_audio(stereo, FADE_RATE, state_of(mute(1.0, 2.0)))
    assert np.array_equal(out[:, 0], -out[:, 1])
    assert out.dtype == np.float32


def test_fades_of_close_edits_take_the_lower_gain() -> None:
    # 5 samples between two mutes, less than one fade: the gap dips but never reaches 1.
    out = render_audio(TONE, FADE_RATE, state_of(mute(1.0, 1.5), mute(1.505, 2.0)))
    gap = out[1500:1505]
    expected = np.minimum(np.arange(1, 6), np.arange(5, 0, -1)) / 11
    assert np.allclose(gap, expected)
