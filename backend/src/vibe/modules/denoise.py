"""Noise reduction (F-33): ``denoise(samples, sample_rate, strength)`` on a whole clip.

Backends (``VIBE_BACKEND``, docs/ARCHITECTURE.md "Compute"):
- ``stub``: a spectral gate in numpy. No model and no GPU, deterministic, so CI and CPU laptops
  get real (if modest) noise reduction. It learns the noise from the clip's quietest frames, so
  it suits steady noise (hiss, hum, fans) and leaves speech mostly alone.
- ``local`` / ``remote``: the chosen model (still open; DeepFilterNet is the lead candidate).
  They raise until they land, rather than quietly handing back the noisy audio.

Only the whole-clip path exists so far. The per-chunk path for live mode needs a running noise
estimate instead of the whole-clip one; it comes with the model, which streams.
"""

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from vibe.config import Backend, model_backend

MAX_ATTENUATION_DB = 24.0
"""How far ``strength=1`` turns noise down. Speech bins are kept; noise bins go this low."""

_FRAME_SECONDS = 0.032  # ~1024 samples at 32 kHz; noise is steady, speech changes within this
_NOISE_PERCENTILE = 10  # per frequency bin, across frames: low enough to skip speech
_OVERSUBTRACT = 3.0  # how many times the noise level a bin needs before it's kept whole
_MAX_NOISE_FRAMES = 2000  # frames sampled for the noise estimate, so long clips stay cheap
_BATCH_FRAMES = 1024  # frames transformed at once, so memory stays flat on long clips
_SILENT = 1e-8  # frames quieter than this (digital silence, e.g. a mute) aren't noise


class DenoiseError(Exception):
    """The backend can't denoise (not built, model missing, server down)."""


def denoise(
    samples: np.ndarray,
    sample_rate: int,
    strength: float | None = None,
    backend: Backend | None = None,
) -> np.ndarray:
    """``samples`` ((n,) or (n, channels), float32 in [-1, 1]) with background noise reduced.

    ``strength`` goes from 0 (unchanged) to 1 (strongest); ``None`` means 1, as in the contract's
    ``add_audio_filter``. Returns a new array with the same shape, rate and dtype.
    """
    if strength is None:
        strength = 1.0
    if not 0 <= strength <= 1:
        raise ValueError(f"strength must be between 0 and 1, got {strength}")
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be positive, got {sample_rate}")
    if samples.ndim not in (1, 2):
        raise ValueError(f"samples must be (n,) or (n, channels), got shape {samples.shape}")
    backend = backend or model_backend()
    if backend != "stub":
        raise DenoiseError(
            f"the {backend!r} denoise backend isn't built yet (F-33); set VIBE_BACKEND=stub"
        )
    if strength == 0:
        return samples.copy()
    floor = 10 ** (-MAX_ATTENUATION_DB * strength / 20)
    size = 1 << round(np.log2(_FRAME_SECONDS * sample_rate))
    if samples.ndim == 1:
        return _gate(samples, size, floor).astype(samples.dtype)
    channels = [_gate(samples[:, c], size, floor) for c in range(samples.shape[1])]
    return np.stack(channels, axis=1).astype(samples.dtype)


def _gate(x: np.ndarray, size: int, floor: float) -> np.ndarray:
    """One channel through a spectral gate: STFT, scale each bin by its gain, overlap-add."""
    n = len(x)
    if n < size:
        return x.copy()  # shorter than one frame: too little to tell noise from signal
    hop = size // 4
    window = np.hanning(size + 1)[:-1].astype(np.float32)  # periodic Hann
    padded = np.pad(x.astype(np.float32), (size, size + hop))  # every sample in 4 frames
    frames = sliding_window_view(padded, size)[::hop]
    # Frames that lie fully inside the clip, so the zero padding doesn't read as quiet noise.
    inside = np.arange(len(frames)) * hop
    inside = np.flatnonzero((inside >= size) & (inside + size <= size + n))
    noise = _noise_profile(frames, inside, window)
    out = np.zeros(len(padded), dtype=np.float32)
    for first in range(0, len(frames), _BATCH_FRAMES):
        batch = frames[first : first + _BATCH_FRAMES]
        spec = np.fft.rfft(batch * window, axis=1)
        mag = np.abs(spec)
        gain = np.clip(1 - _OVERSUBTRACT * noise / np.maximum(mag, _SILENT), floor, 1)
        chunk = np.fft.irfft(spec * gain, n=size, axis=1).astype(np.float32) * window
        for i, frame in enumerate(chunk):
            start = (first + i) * hop
            out[start : start + size] += frame
    # Periodic Hann squared at 75% overlap sums to 1.5 everywhere the frames cover.
    return out[size : size + n] / 1.5


def _noise_profile(frames: np.ndarray, inside: np.ndarray, window: np.ndarray) -> np.ndarray:
    """Per frequency bin, the level the quieter frames sit at: what the gate treats as noise."""
    picks = inside[np.linspace(0, len(inside) - 1, min(len(inside), _MAX_NOISE_FRAMES)).astype(int)]
    mag = np.abs(np.fft.rfft(frames[picks] * window, axis=1))
    loud = mag[(mag**2).mean(axis=1) > _SILENT**2]
    if len(loud) == 0:
        return np.zeros(mag.shape[1], dtype=np.float32)  # all silence: nothing to remove
    return np.percentile(loud, _NOISE_PERCENTILE, axis=0).astype(np.float32)
