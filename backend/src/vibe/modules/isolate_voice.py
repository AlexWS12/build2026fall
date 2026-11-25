"""Voice isolation (F-34): keep the voices, turn down everything else (fans, finger taps,
keyboard clicks, bumps), as in "remove the tapping and the fans, keep the voices".

``denoise`` (F-33) only knows steady noise. Isolation adds what changes over time.

Backends (``VIBE_BACKEND``, docs/ARCHITECTURE.md "Compute"):
- ``stub``: numpy, no model and no GPU, deterministic, so CI and CPU laptops get real (if
  modest) isolation. Per STFT frame and frequency bin, the gain is the product of:
  1. the F-33 noise gate (``gate_gain``), for steady noise such as fans and hum;
  2. a click gate. A tap or click is short and broadband: a vertical line in a spectrogram.
     Voiced speech is a stack of harmonics that last: horizontal lines. So, as in
     harmonic/percussive separation (median filtering, Fitzgerald 2010), each bin compares
     its median across ``TIME_MEDIAN_SECONDS`` (high for lasting sound) with its median
     across ``FREQ_MEDIAN_HZ`` (high for broadband sound). A bin that's more than
     ``CLICK_MARGIN`` times more broadband than lasting is turned down;
  3. a high-pass below ``HIGH_PASS_HZ``, under any voice (rumble, desk thumps).
  It doesn't tell voices from other voices, so voices in the background stay; that's phase B
  (F-32's speakers plus target-speaker extraction). Music with a steady beat partly stays too.
- ``local`` / ``remote``: a speech enhancement model (still open). They raise until one lands,
  rather than quietly handing back the noisy audio.

The renderer streams it like ``denoise``: pass 1 is the same cached ``NoiseProfile``, pass 2 is
``VoiceIsolator``, which has ``NoiseGate``'s ``push`` / ``finish`` shape. The click gate needs to
see ``TIME_MEDIAN_SECONDS / 2`` ahead, so output lags input by that much more than the gate.

Live mode gets ``isolate_voice_chunk(chunk, state, strength)``: the shape is fixed here, but it
raises until it has a running noise estimate, like ``denoise_chunk``.
"""

from dataclasses import dataclass, field

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from vibe.config import Backend, model_backend
from vibe.modules.denoise import (
    MAX_ATTENUATION_DB,
    NoiseProfile,
    frame_size,
    gate_gain,
    learn_noise,
)

TIME_MEDIAN_SECONDS = 0.2
"""How long a sound must last to count as kept sound (a voice) rather than a click. Longer than a
tap (~5-30 ms), shorter than a vowel. Unmeasured starting value: tune it on real clips."""

FREQ_MEDIAN_HZ = 400.0
"""How wide a band a bin is compared with to tell broadband (a click) from a harmonic."""

CLICK_MARGIN = 2.0
"""How many times more broadband than lasting a bin must be before the click gate touches it, so
sustained broadband speech ("s", "sh") and steady noise are left to the noise gate."""

HIGH_PASS_HZ = 70.0
"""Below the lowest voices; what's under this is rumble and thumps."""

_FLOOR = 10 ** (-MAX_ATTENUATION_DB / 20)
_OLA_GAIN = 1.5  # periodic Hann squared at 75% overlap sums to this everywhere
_BATCH_FRAMES = 256  # frames filtered at once; the time median holds batch x bins x window
_TINY = 1e-12


class IsolateVoiceError(Exception):
    """The backend can't isolate voices (not built, model missing, server down)."""


def check_backend(backend: Backend | None = None) -> None:
    """Raise ``IsolateVoiceError`` unless ``backend`` (default ``VIBE_BACKEND``) can isolate."""
    backend = backend or model_backend()
    if backend != "stub":
        raise IsolateVoiceError(
            f"the {backend!r} voice isolation backend isn't built yet (F-34); set VIBE_BACKEND=stub"
        )


class VoiceIsolator:
    """Pass 2: the stub's isolation over a stream, one chunk at a time.

    Same contract as ``NoiseGate``: ``push`` returns the samples that are now final, in order,
    and with ``finish`` they're exactly as many as went in, lined up with them. Any chunk sizes
    give the same samples as the whole clip at once.
    """

    def __init__(
        self, profile: NoiseProfile, strength: float | None = None, dtype: object = np.float32
    ) -> None:
        self._strength = _check_strength(strength)
        self._channels = [
            _ChannelIsolator(profile, c, self._strength, np.dtype(dtype))
            for c in range(profile.channels)
        ]
        self._mono: bool | None = None

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
        return self._out([c.push(data[:, i]) for i, c in enumerate(self._channels)])

    def finish(self) -> np.ndarray:
        """The rest of the stream, once the last chunk is in."""
        return self._out([c.finish() for c in self._channels])

    def _out(self, parts: list[np.ndarray]) -> np.ndarray:
        mono = self._mono if self._mono is not None else len(parts) == 1
        return parts[0] if mono else np.stack(parts, axis=1)


class _ChannelIsolator:
    """One channel's stream. Framing is ``NoiseGate``'s (``size`` samples every ``size // 4``,
    the stream padded with ``size`` zeros in front), split in two stages: frames are analysed as
    soon as their samples are in, and filtered once ``look`` later frames are analysed too,
    since the click gate's time median looks that far ahead (and as far back)."""

    def __init__(self, profile: NoiseProfile, channel: int, strength: float, dtype: np.dtype):
        self._profile, self._channel, self._strength = profile, channel, strength
        self._work = np.result_type(dtype, np.float32)
        self._size = size = profile.size
        self._hop = hop = size // 4
        rate = profile.sample_rate
        self._look = max(1, round(TIME_MEDIAN_SECONDS / 2 * rate / hop))
        self._wide = max(1, round(FREQ_MEDIAN_HZ / 2 / (rate / size)))
        bins = size // 2 + 1
        self._band = (np.arange(bins) * rate / size >= HIGH_PASS_HZ).astype(self._work)
        self._band[self._band == 0] = _FLOOR
        self._hann = np.hanning(size + 1)[:-1].astype(self._work)
        self._pending = np.zeros(size, dtype=self._work)  # starts at the next frame to analyse
        self._specs = np.zeros((0, bins), dtype=np.result_type(self._work, np.complex64))
        self._past = np.zeros((0, bins), dtype=self._work)  # last `look` filtered magnitudes
        self._fresh = True  # no frame filtered yet
        self._out_base = 0  # padded position of the next frame to filter (= _specs[0])
        self._tail = np.zeros(0, dtype=self._work)  # overlap sums from _out_base on
        self._skip = size  # leading padding not to emit
        self._taken = 0  # real samples in
        self._given = 0  # samples out

    def push(self, x: np.ndarray) -> np.ndarray:
        self._taken += len(x)
        self._pending = np.concatenate([self._pending, x.astype(self._work)])
        self._analyse()
        return self._filter(final=False)

    def finish(self) -> np.ndarray:
        # The same padding a whole clip gets: every real sample ends up in 4 frames.
        self._pending = np.concatenate(
            [self._pending, np.zeros(self._size + self._hop, dtype=self._work)]
        )
        self._analyse()
        return self._filter(final=True)

    def _analyse(self) -> None:
        size, hop = self._size, self._hop
        if len(self._pending) < size:
            return
        count = (len(self._pending) - size) // hop + 1
        frames = sliding_window_view(self._pending[: (count - 1) * hop + size], size)[::hop]
        spec = np.fft.rfft(frames * self._hann, axis=1).astype(self._specs.dtype)
        self._specs = np.concatenate([self._specs, spec])
        self._pending = self._pending[count * hop :]

    def _filter(self, final: bool) -> np.ndarray:
        size, hop, look = self._size, self._hop, self._look
        ready = len(self._specs) if final else len(self._specs) - look
        if ready <= 0:
            return np.zeros(0, dtype=self._work)
        mags = np.abs(self._specs).astype(self._work)
        if self._fresh:  # the stream's first frame stands in for the frames before it
            self._past = np.repeat(mags[:1], look, axis=0)
            self._fresh = False
        # Frame j's time window is rows j .. j + 2 * look of ``lined``: the last ``look``
        # filtered frames in front, and at the end of the stream the last frame repeated, so
        # the end isn't judged against silence.
        parts = [self._past, mags]
        if final:
            parts.append(np.repeat(mags[-1:], look, axis=0))
        lined = np.concatenate(parts)

        acc = np.zeros((ready + 3) * hop, dtype=self._work)
        acc[: len(self._tail)] += self._tail
        blocks = acc.reshape(-1, hop)
        for first in range(0, ready, _BATCH_FRAMES):
            n = min(_BATCH_FRAMES, ready - first)
            mag = mags[first : first + n]
            windows = sliding_window_view(lined[first : first + n + 2 * look], 2 * look + 1, axis=0)
            lasting = np.median(windows, axis=-1)
            spread = np.pad(mag, ((0, 0), (self._wide, self._wide)), mode="edge")
            broad = np.median(sliding_window_view(spread, 2 * self._wide + 1, axis=1), axis=-1)
            starts = self._out_base + (first + np.arange(n)) * hop
            noise = self._profile.at(starts - size // 2, self._channel).astype(self._work)
            full = gate_gain(mag, noise) * click_gain(lasting, broad) * self._band
            full = np.clip(full, _FLOOR, 1)
            gain = 1 - self._strength * (1 - full)  # strength blends untouched (0) to full (1)
            spec = self._specs[first : first + n] * gain
            out = (np.fft.irfft(spec, n=size, axis=1) * self._hann).astype(self._work)
            out = out.reshape(n, 4, hop)
            for j in range(4):
                blocks[first + j : first + j + n] += out[:, j]
        # Later frames start at or after the next frame to filter: positions before it are final.
        done = acc[: ready * hop] / _OLA_GAIN
        self._tail = acc[ready * hop :]
        self._past = np.concatenate([self._past, mags[:ready]])[-look:]
        self._specs = self._specs[ready:]
        self._out_base += ready * hop
        skip = min(self._skip, len(done))
        self._skip -= skip
        done = done[skip:][: max(0, self._taken - self._given)]  # not the trailing padding
        self._given += len(done)
        return done


def click_gain(lasting: np.ndarray, broad: np.ndarray) -> np.ndarray:
    """Per bin, the click gate's gain: 1 unless the bin's across-frequency median (``broad``)
    is more than ``CLICK_MARGIN`` times its across-time median (``lasting``), then falling off
    with the square of the ratio, down to the floor."""
    ratio = CLICK_MARGIN * lasting / np.maximum(broad, _TINY)
    return np.clip(ratio * ratio, _FLOOR, 1)


# --- Whole clip and live ---------------------------------------------------------------------


@dataclass
class IsolateVoiceState:
    """What a live stream carries from one chunk to the next: the running noise estimate, the
    recent frames the click gate compares with, and the overlap-add tail. They start empty
    and belong to the backend."""

    sample_rate: int
    channels: int = 1
    noise: np.ndarray | None = field(default=None, repr=False)
    recent: np.ndarray | None = field(default=None, repr=False)
    tail: np.ndarray | None = field(default=None, repr=False)


def isolate_voice(
    samples: np.ndarray,
    sample_rate: int,
    strength: float | None = None,
    backend: Backend | None = None,
) -> np.ndarray:
    """``samples`` ((n,) or (n, channels), floating point in [-1, 1]) with the voices kept and
    the rest turned down.

    ``strength`` goes from 0 (unchanged) to 1 (strongest); ``None`` means 1, as in the
    contract's ``add_audio_filter``. Returns a new array with the same shape, rate and dtype.
    Integer PCM is rejected: convert it to float first. The same two passes as the renderer
    (``learn_noise``, then ``VoiceIsolator``), on one chunk.
    """
    strength = _check(samples, sample_rate, strength)
    check_backend(backend)
    if strength == 0 or len(samples) < frame_size(sample_rate):
        return samples.copy()  # nothing to do, or too little to tell
    profile = learn_noise([samples], sample_rate)
    isolator = VoiceIsolator(profile, strength, samples.dtype)
    out = np.concatenate([isolator.push(samples), isolator.finish()])
    return out.astype(samples.dtype)


def isolate_voice_chunk(
    chunk: np.ndarray,
    state: IsolateVoiceState,
    strength: float | None = None,
    backend: Backend | None = None,
) -> np.ndarray:
    """Live mode: one chunk of a stream ((n,) or (n, channels)), isolated, updating ``state``.

    The output lags the input (overlap-add plus the click gate's look-ahead), so it can be
    shorter than the chunk at the start of a stream. Never calls the network. Not built yet on
    any backend: it raises ``IsolateVoiceError`` after checking its arguments, rather than
    passing the noise through. ``VoiceIsolator`` already streams; what's missing is a running
    noise estimate to feed it.
    """
    _check(chunk, state.sample_rate, strength)
    channels = 1 if chunk.ndim == 1 else chunk.shape[1]
    if channels != state.channels:
        raise ValueError(f"chunk has {channels} channels, the stream has {state.channels}")
    backend = backend or model_backend()
    raise IsolateVoiceError(
        f"per-chunk voice isolation isn't built yet on the {backend!r} backend (F-34, live mode)"
    )


def _check_strength(strength: float | None) -> float:
    if strength is None:
        return 1.0
    if not 0 <= strength <= 1:
        raise ValueError(f"strength must be between 0 and 1, got {strength}")
    return float(strength)


def _check(samples: np.ndarray, sample_rate: int, strength: float | None) -> float:
    """The strength to use (``None`` is 1), after rejecting arguments it can't take."""
    strength = _check_strength(strength)
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be positive, got {sample_rate}")
    if samples.ndim not in (1, 2):
        raise ValueError(f"samples must be (n,) or (n, channels), got shape {samples.shape}")
    if not np.issubdtype(samples.dtype, np.floating):
        raise ValueError(f"samples must be floating point in [-1, 1], got {samples.dtype}")
    return strength
