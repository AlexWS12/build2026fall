"""Shared helpers for the F-09 tests (a plain module, so tests can import it by name)."""

from pathlib import Path

from vibe.benchmark.manifest import ClipEntry, add_clip
from vibe.storage import Storage


def register(clip: Path, manifest_path: Path, storage: Storage, **overrides: object) -> ClipEntry:
    kwargs: dict = {
        "clip_id": "street-walk",
        "source": "Jean, Brickell, 2026-11-01",
        "consent": "everyone in frame agreed on camera at 0:00",
        **overrides,
    }
    entry, _ = add_clip(clip, manifest_path=manifest_path, storage=storage, **kwargs)
    return entry
