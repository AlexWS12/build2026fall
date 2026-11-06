"""One way to run any AI analysis on any machine: the ``stub`` / ``local`` / ``remote`` backends.

An :class:`Analysis` says *what* to compute (module, settings, cache kind) and *how* to compute
it on this machine (:meth:`Analysis.compute`). :func:`run_analysis` decides *where*:

- every backend first returns a cached result under the analysis's key, if there is one;
- ``stub`` stops there: no model, no GPU. A miss raises :class:`NoAnalysis`, never an empty
  result, so privacy edits fail closed. Results computed elsewhere (copy ``cache/<sha256>/``
  from a teammate) replay on any laptop;
- ``local`` runs :meth:`Analysis.compute` in this process and caches the result;
- ``remote`` asks a model server on a team machine (``vibe.modules.remote``) and caches the
  result here, so the next run is a cache hit.

Code above this can't tell which backend ran. This is the offline, whole-clip path; per-frame
models for live mode run in-process only, so nothing per-frame calls the network.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal

from vibe import config
from vibe.config import Backend
from vibe.modules.cache import (
    BiometricRefused,
    ModuleError,
    NoAnalysis,
    cache_key,
    cached_keys,
    check_kind,
    pick_key,
    read_json,
    write_json,
)
from vibe.storage import Storage, VideoRef


class Analysis(ABC):
    """One analysis a module can run on a whole video, e.g. "tracks with RF-DETR L, stride 2".

    Subclasses set the class attributes and implement :meth:`compute`; register them with
    :func:`register` so a model server can build them by name. ``settings`` must be JSON values
    and must include everything that changes the output: they're hashed into the cache key.
    """

    module: ClassVar[str]
    """Registry name, e.g. ``"transcript"``. Unique across modules."""
    kind: ClassVar[str]
    """Cache folder, e.g. ``"tracks"`` or ``"transcript"``."""
    version: ClassVar[int] = 1
    """Bump when the code or the output format changes in a way ``settings`` doesn't show."""
    biometric: ClassVar[bool] = False
    """True if the output is biometric (face embeddings, voiceprints...). Such an analysis never
    goes through :func:`run_analysis`, whatever its ``kind`` is called: keep it in memory
    (D-017)."""

    def __init__(self, **settings: object) -> None:
        self.settings: dict[str, object] = dict(settings)

    @property
    def name(self) -> str:
        """The readable part of the cache key. Override to include the model, e.g. ``rfdetr-l``."""
        return self.module

    @property
    def key(self) -> str:
        return cache_key(self.name, self.version, self.settings)

    @abstractmethod
    def compute(self, video_path: Path) -> object:
        """Run the model on the whole video and return JSON-ready data (dicts, lists, numbers).

        Import model packages (torch...) inside this method, never at module level, so the stub
        path and CI never need them. Raise :class:`ModuleError` with an install hint if one is
        missing."""


_REGISTRY: dict[str, type[Analysis]] = {}


def register[A: type[Analysis]](cls: A) -> A:
    """Class decorator: make an analysis buildable by name (what the model server receives)."""
    existing = _REGISTRY.get(cls.module)
    if existing is not None and existing is not cls:
        raise ValueError(f"analysis module {cls.module!r} is already registered by {existing}")
    _REGISTRY[cls.module] = cls
    return cls


def make_analysis(module: str, settings: dict[str, object]) -> Analysis:
    cls = _REGISTRY.get(module)
    if cls is None:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise ModuleError(f"unknown analysis module {module!r} (registered: {known})")
    return cls(**settings)


def registered() -> dict[str, type[Analysis]]:
    return dict(_REGISTRY)


@dataclass(frozen=True)
class AnalysisResult:
    key: str
    data: object
    source: Literal["cache", "local", "remote"]
    """Where it came from this time. For logs and the UI, never for deciding behaviour."""
    path: Path = field(compare=False)


def replay(storage: Storage, sha256: str, kind: str, key: str | None = None) -> AnalysisResult:
    """Whatever is cached for this video and kind, without knowing the settings that made it.

    For readers like the toolbox: "the tracks for this video". With no ``key`` it uses the only
    cached result; several raise :class:`AmbiguousAnalysis`, none raises :class:`NoAnalysis`.
    """
    picked = pick_key(storage, sha256, kind, key)
    data = None if picked is None else read_json(storage, sha256, kind, picked)
    if picked is None or data is None:
        wanted = f"{kind}/{key}" if key else kind
        raise NoAnalysis(
            f"no cached {wanted} for video {sha256[:12]}: compute it with VIBE_BACKEND=local or "
            f"remote, or copy cache/{sha256}/{kind}/ from a teammate"
        )
    return AnalysisResult(picked, data, "cache", storage.cache_path(sha256, kind, picked))


def run_analysis(
    storage: Storage,
    video: VideoRef,
    analysis: Analysis,
    backend: Backend | None = None,
) -> AnalysisResult:
    """The analysis's result for ``video``: from the cache, or computed by ``backend``.

    ``backend`` defaults to ``VIBE_BACKEND`` (``stub`` unless set).
    """
    # Before any cache read, upload or model run: biometric output never enters the cache.
    if analysis.biometric:
        raise BiometricRefused(
            f"{analysis.module} is biometric: run it in memory on its machine, never through "
            "run_analysis and the shared cache (D-017)"
        )
    check_kind(analysis.kind)
    backend = backend or config.model_backend()
    key = analysis.key
    path = storage.cache_path(video.sha256, analysis.kind, key)

    cached = read_json(storage, video.sha256, analysis.kind, key)
    if cached is not None:
        return AnalysisResult(key, cached, "cache", path)

    if backend == "stub":
        others = [k for k in cached_keys(storage, video.sha256, analysis.kind) if k != key]
        hint = f" Cached under other settings: {', '.join(others)}." if others else ""
        raise NoAnalysis(
            f"stub backend: no cached {analysis.kind}/{key} for video {video.sha256[:12]}. "
            f"Run with VIBE_BACKEND=local or remote, or copy cache/{video.sha256}/"
            f"{analysis.kind}/ from a teammate.{hint}"
        )

    if backend == "local":
        source = storage.video_path(video)
        if not source.is_file():
            raise ModuleError(f"video {video.sha256[:12]} isn't in {storage.videos_dir}")
        data = analysis.compute(source)
        origin: Literal["local", "remote"] = "local"
    elif backend == "remote":
        from vibe.modules.remote import RemoteClient

        data = RemoteClient.from_env().analyze(storage, video, analysis)
        origin = "remote"
    else:  # config.model_backend() already rejects anything else
        raise ModuleError(f"unknown backend {backend!r}")

    if data is None:
        raise ModuleError(f"{analysis.module} returned no result; refusing to cache nothing")
    write_json(storage, video.sha256, analysis.kind, key, data)
    return AnalysisResult(key, data, origin, path)
