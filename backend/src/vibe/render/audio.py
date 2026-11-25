"""The audio side of a render: filter the filtered spans (denoise, isolate voice), silence the
muted ones, then remove the cut ones.

Works on samples from ``vibe.video.extract_audio``, where sample 0 is at source time 0, so a
span's source times map straight to sample numbers. Video frames are dropped over the same
source ranges, so picture and sound stay in sync. Pure, like the per-frame path.

The sound fades out over ``FADE_SECONDS`` before each mute and each cut, and back in after, so
an edit doesn't click. The fades sit outside the edited range: a muted span stays exactly
silent, and the samples a cut keeps are the same ones.

Audio filters run on the source sound first, through ``FilterMix``: a stream, so an export
never holds the whole clip. Each filter (denoise F-33b, isolate voice F-34) runs over the whole
stream and is mixed in over its spans at their strength, with the same ``FADE_SECONDS`` ramps
outside the span. Filters are chained in ``FILTER_ORDER``, so where a denoise span and an
isolate-voice span overlap, the voice is isolated from already-denoised sound.
"""

from collections.abc import Callable

import numpy as np

from vibe.contract import AudioFilterSpan, TimelineState
from vibe.modules.denoise import NoiseGate, NoiseProfile, learn_noise
from vibe.modules.isolate_voice import VoiceIsolator
from vibe.render.errors import UnsupportedEdit

FILTER_ORDER = ("denoise", "isolate_voice")
"""The audio filters this renderer applies, in the order they're chained."""

_PROCESSORS = {"denoise": NoiseGate, "isolate_voice": VoiceIsolator}

FADE_SECONDS = 0.01
"""How long the sound fades out before a mute or a cut, and back in after. 10 ms is short
enough not to hear as a fade, and long enough to stop the click of a hard edge."""


def check_audio(state: TimelineState) -> None:
    """Raise ``UnsupportedEdit`` for audio edits this renderer can't apply yet."""
    speakers = sorted({m.speaker for m in state.mutes if m.speaker is not None})
    if speakers:
        raise UnsupportedEdit(
            f"the timeline mutes one speaker ({', '.join(speakers)}); that needs to know who "
            "speaks when, and comes with F-32"
        )
    unknown = sorted({f.filter for f in state.audio_filters} - set(FILTER_ORDER))
    if unknown:
        raise UnsupportedEdit(
            f"the timeline has audio filters ({', '.join(unknown)}) the renderer doesn't apply yet"
        )


def filter_spans(state: TimelineState, name: str | None = None) -> list[AudioFilterSpan]:
    """The timeline's audio filter spans: all of them, or only filter ``name``'s."""
    return [f for f in state.audio_filters if name is None or f.filter == name]


def render_audio(samples: np.ndarray, sample_rate: int, state: TimelineState) -> np.ndarray:
    """A whole clip's ``samples`` ((n,) or (n, channels)) filtered, with the mutes silenced and
    the cuts removed. Returns a new array; ``samples`` is left as it was. The filters learn
    their noise profile from ``samples`` themselves."""
    check_audio(state)
    if filter_spans(state):
        mix = FilterMix(learn_noise([samples], sample_rate), state, samples.dtype)
        samples = np.concatenate([mix.push(samples), mix.finish()])
    return apply_audio_edits(samples, sample_rate, state)


class FilterMix:
    """Every audio filter's spans over a stream of source sound, one chunk at a time.

    Like ``NoiseGate``, ``push`` returns the samples that are final, lined up with the input
    (sample ``i`` out is source sample ``i``), and ``finish`` the rest. Outside every span the
    output is the input exactly. One stage per filter with spans, chained in ``FILTER_ORDER``.
    """

    def __init__(self, profile: NoiseProfile, state: TimelineState, dtype: object) -> None:
        check_audio(state)
        self._stages = []
        for name in FILTER_ORDER:
            spans = filter_spans(state, name)
            if spans:
                processor = _PROCESSORS[name](profile, 1.0, dtype)
                self._stages.append(_FilterStage(processor, spans, profile.sample_rate))

    def push(self, chunk: np.ndarray) -> np.ndarray:
        for stage in self._stages:
            chunk = stage.push(chunk)
        return chunk

    def finish(self) -> np.ndarray:
        rest: np.ndarray | None = None
        for stage in self._stages:
            parts = [stage.push(rest)] if rest is not None and len(rest) else []
            parts.append(stage.finish())
            rest = np.concatenate(parts)
        return rest if rest is not None else np.zeros(0)


class _FilterStage:
    """One filter's spans: its processor runs over the whole stream, and the processed sound is
    blended in over each span at the span's strength (the strongest where spans overlap), which
    is what the module's whole-clip call does at ``strength=s``."""

    def __init__(
        self, processor: NoiseGate | VoiceIsolator, spans: list[AudioFilterSpan], rate: int
    ) -> None:
        self._rate = rate
        self._spans = [(s.start, s.end, 1.0 if s.strength is None else s.strength) for s in spans]
        self._gate = processor
        self._dry: list[np.ndarray] = []  # input not yet matched by gate output
        self._first = 0  # source sample number of the next output sample

    def push(self, chunk: np.ndarray) -> np.ndarray:
        self._dry.append(chunk)
        return self._mix(self._gate.push(chunk))

    def finish(self) -> np.ndarray:
        return self._mix(self._gate.finish())

    def _mix(self, wet: np.ndarray) -> np.ndarray:
        n = len(wet)
        dry = np.concatenate(self._dry) if self._dry else wet[:0]
        self._dry = [dry[n:]]
        dry = dry[:n]
        weight = self._weights(n)
        self._first += n
        if weight is None:
            return dry.copy()
        w = weight if dry.ndim == 1 else weight[:, None]
        return (dry + w * (wet - dry)).astype(dry.dtype)

    def _weights(self, n: int) -> np.ndarray | None:
        """Per sample, how much of the gated sound to use: the span's strength inside, ramping
        down to 0 over ``FADE_SECONDS`` after each edge. None when no span reaches the chunk."""
        fade = round(FADE_SECONDS * self._rate)
        lo, hi = self._first, self._first + n
        weight: np.ndarray | None = None
        for start, end, strength in self._spans:
            first = round(start * self._rate)
            last = hi + fade if end is None else round(end * self._rate)
            if last + fade <= lo or first - fade >= hi:
                continue
            i = np.arange(lo, hi, dtype=np.float64)
            distance = np.maximum(np.maximum(first - i, i - last + 1), 0)
            w = strength * np.clip(1 - distance / (fade + 1), 0, 1)
            weight = w if weight is None else np.maximum(weight, w)
        return weight


def apply_audio_edits(
    samples: np.ndarray, sample_rate: int, state: TimelineState, first_sample: int = 0
) -> np.ndarray:
    """``render_audio`` for one chunk of a clip, once ``check_audio`` has passed.

    ``first_sample`` is the clip's sample number of ``samples[0]``, so chunks rendered one at a
    time come out the same as the whole clip rendered at once.
    """
    out = samples.copy()
    n = len(out)
    for mute in state.mutes:
        start, end = _samples(mute.start, mute.end, sample_rate, first_sample, n)
        out[start:end] = 0
    edges = [(span.start, span.end) for span in (*state.mutes, *state.cuts)]
    gain = _fades(edges, sample_rate, first_sample, n)
    if gain is not None:
        np.multiply(out, gain if out.ndim == 1 else gain[:, None], out=out, casting="unsafe")
    keep = np.ones(n, dtype=bool)
    for cut in state.cuts:
        start, end = _samples(cut.start, cut.end, sample_rate, first_sample, n)
        keep[start:end] = False
    return out[keep]


def _samples(
    start: float, end: float | None, sample_rate: int, first_sample: int, n: int
) -> tuple[int, int]:
    """Source seconds ``[start, end)`` as positions in a chunk of ``n`` samples, clamped to it."""
    first = round(start * sample_rate) - first_sample
    last = n if end is None else round(end * sample_rate) - first_sample
    return min(n, max(0, first)), min(n, max(0, last))


def _fades(
    edges: list[tuple[float, float | None]], sample_rate: int, first_sample: int, n: int
) -> np.ndarray | None:
    """Per-sample gain for a chunk: a linear fade down to each edited range and back up after.

    Worked out from each sample's number in the whole clip, so chunks rendered one at a time
    fade exactly like the whole clip rendered at once. Each edge only touches its own ramps
    and range (sliced to the chunk), never the whole chunk. None when nothing in the chunk
    fades.
    """
    fade = round(FADE_SECONDS * sample_rate)
    if fade == 0 or not edges:
        return None
    chunk_end = first_sample + n
    gain: np.ndarray | None = None

    def ramp(lo: int, hi: int, value: Callable[[np.ndarray], np.ndarray]) -> None:
        """Lower the gain of clip samples ``[lo, hi)`` (clamped to the chunk) to ``value``."""
        nonlocal gain
        lo, hi = max(lo, first_sample), min(hi, chunk_end)
        if lo >= hi:
            return
        if gain is None:
            gain = np.ones(n, dtype=np.float64)
        window = gain[lo - first_sample : hi - first_sample]
        np.minimum(window, value(np.arange(lo, hi, dtype=np.float64)), out=window)

    for start, end in edges:
        first = round(start * sample_rate)
        last = chunk_end if end is None else round(end * sample_rate)
        # Gain is the distance to the range in samples over fade + 1: 1 right next to it.
        ramp(first - fade, first, lambda i, first=first: (first - i) / (fade + 1))
        ramp(first, last, np.zeros_like)
        if end is not None:
            ramp(last, last + fade, lambda i, last=last: (i - last + 1) / (fade + 1))
    return gain
