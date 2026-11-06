"""The audio side of a render: silence the muted spans, then remove the cut ones.

Works on samples from ``vibe.video.extract_audio``, where sample 0 is at source time 0, so a
span's source times map straight to sample numbers. Video frames are dropped over the same
source ranges, so picture and sound stay in sync. Pure, like the per-frame path.

The sound fades out over ``FADE_SECONDS`` before each mute and each cut, and back in after, so
an edit doesn't click. The fades sit outside the edited range: a muted span stays exactly
silent, and the samples a cut keeps are the same ones.
"""

import numpy as np

from vibe.contract import TimelineState
from vibe.render.errors import UnsupportedEdit

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
    if state.audio_filters:
        filters = sorted({f.filter for f in state.audio_filters})
        raise UnsupportedEdit(
            f"the timeline has audio filters ({', '.join(filters)}), and the renderer doesn't "
            "apply them yet"
        )


def render_audio(samples: np.ndarray, sample_rate: int, state: TimelineState) -> np.ndarray:
    """A whole clip's ``samples`` ((n,) or (n, channels)) with the mutes silenced and the cuts
    removed. Returns a new array; ``samples`` is left as it was."""
    check_audio(state)
    return apply_audio_edits(samples, sample_rate, state)


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
        scaled = out * (gain if out.ndim == 1 else gain[:, None])
        out = scaled.astype(out.dtype, copy=False)
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
    fade exactly like the whole clip rendered at once. None when nothing in the chunk fades.
    """
    fade = round(FADE_SECONDS * sample_rate)
    if fade == 0 or not edges:
        return None
    index = np.arange(first_sample, first_sample + n, dtype=np.float64)
    gain = np.ones(n, dtype=np.float64)
    touched = False
    for start, end in edges:
        first = round(start * sample_rate)
        last = None if end is None else round(end * sample_rate)
        if first - fade >= first_sample + n or (last is not None and last + fade <= first_sample):
            continue  # this range and its fades are outside the chunk
        # How far each sample is from the range, in samples: 1 right next to it, <= 0 inside.
        before = first - index
        after = index - last + 1 if last is not None else np.full(n, -np.inf)
        distance = np.maximum(before, after)
        gain = np.minimum(gain, np.clip(distance / (fade + 1), 0.0, 1.0))
        touched = True
    return gain if touched else None
