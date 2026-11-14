"""Targeting by description (F-13): which tracks match "in red", "wearing a hat"?

    tracks + video ──► a few crops per track ──► judge: yes / no / unsure per track ──► cache
                                         toolbox ``find_by_description`` ◄──┘

The planner never sees pixels (D-017). It names a description; a :class:`Judge` on this machine
looks at crops of each track and answers ``yes``, ``no`` or ``unsure``. ``unsure`` is a real
answer, not a failure: the toolbox masks unsure tracks (privacy fails closed).

Judges, by backend (``VIBE_BACKEND``, docs/ARCHITECTURE.md "Compute"):

- ``stub``: :class:`ColorJudge`, numpy only. It answers clothing-colour descriptions ("in red",
  "wearing a blue shirt") from the pixels and says ``unsure`` to everything else, so CI and CPU
  laptops get a real, if narrow, answer. Its thresholds are starting points, not measured.
- ``local`` / ``remote``: a VLM. Which one is still open, so they raise until it lands instead
  of quietly answering with the colour stub.

Verdicts are cached per video under ``cache/<sha256>/attributes/``, one file per judge and
description, so asking again costs nothing. Crops are never written anywhere.
"""

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ClassVar, Literal

import numpy as np

from vibe.config import Backend, model_backend
from vibe.contract import Track
from vibe.modules.cache import ModuleError, cache_key, read_json, settings_digest, write_json
from vibe.storage import Project, Storage
from vibe.toolbox.context import DescriberError
from vibe.video import VideoError, VideoReader

Verdict = Literal["yes", "no", "unsure"]
"""Same three values as ``vibe.toolbox.context.Verdict``."""

KIND = "attributes"
"""Cache folder. Not biometric: a verdict is "matches 'in red'", never who someone is."""

DEFAULT_SAMPLES = 3
"""Crops looked at per track. More costs a judge call each; a VLM makes this matter."""

_MIN_SIDE = 4  # pixels; a smaller crop holds nothing a judge can use


class DescribeError(ModuleError, DescriberError):
    """The description can't be checked: no judge on this backend, or the video can't be read.

    Callers must treat it as "unknown", never as "nobody matches"."""


class Judge(ABC):
    """Answers one question about one track from a few crops of it.

    ``settings`` must include everything that changes the answers: they're hashed into the
    cache key, with ``version``."""

    name: ClassVar[str]
    version: ClassVar[int] = 1

    def __init__(self, **settings: object) -> None:
        self.settings: dict[str, object] = dict(settings)

    @abstractmethod
    def judge(self, crops: Sequence[np.ndarray], label: str, description: str) -> Verdict:
        """Does the thing in ``crops`` (RGB uint8, one per sampled keyframe, all of the same
        ``label`` track) match ``description``? Answer ``unsure`` rather than guess."""


# --- the stub judge: clothing colour ------------------------------------------------------

_HUES: Mapping[str, tuple[tuple[float, float], ...]] = {
    # degrees on the colour wheel; red wraps around 0
    "red": ((345, 360), (0, 15)),
    "orange": ((15, 40),),
    "yellow": ((40, 70),),
    "green": ((70, 165),),
    "blue": ((190, 260),),
    "purple": ((260, 300),),
    "pink": ((300, 345),),
}
_GREYS = ("white", "black", "grey")
_ALIASES = {"gray": "grey", "violet": "purple"}
COLORS = frozenset({*_HUES, *_GREYS})

_FILLER = frozenset(
    "in a an the with wearing wears dressed who is are that s has have on of and "
    "clothes clothing outfit shirt t-shirt tshirt tee top jacket hoodie sweater jumper coat "
    "dress colored coloured".split()
)
"""Words the colour stub may ignore: connectors and upper-body clothing, which is where it
looks. Any other word ("hat", "tall", "pants") is something it can't check, so it's unsure."""

_WORD = re.compile(r"[a-z][a-z-]*|\d+")


def _hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Hue in degrees, saturation and value in 0..1, for an (h, w, 3) uint8 image."""
    x = rgb.astype(np.float32) / 255.0
    v = x.max(axis=2)
    spread = v - x.min(axis=2)
    s = np.where(v > 0, spread / np.maximum(v, 1e-6), 0.0)
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    safe = np.maximum(spread, 1e-6)
    h = np.where(
        v == r, (g - b) / safe % 6, np.where(v == g, (b - r) / safe + 2, (r - g) / safe + 4)
    )
    return np.where(spread > 0, h * 60.0, 0.0), s, v


def color_fraction(rgb: np.ndarray, color: str) -> float:
    """The share of pixels in ``rgb`` that are ``color`` (a name in ``COLORS``)."""
    h, s, v = _hsv(rgb)
    if color == "black":
        mask = v < 0.2
    elif color == "white":
        mask = (s < 0.15) & (v > 0.8)
    elif color == "grey":
        mask = (s < 0.15) & (v >= 0.2) & (v <= 0.8)
    else:
        hue = np.zeros(h.shape, dtype=bool)
        for low, high in _HUES[color]:
            hue |= (h >= low) & (h < high)
        mask = hue & (s >= 0.4) & (v >= 0.25)
    return float(mask.mean())


def _middle(crop: np.ndarray, label: str) -> np.ndarray:
    """Where the stub looks: the chest of a person box, the middle of anything else, so the
    background around the target and a person's face and legs count less."""
    height, width = crop.shape[:2]
    y1, y2 = (0.2, 0.6) if label == "person" else (0.25, 0.75)
    return crop[int(height * y1) : max(int(height * y2), 1), int(width * 0.25) : int(width * 0.75)]


class ColorJudge(Judge):
    """The stub: is this track's clothing (or, for other labels, the thing itself) a colour?

    Each crop votes: ``yes`` when at least ``yes_at`` of its middle is that colour, ``no`` at
    ``no_at`` or less, ``unsure`` in between. The track gets a verdict only if every crop agrees;
    otherwise, and when the description says anything besides one colour, it is ``unsure``. The
    gaps are deliberate: a wrong ``no`` leaves someone unmasked.
    """

    name = "color"
    version = 2  # 2: every crop must agree (1 took the median, which could turn doubt into a no)

    def __init__(self, yes_at: float = 0.35, no_at: float = 0.08) -> None:
        if not 0 <= no_at < yes_at <= 1:
            raise ValueError(f"need 0 <= no_at < yes_at <= 1, got {no_at} and {yes_at}")
        super().__init__(yes_at=yes_at, no_at=no_at)
        self.yes_at, self.no_at = yes_at, no_at

    def judge(self, crops: Sequence[np.ndarray], label: str, description: str) -> Verdict:
        color = self.color_in(description)
        if color is None or not crops:
            return "unsure"
        # TODO(F-13): known fail-open cases, see the handoff "Improvements" list: a confident
        # "no" for navy / burgundy / pastel / dark / teal, a confident "yes" on skin tones, and a
        # sure verdict from a single usable crop (should need 2+).
        shares = [color_fraction(_middle(c, label), color) for c in crops]
        votes = {
            "yes" if s >= self.yes_at else "no" if s <= self.no_at else "unsure" for s in shares
        }
        # Every crop must agree. A track that is red in some crops and not in others may be two
        # people, or one partly hidden: calling it "no" would leave the red part unmasked.
        return votes.pop() if len(votes) == 1 else "unsure"

    @staticmethod
    def color_in(description: str) -> str | None:
        """The one colour ``description`` asks about, or ``None`` if it asks anything else."""
        words = [_ALIASES.get(w, w) for w in _WORD.findall(description.lower())]
        colors = {w for w in words if w in COLORS}
        rest = [w for w in words if w not in COLORS and w not in _FILLER]
        return next(iter(colors)) if len(colors) == 1 and not rest else None


def make_judge(backend: Backend | None = None) -> Judge:
    """The judge for ``backend`` (default ``VIBE_BACKEND``)."""
    backend = backend or model_backend()
    if backend == "stub":
        return ColorJudge()
    raise DescribeError(
        f"the {backend!r} description judge (a VLM) isn't built yet (F-13); set VIBE_BACKEND=stub "
        "for clothing colours only"
    )


# --- crops --------------------------------------------------------------------------------


def sample_keyframes(track: Track, samples: int = DEFAULT_SAMPLES) -> list[int]:
    """Up to ``samples`` keyframe indexes, spread over the track. Keyframes a model saw or a
    person drew come first; ``uncertain`` and ``tracked`` ones are used only if there are no
    others, since their boxes may not hold the target."""
    if samples < 1:
        raise ValueError(f"samples must be at least 1, got {samples}")
    sure = [i for i, k in enumerate(track.keyframes) if k.state in ("detected", "user")]
    pool = sure or list(range(len(track.keyframes)))
    if len(pool) <= samples:
        return pool
    # The middle of each of `samples` equal stretches: skips the first and last keyframes,
    # where a target is often entering the frame and half cut off.
    return [pool[int((j + 0.5) * len(pool) / samples)] for j in range(samples)]


def _crop(image: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray | None:
    height, width = image.shape[:2]
    x1, y1 = int(box[0] * width), int(box[1] * height)
    x2, y2 = int(np.ceil(box[2] * width)), int(np.ceil(box[3] * height))
    if x2 - x1 < _MIN_SIDE or y2 - y1 < _MIN_SIDE:
        return None
    return image[y1:y2, x1:x2]


def _signature(track: Track, picks: Sequence[int]) -> str:
    """Changes when the crops would: a hand-corrected box under the same id is asked again."""
    frames = [(track.keyframes[i].t, track.keyframes[i].box) for i in picks]
    return settings_digest({"label": track.label, "frames": frames})


def describe_tracks(
    video: str | Path,
    tracks: Sequence[Track],
    description: str,
    judge: Judge,
    samples: int = DEFAULT_SAMPLES,
) -> dict[str, Verdict]:
    """A verdict for every track in ``tracks``: does it match ``description``?

    Reads each needed frame once, in order. A track with no usable crop is ``unsure``.
    """
    picks = {t.track_id: sample_keyframes(t, samples) for t in tracks}
    crops: dict[str, list[np.ndarray]] = {t.track_id: [] for t in tracks}
    try:
        with VideoReader(video) as reader:
            wanted: dict[int, list[tuple[str, tuple[float, float, float, float]]]] = {}
            last = len(reader) - 1
            for track in tracks:
                for i in picks[track.track_id]:
                    key = track.keyframes[i]
                    # Tracks from a longer cut of the video would point past the end: clamp.
                    # TODO(F-13): the last frame isn't where the target was, so a keyframe past
                    # the end should give no crop (unsure), not a sure verdict.
                    frame = reader.index.frame_at(key.t) if key.t < reader.index.end else last
                    wanted.setdefault(frame, []).append((track.track_id, key.box))
            for frame in sorted(wanted):
                image = reader.read(frame).image
                for track_id, box in wanted[frame]:
                    crop = _crop(image, box)
                    if crop is not None:
                        crops[track_id].append(crop)
    except (VideoError, OSError) as e:
        raise DescribeError(f"can't read {video} to check {description!r}: {e}") from e
    return {t.track_id: judge.judge(crops[t.track_id], t.label, description) for t in tracks}


# --- cached, per video --------------------------------------------------------------------


def normalize(description: str) -> str:
    return " ".join(description.lower().split())


class VideoDescriber:
    """Answers the toolbox's description questions for one video, through the cache.

    This is what goes in ``ToolContext.describer``. ``video`` is the file to read crops from and
    ``sha256`` its content hash (the cache key), as in the rest of the vision cache.
    """

    def __init__(
        self,
        storage: Storage,
        sha256: str,
        video: str | Path,
        judge: Judge | None = None,
        samples: int = DEFAULT_SAMPLES,
    ) -> None:
        self.storage, self.sha256, self.video = storage, sha256, Path(video)
        self._judge, self.samples = judge, samples

    @property
    def judge(self) -> Judge:
        """Built on first use, so a describer can be handed out on a backend with no judge yet
        and only a description request fails."""
        if self._judge is None:
            self._judge = make_judge()
        return self._judge

    def key(self, description: str) -> str:
        judge = self.judge
        settings = {"judge": judge.settings, "samples": self.samples, "q": normalize(description)}
        return cache_key(judge.name, judge.version, settings)

    def ask(self, tracks: Sequence[Track], description: str) -> dict[str, Verdict]:
        """A verdict per track id. Only tracks not asked about before reach the judge.

        Any module error (unreadable video, bad cache file, no judge) comes out as
        :class:`DescribeError`, which the tool reports instead of treating as "no match"."""
        try:
            return self._ask(tracks, description)
        except DescribeError:
            raise
        except ModuleError as e:
            raise DescribeError(str(e)) from e

    def _ask(self, tracks: Sequence[Track], description: str) -> dict[str, Verdict]:
        description = normalize(description)
        if not description:
            raise DescribeError("an empty description matches nothing I can check")
        key = self.key(description)
        cached = read_json(self.storage, self.sha256, KIND, key)
        known = cached.get("verdicts", {}) if isinstance(cached, dict) else {}
        signatures = {t.track_id: _signature(t, sample_keyframes(t, self.samples)) for t in tracks}
        verdicts: dict[str, Verdict] = {}
        todo: list[Track] = []
        for track in tracks:
            entry = known.get(track.track_id)
            if (
                isinstance(entry, dict)
                and entry.get("signature") == signatures[track.track_id]
                and entry.get("verdict") in ("yes", "no", "unsure")
            ):
                verdicts[track.track_id] = entry["verdict"]
            else:
                todo.append(track)
        if todo:
            fresh = describe_tracks(self.video, todo, description, self.judge, self.samples)
            verdicts.update(fresh)
            for track_id, verdict in fresh.items():
                known[track_id] = {"signature": signatures[track_id], "verdict": verdict}
            data = {"judge": self.judge.name, "description": description, "verdicts": known}
            write_json(self.storage, self.sha256, KIND, key, data)
        return {t.track_id: verdicts[t.track_id] for t in tracks}


def project_describer(
    storage: Storage, project: Project, judge: Judge | None = None
) -> VideoDescriber:
    """The ``ToolContext.describer`` for a project, next to ``project_tracks`` for its tracks."""
    video = project.info.video
    return VideoDescriber(storage, video.sha256, storage.video_path(video), judge)
