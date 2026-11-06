"""The shared analysis cache, for every AI module (docs/ARCHITECTURE.md, "Compute" and "Storage").

Results live at ``Storage.cache_path(sha256, kind, key)`` = ``cache/<sha256>/<kind>/<key>.json``
(D-014). This module adds what every module needs on top of that path:

- **Keys** (:func:`cache_key`): ``<readable name>-<8 hex digits>``, where the digits hash the
  module's version and every setting that changes its output. Switching a model, a threshold or
  the output format gives a new key, so a stale result is never served.
- **Atomic reads and writes** of JSON, so a crash never leaves a half-written file.
- **Picking a cached result** when the caller didn't name a key (:func:`pick_key`): the one key
  if there is exactly one, an error if there are several, never a guess.
- **No biometrics** (D-017): face embeddings and identity templates never leave their machine,
  so :func:`write_json` refuses those kinds. The cache is meant to be copied between teammates.
"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from vibe.storage import Storage

BIOMETRIC_KINDS = frozenset({"biometric", "embeddings", "face_embeddings", "identity"})
"""Kinds the shared cache refuses: biometric data stays in memory on its machine (D-017)."""

_UNSAFE = re.compile(r"[^A-Za-z0-9._@-]+")
_MAX_KEY = 128  # Storage allows 1-128 letters, digits and . _ @ -
_DIGEST = 8


class ModuleError(Exception):
    """Base for errors from the AI module layer. The message says what to do about it."""


class NoAnalysis(ModuleError):
    """Nothing is cached for this video, and this backend can't compute it.

    Callers must treat this as "unknown", never as "nothing found": a privacy edit on a video
    with no analysis fails closed (the toolbox's ``tracks=None``)."""


class AmbiguousAnalysis(ModuleError):
    """Several results are cached for one video and kind, and the caller didn't pick one."""


class BiometricRefused(ModuleError):
    """Something tried to write biometric data to the shared cache (D-017)."""


def settings_digest(value: object) -> str:
    """8 hex digits of sha256 over canonical JSON. Same settings, same digest, on any machine."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()[:_DIGEST]


def cache_key(name: str, version: int, settings: dict[str, object]) -> str:
    """``<name>-<digest>``, e.g. ``faster-whisper-small-3f9a1c2e``.

    ``name`` is for people (model and size); the digest covers ``name``, ``version`` and
    ``settings``, so it changes whenever the output would. Bump ``version`` when the module's
    code or output format changes in a way the settings don't show.
    """
    digest = settings_digest({"name": name, "version": version, "settings": settings})
    readable = _UNSAFE.sub("-", name).strip("-._@") or "x"
    return f"{readable[: _MAX_KEY - _DIGEST - 1]}-{digest}"


def _check_kind(kind: str) -> None:
    if kind.lower() in BIOMETRIC_KINDS:
        raise BiometricRefused(
            f"refusing to write {kind!r} to the shared analysis cache: biometric data stays on "
            "its machine (D-017). Keep it in memory and discard it after matching."
        )


def write_json(storage: Storage, sha256: str, kind: str, key: str, data: object) -> Path:
    """Write ``data`` as JSON to the cache, atomically (temp file, fsync, rename)."""
    _check_kind(kind)
    path = storage.cache_path(sha256, kind, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def read_json(storage: Storage, sha256: str, kind: str, key: str) -> object | None:
    """The cached JSON, or ``None`` if there's no file. A file that isn't JSON raises."""
    path = storage.cache_path(sha256, kind, key)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ModuleError(f"cached {kind} {path} isn't valid JSON ({e}); delete it to redo") from e


def cached_keys(storage: Storage, sha256: str, kind: str) -> list[str]:
    """Every key cached for this video and kind, sorted. Temp files are skipped."""
    folder = storage.cache_path(sha256, kind, "x").parent
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.json") if not p.name.startswith("."))


def pick_key(storage: Storage, sha256: str, kind: str, key: str | None = None) -> str | None:
    """The key to replay: ``key`` if given and cached, else the only cached one.

    ``None`` if nothing is cached. Several cached keys and no ``key`` raises
    :class:`AmbiguousAnalysis`, because picking one silently could blur the wrong analysis.
    """
    keys = cached_keys(storage, sha256, kind)
    if key is not None:
        return key if key in keys else None
    if not keys:
        return None
    if len(keys) > 1:
        raise AmbiguousAnalysis(
            f"{len(keys)} {kind} results are cached for video {sha256[:12]}; pick one of: "
            + ", ".join(keys)
        )
    return keys[0]
