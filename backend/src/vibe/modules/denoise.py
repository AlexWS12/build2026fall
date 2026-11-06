"""Noise reduction (F-33): ``denoise(samples, sample_rate, strength)`` on a whole clip.

Backends (``VIBE_BACKEND``, docs/ARCHITECTURE.md "Compute"):
- ``stub``: a spectral gate in numpy. No model and no GPU, deterministic, so CI and CPU laptops
  get real (if modest) noise reduction. It learns the noise from the clip's quietest frames, so
  it suits steady noise (hiss, hum, fans) and leaves speech mostly alone.
- ``local`` / ``remote``: the chosen model (still open; DeepFilterNet is the lead candidate).
  They raise until they land, rather than quietly handing back the noisy audio.

Live mode gets ``denoise_chunk(chunk, state, strength)``: the shape is fixed here, but it raises
until the streaming model lands, since it needs a running noise estimate instead of the
whole-clip one.
"""

from dataclasses import dataclass, field

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from vibe.config import Backend, model_backend

MAX_ATTENUATION_DB = 24.0
"""How far ``strength=1`` turns noise down. Speech bins are kept; noise bins go this low."""

_FRAME_SECONDS = 0.032  # ~1024 samples at 32 kHz; noise is steady, speech changes within this
_MIN_FRAME_LOG2 = 4  # frames are at least 16 samples, so very low rates still get a valid hop
_NOISE_PERCENTILE = 10  # per frequency bin, across frames: low enough to skip speech
_OVERSUBTRACT = 3.0  # how many times the noise level a bin needs before it's kept whole
_MAX_NOISE_FRAMES = 2000  # frames sampled for the noise estimate, so long clips stay cheap
_BATCH_FRAMES = 1024  # frames transformed at once, so memory stays flat on long clips
_SILENT = 1e-8  # frames quieter than this (digital silence, e.g. a mute) aren't noise


class DenoiseError(Exception):
    """The backend can't denoise (not built, model missing, server down)."""


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
    """
    strength = _check(samples, sample_rate, strength)
    backend = backend or model_backend()
    if backend != "stub":
        raise DenoiseError(
            f"the {backend!r} denoise backend isn't built yet (F-33); set VIBE_BACKEND=stub"
        )
    if strength == 0:
        return samples.copy()
    work = np.result_type(samples.dtype, np.float32)
    size = 1 << max(_MIN_FRAME_LOG2, round(np.log2(_FRAME_SECONDS * sample_rate)))
    if samples.ndim == 1:
        return _gate(samples.astype(work), size, strength).astype(samples.dtype)
    channels = [_gate(samples[:, c].astype(work), size, strength) for c in range(samples.shape[1])]
    return np.stack(channels, axis=1).astype(samples.dtype)


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
    """
    _check(chunk, state.sample_rate, strength)
    channels = 1 if chunk.ndim == 1 else chunk.shape[1]
    if channels != state.channels:
        raise ValueError(f"chunk has {channels} channels, the stream has {state.channels}")
    backend = backend or model_backend()
    raise DenoiseError(
        f"per-chunk denoise isn't built yet on the {backend!r} backend (F-33, live mode)"
    )


def _check(samples: np.ndarray, sample_rate: int, strength: float | None) -> float:
    """The strength to use (``None`` is 1), after rejecting arguments ``denoise`` can't take."""
    if strength is None:
        strength = 1.0
    if not 0 <= strength <= 1:
        raise ValueError(f"strength must be between 0 and 1, got {strength}")
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be positive, got {sample_rate}")
    if samples.ndim not in (1, 2):
        raise ValueError(f"samples must be (n,) or (n, channels), got shape {samples.shape}")
    if not np.issubdtype(samples.dtype, np.floating):
        raise ValueError(f"samples must be floating point in [-1, 1], got {samples.dtype}")
    return strength


def _gate(x: np.ndarray, size: int, strength: float) -> np.ndarray:
    """One channel through a spectral gate: STFT, scale each bin by its gain, overlap-add."""
    n = len(x)
    if n < size:
        return x.copy()  # shorter than one frame: too little to tell noise from signal
    hop = size // 4
    window = np.hanning(size + 1)[:-1].astype(x.dtype)  # periodic Hann
    padded = np.pad(x, (size, size + hop))  # every sample in 4 frames
    frames = sliding_window_view(padded, size)[::hop]
    # Frames that lie fully inside the clip, so the zero padding doesn't read as quiet noise.
    inside = np.arange(len(frames)) * hop
    inside = np.flatnonzero((inside >= size) & (inside + size <= size + n))
    noise = _noise_profile(frames, inside, window)
    floor = 10 ** (-MAX_ATTENUATION_DB / 20)
    out = np.zeros(len(padded), dtype=x.dtype)
    # Frame k covers hop blocks k..k+3, so overlap-add is 4 shifted adds per batch.
    blocks = out[: (len(frames) + 3) * hop].reshape(-1, hop)
    for first in range(0, len(frames), _BATCH_FRAMES):
        batch = frames[first : first + _BATCH_FRAMES]
        spec = np.fft.rfft(batch * window, axis=1)
        mag = np.abs(spec)
        full = np.clip(1 - _OVERSUBTRACT * noise / np.maximum(mag, _SILENT), floor, 1)
        gain = 1 - strength * (1 - full)  # strength blends from untouched (0) to full gate (1)
        chunk = (np.fft.irfft(spec * gain, n=size, axis=1) * window).astype(x.dtype)
        chunk = chunk.reshape(len(batch), 4, hop)
        for j in range(4):
            blocks[first + j : first + j + len(batch)] += chunk[:, j]
    # Periodic Hann squared at 75% overlap sums to 1.5 everywhere the frames cover.
    return out[size : size + n] / 1.5


def _noise_profile(frames: np.ndarray, inside: np.ndarray, window: np.ndarray) -> np.ndarray:
    """Per frequency bin, the level the quieter frames sit at: what the gate treats as noise.

    It transforms a sample of at most ``_MAX_NOISE_FRAMES`` frames a second time rather than
    keeping every spectrum from the main pass, so memory stays flat on long clips.
    """
    picks = inside[np.linspace(0, len(inside) - 1, min(len(inside), _MAX_NOISE_FRAMES)).astype(int)]
    mag = np.abs(np.fft.rfft(frames[picks] * window, axis=1))
    loud = mag[(mag**2).mean(axis=1) > _SILENT**2]
    if len(loud) == 0:
        return np.zeros(mag.shape[1], dtype=window.dtype)  # all silence: nothing to remove
    return np.percentile(loud, _NOISE_PERCENTILE, axis=0).astype(window.dtype)
