"""AI modules, each with stub / local / remote backends (F-08; docs/ARCHITECTURE.md, "Compute").

- ``backend``: the :class:`Analysis` interface and :func:`run_analysis`, which picks the
  backend (``VIBE_BACKEND``, default ``stub``) and goes through the shared analysis cache.
- ``cache``: cache keys, atomic JSON reads and writes, picking a cached result; no biometrics.
- ``remote``: the ``remote`` backend's client and the model server.
- ``doctor``: what this machine can run (GPU, driver, PyTorch build, compute tier).
- ``download``: model weights with checked hashes (``models.json``).

CLI: ``uv run python -m vibe.modules --help``. Nothing here imports torch at module level, so
the stub path and CI need no GPU packages.
"""

from vibe.modules.backend import (
    Analysis,
    AnalysisResult,
    make_analysis,
    register,
    registered,
    replay,
    run_analysis,
)
from vibe.modules.cache import (
    AmbiguousAnalysis,
    BiometricRefused,
    ModuleError,
    NoAnalysis,
    cache_key,
    cached_keys,
    pick_key,
    read_json,
    write_json,
)

__all__ = [
    "AmbiguousAnalysis",
    "Analysis",
    "AnalysisResult",
    "BiometricRefused",
    "ModuleError",
    "NoAnalysis",
    "cache_key",
    "cached_keys",
    "make_analysis",
    "pick_key",
    "read_json",
    "register",
    "registered",
    "replay",
    "run_analysis",
    "write_json",
]
