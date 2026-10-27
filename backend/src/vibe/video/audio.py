"""Audio extraction: the whole clip at once, or in fixed-size chunks (the per-chunk path).

Samples are ``float32`` in [-1, 1], shaped (n,) for mono or (n, channels) otherwise. Sample 0 is
at the clip's time 0 (its first video frame), so sample times line up with ``FrameIndex`` times:
audio that starts late is padded with silence, and audio from before the first frame is dropped.
"""

import wave
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import av
import av.error
import numpy as np

from vibe.video._media import open_media
from vibe.video.errors import NoAudioStream, UnreadableMedia

DEFAULT_SAMPLE_RATE = 16000  # what speech models (Whisper and friends) expect


@dataclass(frozen=True)
class AudioChunk:
    start: float  # seconds
    samples: np.ndarray


def extract_audio(
    path: str | Path, sample_rate: int = DEFAULT_SAMPLE_RATE, mono: bool = True
) -> tuple[np.ndarray, int]:
    """The clip's whole audio track as ``(samples, sample_rate)``.

    Raises ``NoAudioStream`` if the file has no audio, plus the errors ``probe`` raises.
    """
    data = np.concatenate(list(_blocks(Path(path), sample_rate, mono)))
    return _shape(data, mono), sample_rate


def iter_audio_chunks(
    path: str | Path,
    chunk_seconds: float = 1.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    mono: bool = True,
) -> Iterator[AudioChunk]:
    """The audio track in consecutive chunks of ``chunk_seconds``; the last may be shorter.

    Decodes as it goes, so memory stays at about one chunk whatever the clip's length.
    """
    if chunk_seconds <= 0:
        raise ValueError(f"chunk_seconds must be positive, got {chunk_seconds}")
    size = round(chunk_seconds * sample_rate)
    if size < 1:
        raise ValueError(f"chunk_seconds={chunk_seconds} is under one sample at {sample_rate} Hz")
    pending: list[np.ndarray] = []
    buffered = 0
    emitted = 0
    for block in _blocks(Path(path), sample_rate, mono):
        pending.append(block)
        buffered += len(block)
        while buffered >= size:
            data = np.concatenate(pending)
            yield AudioChunk(emitted / sample_rate, _shape(data[:size], mono))
            emitted += size
            pending, buffered = [data[size:]], len(data) - size
    if buffered:
        yield AudioChunk(emitted / sample_rate, _shape(np.concatenate(pending), mono))


def write_wav(path: str | Path, samples: np.ndarray, sample_rate: int) -> None:
    """Save samples as a 16-bit PCM WAV file. Values outside [-1, 1] are clipped."""
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim not in (1, 2):
        raise ValueError(f"samples must be (n,) or (n, channels), got shape {samples.shape}")
    channels = 1 if samples.ndim == 1 else samples.shape[1]
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).round().astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(pcm.tobytes())


def _shape(data: np.ndarray, mono: bool) -> np.ndarray:
    return data[:, 0] if mono else data


def _blocks(path: Path, sample_rate: int, mono: bool) -> Iterator[np.ndarray]:
    """Resampled audio as (n, channels) float32 blocks, aligned so sample 0 is at time 0.

    The first block is always empty, so callers can concatenate without a special case.
    """
    with open_media(path) as container:
        if not container.streams.audio:
            raise NoAudioStream(f"{path} has no audio stream")
        stream = container.streams.audio[0]
        origin = 0.0  # the clip's time 0: its first video frame, if it has video
        if container.streams.video:
            video = container.streams.video[0]
            if video.start_time is not None and video.time_base is not None:
                origin = float(video.start_time * video.time_base)
        layout = "mono" if mono else stream.layout.name
        resampler = av.AudioResampler(format="flt", layout=layout, rate=sample_rate)
        channels = len(av.AudioLayout(layout).channels)
        yield np.zeros((0, channels), np.float32)

        skip: int | None = None  # samples to drop (> 0) or silence to add (< 0) at the start
        try:
            for frame in container.decode(stream):
                if skip is None:
                    start = origin if frame.time is None else frame.time
                    skip = round((origin - start) * sample_rate)
                    if skip < 0:
                        yield np.zeros((-skip, channels), np.float32)
                        skip = 0
                for out in resampler.resample(frame):
                    block = out.to_ndarray().reshape(-1, channels)
                    if skip:
                        dropped = min(skip, len(block))
                        block, skip = block[dropped:], skip - dropped
                    if len(block):
                        yield block
            for out in resampler.resample(None):
                block = out.to_ndarray().reshape(-1, channels)[skip or 0 :]
                skip = 0
                if len(block):
                    yield block
        except av.error.FFmpegError as e:
            raise UnreadableMedia(f"{path}: decoding audio failed: {e}") from e
