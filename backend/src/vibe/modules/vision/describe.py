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

A description may hold several criteria ("in a grey t-shirt with a bag"). Unless the judge
reads whole sentences itself (``Judge.compound``), the describer splits it, answers each part
and combines them (:func:`combine`): any ``no`` is a no, all ``yes`` is a yes, anything else is
unsure. "With a bag" (or any other object the detector found) is answered from the tracks,
without a judge: is a bag track inside this person's box (:func:`with_object`)?

Verdicts are cached per video under ``cache/<sha256>/attributes/``, one file per judge and
description, so asking again costs nothing. Crops are never written anywhere.
"""

import re
from abc import ABC, abstractmethod
from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
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

MIN_CROPS = 2
"""Fewer crops than this is one look at the target: not enough for a sure answer."""

_MIN_SIDE = 4  # pixels; a smaller crop holds nothing a judge can use
_CROPS_VERSION = 2
"""In every cache key. Bump it when the pixels a judge is shown change (2: a keyframe past the
end of the video gives no crop, where it used to be cut from the last frame)."""


class DescribeError(ModuleError, DescriberError):
    """The description can't be checked: no judge on this backend, or the video can't be read.

    Callers must treat it as "unknown", never as "nobody matches"."""


class Judge(ABC):
    """Answers one question about one track from a few crops of it.

    ``settings`` must include everything that changes the answers: they're hashed into the
    cache key, with ``version``. A judge that can't answer at all (model missing, out of
    memory) raises :class:`DescribeError`; it never answers ``no`` for that."""

    name: ClassVar[str]
    version: ClassVar[int] = 1
    compound: ClassVar[bool] = False
    """True if the judge reads a whole sentence ("in grey and carrying a bag") in one go.
    False: the describer splits it into criteria and combines the answers."""

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

_CLOTHING = frozenset(
    "clothes clothing outfit shirt t-shirt tshirt tee top jacket hoodie sweater jumper coat "
    "dress".split()
)
_WEARING = frozenset({"in", "wearing", "wears", "dressed"})
_HOLDING = frozenset({"with", "has", "have", "carrying", "holding"})
_FILLER = (
    frozenset("a an the who is are that s on of colored coloured one ones".split())
    | _CLOTHING
    | _WEARING
    | _HOLDING
)
"""Words the colour stub may ignore: connectors and upper-body clothing, which is where it
looks. Any other word ("hat", "tall", "pants") is something it can't check, so it's unsure."""

_TOKEN = re.compile(r"[a-z][a-z-]*")
_PUNCTUATION = ".,;:!?\"'()"


def _words(description: str) -> list[str] | None:
    """The words of a description, or ``None`` if any of it isn't a plain word ("#ff0000",
    "no. 10"): something was said that can't be read, so nothing may be concluded."""
    words = []
    for raw in description.lower().split():
        word = raw.strip(_PUNCTUATION)
        if not word:
            continue
        if not _TOKEN.fullmatch(word):
            return None
        words.append(_ALIASES.get(word, word))
    return words


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


def _in_hues(
    h: np.ndarray, ranges: Iterable[tuple[float, float]], widen: float = 0.0
) -> np.ndarray:
    hit = np.zeros(h.shape, dtype=bool)
    for low, high in ranges:
        low, high = low - widen, high + widen
        hit |= (h >= low) & (h < high)
        if low < 0:
            hit |= h >= low + 360
        if high > 360:
            hit |= h < high - 360
    return hit


def _color_masks(rgb: np.ndarray, color: str) -> tuple[np.ndarray, np.ndarray]:
    """Which pixels surely are ``color``, and which might be.

    "Might be" is what keeps the stub from a wrong ``no``: navy and burgundy are too dark to
    count as blue or red, pastels too pale, teal sits between green and blue, and skin has the
    hue of red and orange. None of them is surely the colour, and none is surely not.
    """
    h, s, v = _hsv(rgb)
    if color == "black":
        return v < 0.2, v < 0.35
    if color == "white":
        return (s < 0.15) & (v > 0.8), (s < 0.3) & (v > 0.6)
    if color == "grey":
        return (s < 0.15) & (v >= 0.2) & (v <= 0.8), (s < 0.3) & (v >= 0.1) & (v <= 0.9)
    skin = (h <= 50) & (s >= 0.15) & (s <= 0.7) & (v >= 0.3)
    sure = _in_hues(h, _HUES[color]) & (s >= 0.4) & (v >= 0.25) & ~skin
    maybe = _in_hues(h, _HUES[color], widen=20) & (s >= 0.15) & (v >= 0.1)
    return sure, maybe


def color_fraction(rgb: np.ndarray, color: str) -> float:
    """The share of pixels in ``rgb`` that surely are ``color`` (a name in ``COLORS``)."""
    return float(_color_masks(rgb, color)[0].mean())


def _middle(crop: np.ndarray, label: str) -> np.ndarray:
    """Where the stub looks: the chest of a person box, the middle of anything else, so the
    background around the target and a person's face and legs count less."""
    height, width = crop.shape[:2]
    y1, y2 = (0.2, 0.6) if label == "person" else (0.25, 0.75)
    return crop[int(height * y1) : max(int(height * y2), 1), int(width * 0.25) : int(width * 0.75)]


class ColorJudge(Judge):
    """The stub: is this track's clothing (or, for other labels, the thing itself) a colour?

    Each crop votes. ``yes``: at least ``yes_at`` of its middle surely is that colour. ``no``:
    ``no_at`` or less of the middle surely is, ``maybe_at`` or less of it might be, and the
    colour isn't somewhere else in the box either (red trousers under a white shirt are still
    "in red"). Anything else is ``unsure``. The track gets a verdict only if every crop agrees
    and there are at least ``MIN_CROPS``; otherwise, and when the description says anything
    besides one colour, it is ``unsure``. The gaps are deliberate: a wrong ``no`` leaves someone
    unmasked. Still fooled by: a neighbour filling the middle of the box, coloured light.
    """

    name = "color"
    version = 3
    # 2: every crop must agree (1 took the median, which could turn doubt into a no).
    # 3: "might be" pixels and colour elsewhere in the box block a no, skin isn't red or orange,
    #    one crop is never enough, clothing words on a non-person are unsure.

    def __init__(self, yes_at: float = 0.35, no_at: float = 0.08, maybe_at: float = 0.25) -> None:
        if not 0 <= no_at < yes_at <= 1:
            raise ValueError(f"need 0 <= no_at < yes_at <= 1, got {no_at} and {yes_at}")
        if not 0 <= maybe_at <= 1:
            raise ValueError(f"need 0 <= maybe_at <= 1, got {maybe_at}")
        super().__init__(yes_at=yes_at, no_at=no_at, maybe_at=maybe_at)
        self.yes_at, self.no_at, self.maybe_at = yes_at, no_at, maybe_at

    def judge(self, crops: Sequence[np.ndarray], label: str, description: str) -> Verdict:
        color = self.color_in(description, label)
        if color is None or len(crops) < MIN_CROPS:
            return "unsure"
        # Every crop must agree. A track that is red in some crops and not in others may be two
        # people, or one partly hidden: calling it "no" would leave the red part unmasked.
        votes = {self._vote(crop, label, color) for crop in crops}
        return votes.pop() if len(votes) == 1 else "unsure"

    def _vote(self, crop: np.ndarray, label: str, color: str) -> Verdict:
        sure, maybe = _color_masks(_middle(crop, label), color)
        if sure.size == 0:
            return "unsure"
        share = float(sure.mean())
        if share >= self.yes_at:
            return "yes"
        elsewhere = color_fraction(crop, color)
        if share <= self.no_at and float(maybe.mean()) <= self.maybe_at and elsewhere <= self.no_at:
            return "no"
        return "unsure"

    @staticmethod
    def color_in(description: str, label: str = "person") -> str | None:
        """The one colour ``description`` asks about, or ``None`` if it asks anything else."""
        words = _words(description)
        if not words:
            return None
        colors = {w for w in words if w in COLORS}
        rest = [w for w in words if w not in COLORS and w not in _FILLER]
        if len(colors) != 1 or rest:
            return None
        said = set(words)
        if label != "person" and said & (_CLOTHING | _WEARING):
            return None  # "faces in red" asks about the clothes of whoever the face belongs to
        if label == "person" and words[0] in _HOLDING and not said & _CLOTHING:
            return None  # "with a black ...": names a thing they carry, not what they wear
        return next(iter(colors))


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
    # With more keyframes than samples: the middle of each of `samples` equal stretches, which
    # skips the first and last keyframes, where a target is often half out of the frame.
    return [pool[int((j + 0.5) * len(pool) / samples)] for j in range(samples)]


def _crop(image: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray | None:
    height, width = image.shape[:2]
    x1, y1 = int(box[0] * width), int(box[1] * height)
    x2, y2 = int(np.ceil(box[2] * width)), int(np.ceil(box[3] * height))
    if x2 - x1 < _MIN_SIDE or y2 - y1 < _MIN_SIDE:
        return None
    # A copy: a view would keep the whole decoded frame alive for as long as the crop is.
    return np.ascontiguousarray(image[y1:y2, x1:x2])


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

    Reads each needed frame once, in order. A track with no usable crop is ``unsure``. A
    keyframe past the end of the video (tracks from a longer cut) gives no crop: no frame
    shows where the target was then.
    """
    picks = {t.track_id: sample_keyframes(t, samples) for t in tracks}
    crops: dict[str, list[np.ndarray]] = {t.track_id: [] for t in tracks}
    try:
        with VideoReader(video) as reader:
            wanted: dict[int, list[tuple[str, tuple[float, float, float, float]]]] = {}
            for track in tracks:
                for i in picks[track.track_id]:
                    key = track.keyframes[i]
                    if key.t >= reader.index.end:
                        continue
                    frame = reader.index.frame_at(key.t)
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


# --- several criteria ---------------------------------------------------------------------


def normalize(description: str) -> str:
    """Lowercase, single spaces, no punctuation at the ends: one cache file per question."""
    return " ".join(description.lower().split()).strip(_PUNCTUATION + " ")


_CRITERION_START = r"(?:in|with|wearing|carrying|holding|dressed)\b"
_CRITERIA_SPLIT = re.compile(
    rf"\s*(?:[,;]|\band\b|\bplus\b)\s*(?:(?:and|plus)\s+)?(?={_CRITERION_START})"
    rf"|\s+(?=(?:with|wearing|carrying|holding)\b)"
)


def split_criteria(description: str) -> list[str]:
    """ "in a grey t-shirt with a bag" is two questions: "in a grey t-shirt" and "with a bag".

    A cut happens before "with" / "wearing" / "carrying" / "holding", and at "and", "plus" or
    a comma only when what follows starts a new criterion ("in red, and carrying a bag"). So
    "in black and white" and "with a black and white dog" stay whole: what "and" between
    colours means (both, either, one outfit) is for a judge that reads the sentence; the
    colour stub is unsure about it, which is masked. Cutting there gave a bare "white" the
    stub *could* answer, and a wrong ``no`` for a person in black."""
    parts = (part.strip() for part in _CRITERIA_SPLIT.split(normalize(description)))
    return [part for part in parts if part]


def combine(verdicts: Iterable[Verdict]) -> Verdict:
    """All the criteria at once: any ``no`` is a no, all ``yes`` is a yes, else unsure."""
    verdicts = list(verdicts)
    if "no" in verdicts:
        return "no"
    return "yes" if verdicts and all(v == "yes" for v in verdicts) else "unsure"


# --- "with a bag": answered from the tracks -----------------------------------------------

_OBJECT_WORDS: Mapping[str, tuple[str, ...]] = {
    "bag": ("handbag", "backpack", "suitcase"),
    "purse": ("handbag",),
    "rucksack": ("backpack",),
    "luggage": ("suitcase",),
    "phone": ("cell phone",),
    "bike": ("bicycle",),
}
"""Everyday words for detector labels. Any other noun is tried as a label itself ("dog",
"umbrella", "backpack"), so this works for whatever the detector was asked to find."""

_OBJECT = re.compile(
    r"(?:(?:with|carrying|holding|has|have)\s+)?(?:(?:a|an|the|his|her|their)\s+)?([a-z][a-z -]*)"
)

OBJECT_INSIDE = 0.5
"""An object is "with" a person when at least this much of its box is inside the person's."""
OBJECT_YES_AT = 0.5
"""... in at least this share of the person's keyframes. Starting points, not measured."""
_OBJECT_GAP = 1.0  # seconds; like the renderer, no straight line across a longer gap
_OBJECT_EDGE = 0.1  # seconds past an object's first or last keyframe it still counts as there


def object_labels(criterion: str, scene_labels: Iterable[str]) -> tuple[str, ...] | None:
    """The detector labels "with a bag" is about, or ``None`` if it isn't that kind of
    question ("with a red bag" needs eyes: it goes to the judge)."""
    match = _OBJECT.fullmatch(criterion)
    if match is None:
        return None
    noun = match.group(1).strip()
    labels = set(scene_labels)
    for word in (noun, noun.removesuffix("s"), noun.removesuffix("es")):
        if word in _OBJECT_WORDS:
            return _OBJECT_WORDS[word]
        if word in labels:
            return (word,)
    return None


class _Path:
    """An object track's box over time."""

    def __init__(self, track: Track) -> None:
        self.keys = track.keyframes
        self.times = [k.t for k in track.keyframes]

    def at(self, t: float) -> tuple[float, float, float, float] | None:
        first, last = self.keys[0], self.keys[-1]
        if t <= first.t:
            return first.box if first.t - t <= _OBJECT_EDGE else None
        if t >= last.t:
            return last.box if t - last.t <= _OBJECT_EDGE else None
        i = bisect_right(self.times, t) - 1
        a, b = self.keys[i], self.keys[i + 1]
        if b.t - a.t > _OBJECT_GAP:
            near = a if t - a.t <= b.t - t else b
            return near.box if abs(near.t - t) <= _OBJECT_EDGE else None
        w = (t - a.t) / (b.t - a.t)
        return tuple(x + (y - x) * w for x, y in zip(a.box, b.box, strict=True))  # type: ignore[return-value]


def _inside(obj: tuple[float, float, float, float], box: tuple[float, float, float, float]) -> bool:
    area = (obj[2] - obj[0]) * (obj[3] - obj[1])
    width = min(obj[2], box[2]) - max(obj[0], box[0])
    height = min(obj[3], box[3]) - max(obj[1], box[1])
    return area > 0 and width > 0 and height > 0 and width * height / area >= OBJECT_INSIDE


def with_object(track: Track, objects: Sequence[Track]) -> Verdict:
    """Is one of ``objects`` (bag tracks, say) with the target of ``track``?

    ``yes`` when an object sits inside the track's box in at least ``OBJECT_YES_AT`` of the
    keyframes a model saw or a person drew; else ``unsure``. Never ``no``: a detector often
    misses a small or hidden bag, and a ``no`` here would rule the person out of the whole
    description and leave them unmasked. Only a judge looking at the pixels can say "no bag".
    ``tracked`` and ``uncertain`` keyframes don't count, as in :func:`sample_keyframes`: their
    boxes may have drifted off the person. In a crowd a neighbour's bag can sit inside
    someone's box, so a ``yes`` here can be wrong: fine for masking more, a known gap for
    "everyone except".
    """
    keys = [k for k in track.keyframes if k.state in ("detected", "user")]
    if len(keys) < MIN_CROPS:
        return "unsure"
    first, last = keys[0].t - _OBJECT_EDGE, keys[-1].t + _OBJECT_EDGE
    paths = [_Path(o) for o in objects if o.keyframes[0].t <= last and o.keyframes[-1].t >= first]
    hits = 0
    for key in keys:
        for path in paths:
            box = path.at(key.t)
            if box is not None and _inside(box, key.box):
                hits += 1
                break
    return "yes" if hits / len(keys) >= OBJECT_YES_AT else "unsure"


def _object_rule(
    criterion: str, tracks: Sequence[Track], scene: Sequence[Track] | None
) -> dict[str, Verdict] | None:
    """Verdicts for an object criterion, or ``None`` when the tracks can't answer it: it isn't
    about an object, or this analysis found no such object anywhere (maybe it never looked)."""
    if not scene:
        return None
    labels = object_labels(criterion, {t.label for t in scene})
    if labels is None:
        return None
    asked = {t.label for t in tracks}
    objects = [t for t in scene if t.label in labels and t.label not in asked]
    if not objects:
        return None
    return {t.track_id: with_object(t, objects) for t in tracks}


# --- cached, per video --------------------------------------------------------------------


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
        settings = {
            "judge": judge.settings,
            "samples": self.samples,
            "crops": _CROPS_VERSION,
            "q": normalize(description),
        }
        return cache_key(judge.name, judge.version, settings)

    def ask(
        self,
        tracks: Sequence[Track],
        description: str,
        scene: Sequence[Track] | None = None,
    ) -> dict[str, Verdict]:
        """A verdict per track id: does it match every criterion in ``description``?

        ``scene`` is every track of the video, for criteria about other things ("with a
        bag"). Any failure (unreadable video, bad cache file, no judge) comes out as
        :class:`DescribeError`, which the tool reports instead of treating as "no match"."""
        each = self.ask_each(tracks, description, scene)
        return {t.track_id: combine(v[t.track_id] for v in each.values()) for t in tracks}

    def ask_each(
        self,
        tracks: Sequence[Track],
        description: str,
        scene: Sequence[Track] | None = None,
    ) -> dict[str, dict[str, Verdict]]:
        """The verdicts for each criterion of ``description``, keyed by the criterion."""
        try:
            return self._ask_each(tracks, description, scene)
        except DescribeError:
            raise
        except (ModuleError, OSError) as e:
            raise DescribeError(str(e)) from e

    def _ask_each(
        self, tracks: Sequence[Track], description: str, scene: Sequence[Track] | None
    ) -> dict[str, dict[str, Verdict]]:
        description = normalize(description)
        if not description:
            raise DescribeError("an empty description matches nothing I can check")
        if self.judge.compound:
            return {description: self._ask_judge(tracks, description)}
        each: dict[str, dict[str, Verdict]] = {}
        for criterion in split_criteria(description) or [description]:
            rule = _object_rule(criterion, tracks, scene)
            each[criterion] = rule if rule is not None else self._ask_judge(tracks, criterion)
        return each

    def _known(self, key: str, description: str) -> dict[str, object]:
        """The cached entries for this judge and question; nothing if the file is another's."""
        cached = read_json(self.storage, self.sha256, KIND, key)
        if (
            not isinstance(cached, dict)
            or cached.get("judge") != self.judge.name
            or cached.get("description") != description
            or not isinstance(cached.get("verdicts"), dict)
        ):
            return {}
        return cached["verdicts"]

    def _ask_judge(self, tracks: Sequence[Track], description: str) -> dict[str, Verdict]:
        """One question for the judge. Only tracks not asked about before reach it."""
        key = self.key(description)
        known = self._known(key, description)
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
            # Read again before writing: another request may have added entries meanwhile.
            known = self._known(key, description)
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
