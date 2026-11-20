"""Noise reduction (F-33): ``denoise(samples, sample_rate, strength)`` on a whole clip, and the
two streaming passes the renderer uses (F-33b) so an export never holds the whole clip.

Backends (``VIBE_BACKEND``, docs/ARCHITECTURE.md "Compute"):
- ``stub``: a spectral gate in numpy. No model and no GPU, deterministic, so CI and CPU laptops
  get real (if modest) noise reduction. It suits steady noise (hiss, hum, fans) and leaves
  speech mostly alone.
- ``local`` / ``remote``: the chosen model (still open; DeepFilterNet is the lead candidate).
  They raise until they land, rather than quietly handing back the noisy audio.

The stub works in two passes, so it can stream:
1. ``learn_noise`` reads the audio once, a chunk at a time, and keeps one noise profile per
   ``PROFILE_SECONDS`` window (per channel, per frequency bin). It's small (about 0.7 MB for 30
   minutes of stereo), depends only on the source clip, and is cached per video
   (``cached_noise_profile``), so re-exports skip this pass.
2. ``NoiseGate`` applies the gate a chunk at a time, carrying its overlap-add state from one
   chunk to the next, so any chunk size gives the same samples as the whole clip at once. Each
   frame uses the profile at its own time, blended between the two nearest windows.

Live mode gets ``denoise_chunk(chunk, state, strength)``: the shape is fixed here, but it raises
until it has a running noise estimate (a live stream has no profile to learn first).
"""

import base64
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from vibe.config import Backend, model_backend
from vibe.modules.backend import Analysis
from vibe.modules.cache import ModuleError, read_json, write_json
from vibe.storage import Storage, VideoRef

MAX_ATTENUATION_DB = 24.0
"""How far ``strength=1`` turns noise down. Speech bins are kept; noise bins go this low."""

PROFILE_SECONDS = 10.0
"""One noise profile per window this long, so noise that changes over a clip is followed."""

PROFILE_MARGIN_DB = 6.0
"""How far a window's profile may sit above the whole clip's. A window that's all speech has no
quiet frames, so its own estimate is speech; this cap stops the gate from eating it. Unmeasured
starting value: tune it on real clips."""

_FRAME_SECONDS = 0.032  # ~1024 samples at 32 kHz; noise is steady, speech changes within this
_MIN_FRAME_LOG2 = 4  # frames are at least 16 samples, so very low rates still get a valid hop
_NOISE_PERCENTILE = 10  # per frequency bin, across frames: low enough to skip speech
_OVERSUBTRACT = 3.0  # how many times the noise level a bin needs before it's kept whole
_BATCH_FRAMES = 1024  # frames transformed at once, so memory stays flat on long chunks
_SILENT = 1e-8  # frames quieter than this (digital silence, e.g. a mute) aren't noise
_OLA_GAIN = 1.5  # periodic Hann squared at 75% overlap sums to this everywhere


class DenoiseError(Exception):
    """The backend can't denoise (not built, model missing, server down)."""


def frame_size(sample_rate: int) -> int:
    """The gate's FFT size at this rate: a power of two near ``_FRAME_SECONDS``."""
    return 1 << max(_MIN_FRAME_LOG2, round(np.log2(_FRAME_SECONDS * sample_rate)))


def check_backend(backend: Backend | None = None) -> None:
    """Raise ``DenoiseError`` unless ``backend`` (default ``VIBE_BACKEND``) can denoise."""
    backend = backend or model_backend()
    if backend != "stub":
        raise DenoiseError(
            f"the {backend!r} denoise backend isn't built yet (F-33); set VIBE_BACKEND=stub"
        )


# --- Pass 1: the noise profile -------------------------------------------------------------


@dataclass(frozen=True)
class NoiseProfile:
    """What ``learn_noise`` found: per window, per channel, per frequency bin, the noise level.

    ``windows`` is shaped (windows, channels, bins), float32; window ``k`` covers samples
    ``[k * window_samples, (k + 1) * window_samples)``. At least one window, always.
    """

    sample_rate: int
    channels: int
    window_samples: int
    windows: np.ndarray = field(repr=False)

    @property
    def size(self) -> int:
        return frame_size(self.sample_rate)

    def at(self, positions: np.ndarray, channel: int) -> np.ndarray:
        """The profile at each sample position (frames, bins): a straight line between the
        centres of the two nearest windows, and the end window's profile past either end."""
        levels = self.windows[:, channel]
        where = np.clip(positions / self.window_samples - 0.5, 0, len(levels) - 1)
        low = np.floor(where).astype(int)
        high = np.minimum(low + 1, len(levels) - 1)
        frac = (where - low).astype(np.float32)[:, None]
        return levels[low] * (1 - frac) + levels[high] * frac

    def to_json(self) -> dict[str, object]:
        data = np.ascontiguousarray(self.windows, dtype="<f4")
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "window_samples": self.window_samples,
            "shape": list(data.shape),
            "windows": base64.b64encode(data.tobytes()).decode("ascii"),
        }

    @classmethod
    def from_json(cls, data: object) -> "NoiseProfile":
        try:
            assert isinstance(data, dict)
            raw = base64.b64decode(data["windows"], validate=True)
            windows = np.frombuffer(raw, dtype="<f4").reshape(data["shape"]).astype(np.float32)
            return cls(
                int(data["sample_rate"]),
                int(data["channels"]),
                int(data["window_samples"]),
                windows,
            )
        except (AssertionError, KeyError, TypeError, ValueError) as e:
            raise ModuleError(
                f"a cached noise profile is malformed ({e}); delete it to redo"
            ) from e


def learn_noise(
    chunks: Iterable[np.ndarray],
    sample_rate: int,
    window_seconds: float = PROFILE_SECONDS,
) -> NoiseProfile:
    """Pass 1: read ``chunks`` ((n,) or (n, channels), any sizes) once and return the noise
    profile. Memory is about one window's spectra, whatever the clip's length."""
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be positive, got {sample_rate}")
    size = frame_size(sample_rate)
    window_samples = max(size, round(window_seconds * sample_rate))
    bins = size // 2 + 1
    hann = np.hanning(size + 1)[:-1]
    leftover: np.ndarray | None = None  # samples not yet in a full frame, (n, channels)
    position = 0  # sample number of leftover[0]
    current: list[np.ndarray] = []  # this window's frame spectra, each (frames, channels, bins)
    window = 0
    profiles: list[np.ndarray] = []

    def close(index: int) -> None:
        """Turn the frames gathered so far into window ``index`` (and fill skipped ones)."""
        nonlocal current
        while len(profiles) < index:
            profiles.append(np.full((channels, bins), np.nan, dtype=np.float32))
        profiles.append(_window_profile(current, channels, bins))
        current = []

    channels = 0
    for chunk in chunks:
        chunk = np.asarray(chunk)
        data = chunk[:, None] if chunk.ndim == 1 else chunk
        if leftover is None:
            channels = data.shape[1]
            leftover = np.zeros((0, channels), dtype=np.float64)
        data = np.concatenate([leftover, data.astype(np.float64)])
        whole = len(data) // size
        for f in range(whole):
            start = position + f * size
            if start // window_samples != window:
                close(window)
                window = start // window_samples
            frame = data[f * size : (f + 1) * size].T * hann  # (channels, size)
            current.append(np.abs(np.fft.rfft(frame, axis=1)).astype(np.float32))
        leftover = data[whole * size :]
        position += whole * size
    if leftover is None:  # no audio at all
        channels = 1
    close(window)

    stack = np.stack(profiles)  # (windows, channels, bins), NaN where a window had no sound
    known = ~np.isnan(stack[:, :, 0])
    whole_clip = np.zeros((channels, bins), dtype=np.float32)
    for c in range(channels):
        if known[:, c].any():
            whole_clip[c] = np.percentile(stack[known[:, c], c], _NOISE_PERCENTILE, axis=0)
    cap = whole_clip * np.float32(10 ** (PROFILE_MARGIN_DB / 20))
    stack = np.where(np.isnan(stack), whole_clip, np.minimum(stack, cap))
    return NoiseProfile(sample_rate, channels, window_samples, stack.astype(np.float32))


def _window_profile(frames: list[np.ndarray], channels: int, bins: int) -> np.ndarray:
    """Per channel, per bin, the level the window's quieter frames sit at; NaN with no sound."""
    out = np.full((channels, bins), np.nan, dtype=np.float32)
    if not frames:
        return out
    mag = np.stack(frames)  # (frames, channels, bins)
    for c in range(channels):
        loud = mag[(mag[:, c] ** 2).mean(axis=1) > _SILENT**2, c]
        if len(loud):
            out[c] = np.percentile(loud, _NOISE_PERCENTILE, axis=0)
    return out


class NoiseProfileAnalysis(Analysis):
    """The noise profile of a clip's audio at one rate and layout, for the analysis cache."""

    module: ClassVar[str] = "noise_profile"
    kind: ClassVar[str] = "noise_profile"
    version: ClassVar[int] = 1

    def __init__(self, sample_rate: int, layout: str, window_seconds: float = PROFILE_SECONDS):
        super().__init__(sample_rate=sample_rate, layout=layout, window_seconds=window_seconds)

    def compute(self, video_path: Path) -> object:
        return learn_noise_from_file(
            video_path,
            int(self.settings["sample_rate"]),  # type: ignore[call-overload]
            str(self.settings["layout"]),
            float(self.settings["window_seconds"]),  # type: ignore[arg-type]
        ).to_json()


def learn_noise_from_file(
    path: str | Path, sample_rate: int, layout: str, window_seconds: float = PROFILE_SECONDS
) -> NoiseProfile:
    """Pass 1 over a clip's audio track, decoded at ``sample_rate`` in ``layout``."""
    from vibe.video import iter_audio_chunks

    chunks = (c.samples for c in iter_audio_chunks(path, 1.0, sample_rate, layout=layout))
    return learn_noise(chunks, sample_rate, window_seconds)


def cached_noise_profile(
    storage: Storage, video: VideoRef, sample_rate: int, layout: str
) -> NoiseProfile:
    """The video's noise profile from the analysis cache, learned and cached on a miss.

    Unlike model analyses, this computes on every backend, ``stub`` included: it's the stub's
    own plain-numpy pass, not a model, so there's nothing to wait for a GPU for.
    """
    analysis = NoiseProfileAnalysis(sample_rate, layout)
    data = read_json(storage, video.sha256, analysis.kind, analysis.key)
    if data is None:
        data = analysis.compute(storage.video_path(video))
        write_json(storage, video.sha256, analysis.kind, analysis.key, data)
    return NoiseProfile.from_json(data)


# --- Pass 2: the gate ------------------------------------------------------------------------


class NoiseGate:
    """Pass 2: the spectral gate over a stream, one chunk at a time.

    ``push`` takes the next chunk and returns the samples that are now final, in order; together
    with ``finish`` they're exactly as many samples as went in, lined up with them (sample ``i``
    out is sample ``i`` in, denoised). Output lags input by about one frame (``profile.size``
    samples), so the first pushes of a stream can return nothing.
    """

    def __init__(
        self, profile: NoiseProfile, strength: float | None = None, dtype: object = np.float32
    ) -> None:
        self._strength = _check_strength(strength)
        self._channels = [
            _ChannelGate(profile, c, self._strength, np.dtype(dtype))
            for c in range(profile.channels)
        ]
        self._mono: bool | None = None
        self._dtype = np.dtype(dtype)

    def push(self, chunk: np.ndarray) -> np.ndarray:
        if chunk.ndim not in (1, 2):
            raise ValueError(f"chunks must be (n,) or (n, channels), got shape {chunk.shape}")
        mono = chunk.ndim == 1
        if self._mono is None:
            self._mono = mono
        channels = 1 if mono else chunk.shape[1]
        if channels != len(self._channels) or mono != self._mono:
            raise ValueError(
                f"chunk has shape {chunk.shape}, the profile has {len(self._channels)} channels"
            )
        data = chunk[:, None] if mono else chunk
        return self._out([g.push(data[:, c]) for c, g in enumerate(self._channels)])

    def finish(self) -> np.ndarray:
        """The rest of the stream, once the last chunk is in."""
        return self._out([g.finish() for g in self._channels])

    def _out(self, parts: list[np.ndarray]) -> np.ndarray:
        mono = self._mono if self._mono is not None else len(parts) == 1
        if mono:
            return parts[0]
        return np.stack(parts, axis=1)


class _ChannelGate:
    """One channel's stream: STFT frames of ``size`` every ``size // 4`` samples, each bin scaled
    by its gain, overlap-added. Positions below are "padded": the stream starts with ``size``
    zeros, so the first sample is in 4 frames like every other."""

    def __init__(self, profile: NoiseProfile, channel: int, strength: float, dtype: np.dtype):
        self._profile, self._channel, self._strength = profile, channel, strength
        self._work = np.result_type(dtype, np.float32)
        self._size = size = profile.size
        self._hop = size // 4
        self._hann = np.hanning(size + 1)[:-1].astype(self._work)
        self._floor = 10 ** (-MAX_ATTENUATION_DB / 20)
        self._base = 0  # padded position of _pending[0], always the next frame's start
        self._pending = np.zeros(size, dtype=self._work)
        self._tail = np.zeros(0, dtype=self._work)  # overlap sums past _base
        self._skip = size  # leading padding not to emit
        self._taken = 0  # real samples in
        self._given = 0  # samples out

    def push(self, x: np.ndarray) -> np.ndarray:
        self._taken += len(x)
        self._pending = np.concatenate([self._pending, x.astype(self._work)])
        return self._run()

    def finish(self) -> np.ndarray:
        # The same padding a whole clip gets: every real sample ends up in 4 frames.
        self._pending = np.concatenate(
            [self._pending, np.zeros(self._size + self._hop, dtype=self._work)]
        )
        return self._run()

    def _run(self) -> np.ndarray:
        size, hop = self._size, self._hop
        count = (len(self._pending) - size) // hop + 1 if len(self._pending) >= size else 0
        if count <= 0:
            return np.zeros(0, dtype=self._work)
        acc = np.zeros((count + 3) * hop, dtype=self._work)
        acc[: len(self._tail)] += self._tail
        blocks = acc.reshape(-1, hop)
        frames = sliding_window_view(self._pending[: (count - 1) * hop + size], size)[::hop]
        for first in range(0, count, _BATCH_FRAMES):
            batch = frames[first : first + _BATCH_FRAMES]
            starts = self._base + (first + np.arange(len(batch))) * hop
            noise = self._profile.at(starts - size // 2, self._channel).astype(self._work)
            spec = np.fft.rfft(batch * self._hann, axis=1)
            mag = np.abs(spec)
            full = np.clip(1 - _OVERSUBTRACT * noise / np.maximum(mag, _SILENT), self._floor, 1)
            gain = 1 - self._strength * (1 - full)  # strength blends untouched (0) to full (1)
            out = (np.fft.irfft(spec * gain, n=size, axis=1) * self._hann).astype(self._work)
            out = out.reshape(len(batch), 4, hop)
            for j in range(4):
                blocks[first + j : first + j + len(batch)] += out[:, j]
        # Positions before the next frame's start get no more frames: they're final.
        done = acc[: count * hop] / _OLA_GAIN
        self._tail = acc[count * hop :]
        self._pending = self._pending[count * hop :]
        self._base += count * hop
        skip = min(self._skip, len(done))
        self._skip -= skip
        done = done[skip:][: max(0, self._taken - self._given)]  # not the trailing padding
        self._given += len(done)
        return done


# --- Whole clip and live ---------------------------------------------------------------------


@dataclass
class DenoiseState:
    """What a live stream carries from one chunk to the next.

    ``noise`` is the running noise estimate (per channel, per frequency bin) and ``tail`` the
    samples held back for overlap-add; both start empty and belong to the backend.
    """

    sample_rate: int
    channels: int = 1
    noise: np.ndarray | None = field(default=None, repr=False)
    tail: np.ndarray | None = field(default=None, repr=False)


def denoise(
    samples: np.ndarray,
    sample_rate: int,
    strength: float | None = None,
    backend: Backend | None = None,
) -> np.ndarray:
    """``samples`` ((n,) or (n, channels), floating point in [-1, 1]) with background noise reduced.

    ``strength`` goes from 0 (unchanged) to 1 (strongest); ``None`` means 1, as in the contract's
    ``add_audio_filter``. It scales the whole effect, so small values change the audio a little.
    Returns a new array with the same shape, rate and dtype, computed in that precision (float16
    in float32). Integer PCM is rejected: convert it to float first.

    The same two passes as the renderer (``learn_noise``, then ``NoiseGate``), on one chunk.
    """
    strength = _check(samples, sample_rate, strength)
    check_backend(backend)
    if strength == 0 or len(samples) < frame_size(sample_rate):
        return samples.copy()  # nothing to do, or too little to tell noise from signal
    if samples.ndim == 2:
        # Channel by channel, so a channel's output never depends on the others' batching.
        return np.stack(
            [
                denoise(samples[:, c], sample_rate, strength, "stub")
                for c in range(samples.shape[1])
            ],
            axis=1,
        )
    profile = learn_noise([samples], sample_rate)
    gate = NoiseGate(profile, strength, samples.dtype)
    out = np.concatenate([gate.push(samples), gate.finish()])
    return out.astype(samples.dtype)


def denoise_chunk(
    chunk: np.ndarray,
    state: DenoiseState,
    strength: float | None = None,
    backend: Backend | None = None,
) -> np.ndarray:
    """Live mode: one chunk of a stream ((n,) or (n, channels)), denoised, updating ``state``.

    The output may lag the input by up to one frame (overlap-add), so it can be shorter than the
    chunk at the start of a stream. Never calls the network. Not built yet on any backend: it
    raises ``DenoiseError`` after checking its arguments, rather than passing noise through.
    ``NoiseGate`` already streams; what's missing is a running noise estimate to feed it.
    """
    _check(chunk, state.sample_rate, strength)
    channels = 1 if chunk.ndim == 1 else chunk.shape[1]
    if channels != state.channels:
        raise ValueError(f"chunk has {channels} channels, the stream has {state.channels}")
    backend = backend or model_backend()
    raise DenoiseError(
        f"per-chunk denoise isn't built yet on the {backend!r} backend (F-33, live mode)"
    )


def _check_strength(strength: float | None) -> float:
    if strength is None:
        return 1.0
    if not 0 <= strength <= 1:
        raise ValueError(f"strength must be between 0 and 1, got {strength}")
    return float(strength)


def _check(samples: np.ndarray, sample_rate: int, strength: float | None) -> float:
    """The strength to use (``None`` is 1), after rejecting arguments ``denoise`` can't take."""
    strength = _check_strength(strength)
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be positive, got {sample_rate}")
    if samples.ndim not in (1, 2):
        raise ValueError(f"samples must be (n,) or (n, channels), got shape {samples.shape}")
    if not np.issubdtype(samples.dtype, np.floating):
        raise ValueError(f"samples must be floating point in [-1, 1], got {samples.dtype}")
    return strength


ProfileSource = Callable[[int, str], NoiseProfile]
"""Gives the renderer a noise profile for its output ``(sample_rate, layout)``."""
