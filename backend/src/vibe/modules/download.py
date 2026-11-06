"""Download model weights into ``VIBE_MODELS_DIR`` and check their sha256 (``download``).

The list lives in ``models.json`` next to this file. Weights are never committed (.gitignore);
each teammate downloads what their tier can run. A model whose hash isn't pinned yet is refused
unless you pass ``pin=True``, which downloads it and prints the hash to add to ``models.json``
after you've checked the file came from the right place.
"""

import hashlib
import json
import os
import tempfile
import urllib.request
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from vibe import config
from vibe.modules.cache import ModuleError


@dataclass(frozen=True)
class ModelEntry:
    name: str
    source: str
    """``url`` (we download it), ``package`` (its pip package does) or ``manual`` (gated)."""
    license: str
    purpose: str
    min_tier: int
    url: str | None = None
    file: str | None = None
    sha256: str | None = None
    package: str | None = None


def manifest(text: str | None = None) -> dict[str, ModelEntry]:
    if text is None:
        text = resources.files("vibe.modules").joinpath("models.json").read_text("utf-8")
    raw = json.loads(text)["models"]
    return {
        name: ModelEntry(
            name=name,
            source=m["source"],
            license=m["license"],
            purpose=m["for"],
            min_tier=m["min_tier"],
            url=m.get("url"),
            file=m.get("file"),
            sha256=m.get("sha256"),
            package=m.get("package"),
        )
        for name, m in raw.items()
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def model_path(entry: ModelEntry, models_dir: Path | None = None) -> Path:
    if entry.file is None:
        raise ModuleError(f"{entry.name} has no weights file of ours to locate")
    return (models_dir or config.models_dir()) / entry.file


def download(
    entry: ModelEntry, models_dir: Path | None = None, pin: bool = False
) -> tuple[Path, str]:
    """Fetch ``entry`` (if not already there and valid). Returns the path and its sha256."""
    if entry.source == "package":
        raise ModuleError(
            f"{entry.name}: its package downloads its own weights on first use; install it "
            f"(`uv pip install {entry.package}`) instead"
        )
    if entry.source == "manual":
        raise ModuleError(
            f"{entry.name}: gated download, so do it by hand from {entry.url} (accept the terms "
            f"yourself) and save it as {model_path(entry, models_dir)}"
        )
    if not entry.url:
        raise ModuleError(f"{entry.name}: models.json gives no url to download it from")
    target = model_path(entry, models_dir)
    if target.is_file():
        digest = _sha256(target)
        # No pinned hash: the file is the one you fetched with --pin and checked yourself.
        if entry.sha256 is None or digest == entry.sha256:
            return target, digest
        target.unlink()  # wrong or corrupt: fetch it again
    if entry.sha256 is None and not pin:
        raise ModuleError(
            f"{entry.name}: no sha256 pinned in models.json yet, so the download can't be "
            "checked. Run it once with --pin, check the file, then add the printed hash."
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".part")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(entry.url, timeout=60) as resp:
            for chunk in iter(lambda: resp.read(1 << 20), b""):
                out.write(chunk)
        digest = _sha256(tmp)
        if entry.sha256 is not None and digest != entry.sha256:
            raise ModuleError(
                f"{entry.name}: sha256 is {digest}, expected {entry.sha256}; not installed"
            )
        os.replace(tmp, target)
    except OSError as e:
        raise ModuleError(f"{entry.name}: download from {entry.url} failed: {e}") from e
    finally:
        tmp.unlink(missing_ok=True)
    return target, digest
