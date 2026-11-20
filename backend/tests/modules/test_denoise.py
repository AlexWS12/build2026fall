"""F-33: noise reduction, and F-33b's streaming passes. The stub backend only: no GPU, no model,
no video files (the cache test uses a WAV written here)."""

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from vibe.modules.cache import ModuleError, cached_keys
from vibe.modules.denoise import (
    DenoiseError,
    DenoiseState,
    NoiseGate,
    NoiseProfile,
    cached_noise_profile,
    denoise,
    denoise_chunk,
    learn_noise,
)
from vibe.storage import Storage

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
        (np.zeros(RATE, dtype=np.int16), RATE, None),
        (np.zeros(RATE, dtype=np.int32), RATE, None),
    ],
)
def test_bad_arguments_raise(samples: np.ndarray, rate: int, strength: float | None) -> None:
    with pytest.raises(ValueError):
        denoise(samples, rate, strength, backend="stub")


@pytest.mark.parametrize("rate", [1, 8, 60, 1000, 8000])
def test_low_sample_rates_work(rate: int) -> None:
    noisy = tone_with_gaps(seconds=4.0, rate=max(rate, 1000))[0][: 4 * rate] + noise(4 * rate)

    out = denoise(noisy, rate, backend="stub")

    assert out.shape == noisy.shape and np.isfinite(out).all()


def test_small_strength_changes_the_audio_only_a_little() -> None:
    noisy = tone_with_gaps()[0] + noise(4 * RATE)

    out = denoise(noisy, RATE, strength=0.01, backend="stub")

    assert db(out - noisy) < db(noisy) - 35  # 1% of the effect, not the full gate


def test_float64_is_computed_in_float64() -> None:
    noisy = (tone_with_gaps()[0] + noise(4 * RATE)).astype(np.float64)

    out = denoise(noisy, RATE, strength=1e-9, backend="stub")

    assert out.dtype == np.float64
    assert np.max(np.abs(out - noisy)) < 5e-10  # the effect itself is ~1e-10; float32 is ~1e-8


def test_float16_comes_back_float16() -> None:
    noisy = (tone_with_gaps()[0] + noise(4 * RATE)).astype(np.float16)

    assert denoise(noisy, RATE, backend="stub").dtype == np.float16


@pytest.mark.parametrize("backend", ["stub", "local", "remote"])
def test_per_chunk_denoise_has_its_shape_but_isnt_built(backend: str) -> None:
    state = DenoiseState(sample_rate=RATE, channels=1)

    with pytest.raises(DenoiseError, match="per-chunk"):
        denoise_chunk(noise(RATE // 10), state, backend=backend)  # type: ignore[arg-type]


def test_per_chunk_denoise_checks_its_arguments_first() -> None:
    state = DenoiseState(sample_rate=RATE, channels=2)

    with pytest.raises(ValueError, match="channels"):
        denoise_chunk(noise(RATE // 10), state, backend="stub")
    with pytest.raises(ValueError, match="floating"):
        denoise_chunk(np.zeros((160, 2), dtype=np.int16), state, backend="stub")


# --- F-33b: the two streaming passes the renderer uses -----------------------------------


def chunks_of(samples: np.ndarray, size: int) -> list[np.ndarray]:
    return [samples[i : i + size] for i in range(0, len(samples), size)]


def stream(gate: NoiseGate, samples: np.ndarray, size: int) -> np.ndarray:
    parts = [gate.push(c) for c in chunks_of(samples, size)]
    return np.concatenate([*parts, gate.finish()])


@pytest.mark.parametrize("size", [1, 7, 333, 1600, 4800, 64000])
def test_any_chunk_size_gives_the_whole_clip_result(size: int) -> None:
    """No clicks at chunk edges and no drift: every chunking gives the same samples."""
    noisy = tone_with_gaps()[0] + noise(4 * RATE)

    out = stream(NoiseGate(learn_noise([noisy], RATE)), noisy, size)

    assert len(out) == len(noisy)
    np.testing.assert_allclose(out, denoise(noisy, RATE, backend="stub"), atol=1e-6)


def test_streamed_stereo_matches_each_channel_alone() -> None:
    clean, _ = tone_with_gaps()
    stereo = np.stack([clean + noise(len(clean)), clean + noise(len(clean), seed=1)], axis=1)

    out = stream(NoiseGate(learn_noise(chunks_of(stereo, 1600), RATE)), stereo, 1600)

    assert out.shape == stereo.shape
    np.testing.assert_allclose(out, denoise(stereo, RATE, backend="stub"), atol=1e-6)


def test_the_gate_lines_its_output_up_with_its_input() -> None:
    """At strength 0 the stream gives back its input, so nothing is shifted in time."""
    noisy = tone_with_gaps()[0] + noise(4 * RATE)

    out = stream(NoiseGate(learn_noise([noisy], RATE), strength=0), noisy, 1600)

    np.testing.assert_allclose(out, noisy, atol=1e-6)


def test_the_profile_doesnt_depend_on_chunk_size() -> None:
    noisy = tone_with_gaps(seconds=25.0)[0] + noise(25 * RATE)

    whole = learn_noise([noisy], RATE)
    chunked = learn_noise(chunks_of(noisy, 1234), RATE)

    np.testing.assert_array_equal(whole.windows, chunked.windows)
    assert whole.windows.shape[0] == 3  # 10 s windows over 25 s


def test_the_profile_follows_noise_that_changes_over_the_clip() -> None:
    clean, on = tone_with_gaps(seconds=20.0)
    louder = np.where(np.arange(len(clean)) < 10 * RATE, 1.0, 3.0).astype(np.float32)
    noisy = clean + noise(len(clean)) * louder

    profile = learn_noise([noisy], RATE)
    out = denoise(noisy, RATE, backend="stub")

    first, second = profile.windows[0, 0].mean(), profile.windows[1, 0].mean()
    assert second > 2 * first
    pauses = ~on & (np.arange(len(clean)) >= 10 * RATE)
    assert db(out[pauses]) < db(noisy[pauses]) - 6  # the louder noise is still turned down


def test_a_window_with_no_pauses_cant_learn_speech_as_noise() -> None:
    """A window that's all tone is capped near the whole clip's profile, so the tone survives."""
    clean, on = tone_with_gaps(seconds=30.0)
    clean[10 * RATE : 20 * RATE] = 0.3 * np.sin(2 * np.pi * 440 * np.arange(10 * RATE) / RATE)
    noisy = clean + noise(len(clean))

    out = denoise(noisy, RATE, backend="stub")

    middle = slice(11 * RATE, 19 * RATE)
    assert db((out - clean)[middle]) < db(clean[middle]) - 20


def test_a_profile_round_trips_through_json() -> None:
    profile = learn_noise([tone_with_gaps()[0] + noise(4 * RATE)], RATE)

    again = NoiseProfile.from_json(json.loads(json.dumps(profile.to_json())))

    np.testing.assert_array_equal(again.windows, profile.windows)
    assert (again.sample_rate, again.channels) == (profile.sample_rate, profile.channels)


def test_a_malformed_profile_is_an_error() -> None:
    with pytest.raises(ModuleError, match="malformed"):
        NoiseProfile.from_json({"sample_rate": RATE, "windows": "not base64!"})


def test_no_audio_at_all_gives_an_empty_profile() -> None:
    profile = learn_noise([], RATE)

    assert profile.windows.shape[0] == 1 and not profile.windows.any()


def test_the_gate_refuses_chunks_that_dont_match_the_profile() -> None:
    gate = NoiseGate(learn_noise([noise(RATE)], RATE))

    with pytest.raises(ValueError, match="channels"):
        gate.push(np.zeros((160, 2), dtype=np.float32))


def write_wav(path: Path, samples: np.ndarray, rate: int) -> Path:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm.tobytes())
    return path


def test_the_profile_is_cached_with_the_video(tmp_path: Path) -> None:
    """Pass 1 runs once per video, on every backend (it's numpy, not a model)."""
    storage = Storage(tmp_path / "data")
    noisy = tone_with_gaps()[0] + noise(4 * RATE)
    video = storage.add_video(write_wav(tmp_path / "clip.wav", noisy, RATE))

    first = cached_noise_profile(storage, video, RATE, "mono")
    keys = cached_keys(storage, video.sha256, "noise_profile")
    storage.video_path(video).unlink()  # a second call must not need the video
    second = cached_noise_profile(storage, video, RATE, "mono")

    assert len(keys) == 1 and keys[0].startswith("noise_profile-")
    np.testing.assert_array_equal(first.windows, second.windows)
    assert first.channels == 1 and first.sample_rate == RATE
