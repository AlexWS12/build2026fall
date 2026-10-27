"""Project storage on disk (R-2, D-014): project folders, videos by hash, the analysis cache.

Shared file (HANDOFF.md). Layout and rules: docs/ARCHITECTURE.md, "Storage".
"""

from vibe.storage.store import (
    STORAGE_VERSION,
    Project,
    ProjectInfo,
    Storage,
    StorageError,
    VideoRef,
)

__all__ = ["STORAGE_VERSION", "Project", "ProjectInfo", "Storage", "StorageError", "VideoRef"]
