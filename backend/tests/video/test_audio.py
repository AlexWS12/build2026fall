"""Audio extraction: the tone comes out at the right rate, length, pitch and start time."""

import wave
from pathlib import Path

import numpy as np
import pytest
from clip_spec import AUDIO_DELAY, SAMPLE_RATE, SECONDS, TONE_HZ

from vibe.video import (
    NoAudioStream,
    UnreadableMedia,
    extract_audio,
    iter_audio_chunks,
    write_wav,
)

# AAC encodes in 1024-sample frames, so its decoded length can be off by up to one frame.
AAC_SLACK = 1024 / SAMPLE_RATE


def peak_hz(samples: np.ndarray, sample_rate: int) -> float:
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return float(np.fft.rfftfreq(len(samples), 1 / sample_rate)[spectrum.argmax()])


def rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def test_default_is_16k_mono(main_clip: Path) -> None:
    samples, rate = extract_audio(main_clip)
    assert rate == 16000
    assert samples.dtype == np.float32
    assert samples.ndim == 1
    assert len(samples) / rate == pytest.approx(SECONDS, abs=AAC_SLACK)
    assert peak_hz(samples, rate) == pytest.approx(TONE_HZ, abs=2)
    # ffmpeg's sine source has amplitude 1/8, so RMS is 1/(8·√2) ≈ 0.088.
    assert rms(samples) == pytest.approx(1 / (8 * np.sqrt(2)), rel=0.05)
    assert np.abs(samples).max() <= 1.0


def test_native_rate_stereo(main_clip: Path) -> None:
    samples, rate = extract_audio(main_clip, sample_rate=SAMPLE_RATE, mono=False)
    assert rate == SAMPLE_RATE
    assert samples.shape[1] == 2
    assert len(samples) / rate == pytest.approx(SECONDS, abs=AAC_SLACK)
    # The tone was mono, copied to both channels; lossy AAC makes them differ very slightly.
    np.testing.assert_allclose(samples[:, 0], samples[:, 1], atol=1e-3)


def test_late_audio_is_padded_to_line_up_with_video(delayed_audio_clip: Path) -> None:
    samples, rate = extract_audio(delayed_audio_clip)
    lead = round(AUDIO_DELAY * rate)
    assert np.all(samples[:lead] == 0), "before the audio starts there should be silence"
    assert rms(samples[lead : lead + rate // 10]) > 0.05, "the tone starts right at the delay"
    assert len(samples) / rate == pytest.approx(AUDIO_DELAY + SECONDS, abs=AAC_SLACK)


def test_audio_only_file(audio_only: Path) -> None:
    samples, rate = extract_audio(audio_only)
    assert len(samples) / rate == pytest.approx(1.0, abs=AAC_SLACK)
    assert peak_hz(samples, rate) == pytest.approx(TONE_HZ, abs=2)


def test_chunks_add_up_to_the_whole(main_clip: Path) -> None:
    whole, rate = extract_audio(main_clip)
    chunks = list(iter_audio_chunks(main_clip, chunk_seconds=0.3))
    size = round(0.3 * rate)
    assert [c.start for c in chunks] == pytest.approx([i * 0.3 for i in range(len(chunks))])
    assert all(len(c.samples) == size for c in chunks[:-1])
    assert 0 < len(chunks[-1].samples) <= size
    np.testing.assert_array_equal(np.concatenate([c.samples for c in chunks]), whole)


def test_chunks_stereo(main_clip: Path) -> None:
    chunk = next(iter_audio_chunks(main_clip, chunk_seconds=0.5, mono=False))
    assert chunk.samples.shape == (8000, 2)


def test_bad_chunk_size(main_clip: Path) -> None:
    with pytest.raises(ValueError):
        next(iter_audio_chunks(main_clip, chunk_seconds=0))
    with pytest.raises(ValueError):
        next(iter_audio_chunks(main_clip, chunk_seconds=1e-6))


@pytest.mark.parametrize("mono", [True, False])
def test_write_wav_round_trip(main_clip: Path, tmp_path: Path, mono: bool) -> None:
    samples, rate = extract_audio(main_clip, mono=mono)
    out = tmp_path / "out.wav"
    write_wav(out, samples, rate)
    with wave.open(str(out), "rb") as wav:
        assert wav.getnchannels() == (1 if mono else 2)
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == rate
        assert wav.getnframes() == len(samples)
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    np.testing.assert_allclose(pcm.reshape(samples.shape) / 32767, samples, atol=1 / 32767)


def test_write_wav_clips(tmp_path: Path) -> None:
    out = tmp_path / "loud.wav"
    write_wav(out, np.array([2.0, -2.0], np.float32), 8000)
    with wave.open(str(out), "rb") as wav:
        pcm = np.frombuffer(wav.readframes(2), dtype="<i2")
    assert pcm.tolist() == [32767, -32767]


def test_errors(silent_clip: Path, junk_file: Path) -> None:
    with pytest.raises(NoAudioStream):
        extract_audio(silent_clip)
    with pytest.raises(NoAudioStream):
        next(iter_audio_chunks(silent_clip))
    with pytest.raises(UnreadableMedia):
        extract_audio(junk_file)
