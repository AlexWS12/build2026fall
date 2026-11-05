"""F-33: noise reduction. The stub backend only: no GPU, no model, no video files."""

import numpy as np
import pytest

from vibe.modules.denoise import DenoiseError, denoise

RATE = 16000


def db(x: np.ndarray) -> float:
    return float(10 * np.log10(np.mean(np.square(x, dtype=np.float64)) + 1e-20))


def tone_with_gaps(seconds: float = 4.0, rate: int = RATE) -> tuple[np.ndarray, np.ndarray]:
    """A 440 Hz tone that's on for half of every second, like speech with pauses, and its mask."""
    t = np.arange(round(seconds * rate)) / rate
    on = (t % 1.0) < 0.5
    return (0.3 * np.sin(2 * np.pi * 440 * t) * on).astype(np.float32), on


def noise(n: int, seed: int = 0) -> np.ndarray:
    return (0.03 * np.random.default_rng(seed).standard_normal(n)).astype(np.float32)


@pytest.mark.parametrize("rate", [16000, 44100, 48000])
def test_noise_in_the_pauses_is_turned_down_and_the_tone_is_kept(rate: int) -> None:
    clean, on = tone_with_gaps(rate=rate)
    noisy = clean + noise(len(clean))

    out = denoise(noisy, rate, backend="stub")

    assert db(out[~on]) < db(noisy[~on]) - 6
    assert db((out - clean)[on]) < db(clean[on]) - 20  # the tone comes through nearly unchanged


def test_output_has_the_input_shape_dtype_and_length() -> None:
    clean, _ = tone_with_gaps(seconds=1.234)
    stereo = np.stack([clean, clean + noise(len(clean))], axis=1)

    out = denoise(stereo, RATE, backend="stub")

    assert out.shape == stereo.shape and out.dtype == np.float32
    assert out is not stereo


def test_channels_are_denoised_separately() -> None:
    clean, _ = tone_with_gaps()
    noisy = clean + noise(len(clean))
    stereo = np.stack([noisy, clean], axis=1)

    out = denoise(stereo, RATE, backend="stub")

    np.testing.assert_array_equal(out[:, 0], denoise(noisy, RATE, backend="stub"))
    np.testing.assert_array_equal(out[:, 1], denoise(clean, RATE, backend="stub"))


def test_same_input_same_output() -> None:
    noisy = tone_with_gaps()[0] + noise(4 * RATE)

    np.testing.assert_array_equal(
        denoise(noisy, RATE, backend="stub"), denoise(noisy, RATE, backend="stub")
    )


def test_strength_zero_leaves_the_audio_as_it_was() -> None:
    noisy = tone_with_gaps()[0] + noise(4 * RATE)

    np.testing.assert_array_equal(denoise(noisy, RATE, strength=0, backend="stub"), noisy)


def test_more_strength_removes_more_noise() -> None:
    clean, on = tone_with_gaps()
    noisy = clean + noise(len(clean))

    weak = denoise(noisy, RATE, strength=0.2, backend="stub")
    strong = denoise(noisy, RATE, strength=1, backend="stub")

    assert db(strong[~on]) < db(weak[~on]) < db(noisy[~on])


def test_null_strength_means_strongest() -> None:
    noisy = tone_with_gaps()[0] + noise(4 * RATE)

    np.testing.assert_array_equal(
        denoise(noisy, RATE, strength=None, backend="stub"),
        denoise(noisy, RATE, strength=1, backend="stub"),
    )


def test_silence_stays_silent() -> None:
    silence = np.zeros(2 * RATE, dtype=np.float32)

    np.testing.assert_array_equal(denoise(silence, RATE, backend="stub"), silence)


def test_a_clip_shorter_than_one_frame_is_returned_unchanged() -> None:
    blip = noise(100)

    np.testing.assert_array_equal(denoise(blip, RATE, backend="stub"), blip)


def test_the_backend_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBE_BACKEND", "local")

    with pytest.raises(DenoiseError, match="'local'"):
        denoise(noise(RATE), RATE)


@pytest.mark.parametrize("backend", ["local", "remote"])
def test_backends_that_arent_built_fail_instead_of_returning_noisy_audio(backend: str) -> None:
    with pytest.raises(DenoiseError, match="isn't built yet"):
        denoise(noise(RATE), RATE, backend=backend)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("samples", "rate", "strength"),
    [
        (np.zeros(RATE, dtype=np.float32), RATE, 1.5),
        (np.zeros(RATE, dtype=np.float32), RATE, -0.1),
        (np.zeros(RATE, dtype=np.float32), 0, None),
        (np.zeros((2, 2, 2), dtype=np.float32), RATE, None),
    ],
)
def test_bad_arguments_raise(samples: np.ndarray, rate: int, strength: float | None) -> None:
    with pytest.raises(ValueError):
        denoise(samples, rate, strength, backend="stub")
