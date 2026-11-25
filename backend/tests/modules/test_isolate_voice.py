"""F-34: voice isolation. The stub backend only: no GPU, no model, no video files.

The test sound stands in for a voice recorded with finger taps and a computer fan: a gliding
harmonic tone (voiced speech) with pauses, short broadband clicks (taps), and low steady noise
plus a hum (the fan). These are correctness checks on synthetic sound, not a quality benchmark.
"""

import numpy as np
import pytest

from vibe.modules.denoise import denoise, learn_noise
from vibe.modules.isolate_voice import (
    CLICK_MARGIN,
    IsolateVoiceError,
    IsolateVoiceState,
    VoiceIsolator,
    check_backend,
    click_gain,
    isolate_voice,
    isolate_voice_chunk,
)

RATE = 16000
SECONDS = 4.0


def db(x: np.ndarray) -> float:
    return float(10 * np.log10(np.mean(np.square(x, dtype=np.float64)) + 1e-20))


def recording(rate: int = RATE) -> dict[str, np.ndarray]:
    """The parts of the test sound, and masks for where the voice and the taps are."""
    rng = np.random.default_rng(0)
    n = round(SECONDS * rate)
    t = np.arange(n) / rate
    pitch = 150 * (1 + 0.05 * np.sin(2 * np.pi * 3 * t))
    phase = 2 * np.pi * np.cumsum(pitch) / rate
    speaking = (t % 1.0) < 0.6
    voice = 0.08 * speaking * sum(np.sin(k * phase) / k for k in range(1, 15))
    fan = np.convolve(rng.normal(0, 0.02, n), np.ones(8) / 8, "same")
    fan += 0.01 * np.sin(2 * np.pi * 120 * t)
    taps = np.zeros(n)
    tapping = np.zeros(n, dtype=bool)
    length = round(0.006 * rate)
    for start in (np.arange(0.3, SECONDS - 0.1, 0.37) * rate).astype(int):
        decay = np.exp(-np.arange(length) / (0.0015 * rate))
        taps[start : start + length] += rng.normal(0, 0.3, length) * decay
        tapping[start : start + round(0.01 * rate)] = True
    mixed = (voice + fan + taps).astype(np.float32)
    return {"mixed": mixed, "voice": voice, "speaking": speaking, "tapping": tapping}


@pytest.mark.parametrize("rate", [16000, 44100, 48000])
def test_taps_and_fan_are_turned_down_and_the_voice_is_kept(rate: int) -> None:
    sound = recording(rate)
    mixed, voice, speaking, tapping = (sound[k] for k in ("mixed", "voice", "speaking", "tapping"))

    out = isolate_voice(mixed, rate, backend="stub")

    taps_alone, pause = tapping & ~speaking, ~speaking & ~tapping
    under_voice = tapping & speaking
    assert db(out[taps_alone]) < db(mixed[taps_alone]) - 12
    assert db((out - voice)[under_voice]) < db((mixed - voice)[under_voice]) - 6
    assert db(out[pause]) < db(mixed[pause]) - 6  # the fan
    speech = speaking & ~tapping
    assert db(out[speech]) > db(voice[speech]) - 3  # the voice isn't turned down


def test_it_turns_taps_down_where_denoise_does_not() -> None:
    sound = recording()
    taps_alone = sound["tapping"] & ~sound["speaking"]

    isolated = isolate_voice(sound["mixed"], RATE, backend="stub")
    denoised = denoise(sound["mixed"], RATE, backend="stub")

    assert db(denoised[taps_alone]) > db(sound["mixed"][taps_alone]) - 3
    assert db(isolated[taps_alone]) < db(denoised[taps_alone]) - 10


def test_a_steady_tone_without_clicks_comes_through_nearly_unchanged() -> None:
    """The click gate leaves lasting sound alone. The tone has pauses with some hiss in them:
    with no pauses, or digital silence in them, the noise gate (F-33) would learn the tone
    itself as the noise, and so would this."""
    t = np.arange(round(4 * RATE)) / RATE
    on = (t % 1.0) < 0.5
    tone = (0.3 * np.sin(2 * np.pi * 440 * t) * on).astype(np.float32)
    hiss = (0.03 * np.random.default_rng(0).standard_normal(len(t))).astype(np.float32)

    out = isolate_voice(tone + hiss, RATE, backend="stub")

    inside = on & ((t % 1.0) > 0.05) & ((t % 1.0) < 0.45)  # away from the tone's edges
    assert db((out - tone)[inside]) < db(tone[inside]) - 20


def test_output_has_the_input_shape_dtype_and_length() -> None:
    mixed = recording()["mixed"][: round(1.234 * RATE)]
    stereo = np.stack([mixed, mixed[::-1]], axis=1)

    out = isolate_voice(stereo, RATE, backend="stub")

    assert out.shape == stereo.shape and out.dtype == np.float32
    assert out is not stereo
    np.testing.assert_allclose(out[:, 0], isolate_voice(mixed, RATE, backend="stub"), atol=1e-6)


def test_float16_stays_float16() -> None:
    mixed = recording()["mixed"].astype(np.float16)

    assert isolate_voice(mixed, RATE, backend="stub").dtype == np.float16


def test_strength_zero_and_tiny_clips_come_back_unchanged() -> None:
    mixed = recording()["mixed"]

    np.testing.assert_array_equal(isolate_voice(mixed, RATE, 0.0, backend="stub"), mixed)
    np.testing.assert_array_equal(isolate_voice(mixed[:100], RATE, backend="stub"), mixed[:100])


def test_half_strength_is_between_none_and_full() -> None:
    sound = recording()
    taps = sound["tapping"] & ~sound["speaking"]

    half = isolate_voice(sound["mixed"], RATE, 0.5, backend="stub")
    full = isolate_voice(sound["mixed"], RATE, 1.0, backend="stub")

    assert db(full[taps]) < db(half[taps]) < db(sound["mixed"][taps])


@pytest.mark.parametrize(
    "bad",
    [
        {"samples": np.zeros(RATE, dtype=np.int16)},
        {"strength": 1.5},
        {"sample_rate": 0},
        {"samples": np.zeros((2, 2, 2), dtype=np.float32)},
    ],
)
def test_bad_arguments_are_rejected(bad: dict) -> None:
    args = {"samples": np.zeros(RATE, dtype=np.float32), "sample_rate": RATE, "strength": None}
    args.update(bad)

    with pytest.raises(ValueError):
        isolate_voice(**args, backend="stub")


@pytest.mark.parametrize("backend", ["local", "remote"])
def test_backends_that_arent_built_raise_instead_of_passing_noise_through(backend: str) -> None:
    with pytest.raises(IsolateVoiceError, match="isn't built yet"):
        isolate_voice(recording()["mixed"], RATE, backend=backend)  # type: ignore[arg-type]
    with pytest.raises(IsolateVoiceError):
        check_backend(backend)  # type: ignore[arg-type]


def test_click_gain_only_touches_bins_much_more_broadband_than_lasting() -> None:
    lasting = np.array([1.0, 1.0, 1.0, 1.0])
    broad = np.array([0.5, CLICK_MARGIN, 2 * CLICK_MARGIN, 1e6])

    gain = click_gain(lasting, broad)

    assert gain[0] == gain[1] == 1
    assert gain[2] == pytest.approx(0.25)
    assert gain[3] == pytest.approx(10 ** (-24 / 20))  # the floor


# --- Streaming: what the renderer uses ----------------------------------------------------


@pytest.mark.parametrize("sizes", [[1, 7, 100], [4097], [30000], [RATE * 10]])
def test_any_chunk_sizes_give_the_whole_clip_result(sizes: list[int]) -> None:
    """Even a first chunk shorter than the click gate's look-ahead."""
    mixed = recording()["mixed"]
    stereo = np.stack([mixed, mixed * 0.5], axis=1)
    whole = isolate_voice(stereo, RATE, backend="stub")
    isolator = VoiceIsolator(learn_noise([stereo], RATE))

    parts, at, i = [], 0, 0
    while at < len(stereo):
        size = sizes[i % len(sizes)]
        parts.append(isolator.push(stereo[at : at + size]))
        at, i = at + size, i + 1
    parts.append(isolator.finish())
    out = np.concatenate(parts)

    assert out.shape == stereo.shape
    np.testing.assert_allclose(out, whole, atol=1e-6)


def test_the_isolator_checks_its_chunks() -> None:
    isolator = VoiceIsolator(learn_noise([np.zeros((RATE, 2), dtype=np.float32)], RATE))

    with pytest.raises(ValueError, match="channels"):
        isolator.push(np.zeros(100, dtype=np.float32))


# --- Live mode ----------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["stub", "local", "remote"])
def test_per_chunk_isolation_has_its_shape_but_isnt_built(backend: str) -> None:
    state = IsolateVoiceState(sample_rate=RATE, channels=1)

    with pytest.raises(IsolateVoiceError, match="per-chunk"):
        isolate_voice_chunk(np.zeros(RATE // 10, dtype=np.float32), state, backend=backend)  # type: ignore[arg-type]


def test_per_chunk_isolation_checks_its_arguments_first() -> None:
    state = IsolateVoiceState(sample_rate=RATE, channels=2)

    with pytest.raises(ValueError, match="channels"):
        isolate_voice_chunk(np.zeros(160, dtype=np.float32), state, backend="stub")
    with pytest.raises(ValueError, match="floating"):
        isolate_voice_chunk(np.zeros((160, 2), dtype=np.int16), state, backend="stub")
