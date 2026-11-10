"""The audio side of a render: silence the muted spans, then remove the cut ones.

Works on samples from ``vibe.video.extract_audio``, where sample 0 is at source time 0, so a
span's source times map straight to sample numbers. Video frames are dropped over the same
source ranges, so picture and sound stay in sync. Pure, like the per-frame path.
"""

import numpy as np

from vibe.contract import TimelineState
from vibe.render.errors import UnsupportedEdit


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
