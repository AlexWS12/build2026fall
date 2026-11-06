"""The ``remote`` backend: run an analysis on a model server on a team machine (e.g. the 5070).

**Client** (:class:`RemoteClient`): sends ``{module, settings}`` and the video's hash. If the
server doesn't have the video, it uploads it once, then asks again. It checks that the server
produced the same cache key it would have, so a server running another module version can't
fill this cache with results that don't match their key.

**Server** (:func:`create_app`, ``python -m vibe.modules serve``): runs the analysis with the
``local`` backend against its own data directory, so its cache fills up too. One analysis runs
at a time, so models take turns in VRAM.

**Data policy (D-017):** footage may only go to team-controlled machines. The client refuses any
host not listed in ``VIBE_REMOTE_HOSTS``; there's no per-project opt-in for third-party hosts
yet, so those always fail closed. Over a private network (e.g. Tailscale), set
``VIBE_REMOTE_TOKEN`` on both ends.

This is the offline path only: nothing per-frame (live mode) calls the network.
"""

import hashlib
import hmac
import json
import tempfile
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from vibe import config
from vibe.modules.backend import Analysis, make_analysis, run_analysis
from vibe.modules.cache import ModuleError
from vibe.storage import Storage, StorageError, VideoRef

API = "/v1"


class RemoteNotAllowed(ModuleError):
    """The model server's host isn't a team-controlled machine on the allowlist (D-017)."""


class RemoteError(ModuleError):
    """The model server is unreachable or answered with an error."""


# --- Client -------------------------------------------------------------------------------


class Transport(Protocol):
    """Sends one HTTP request. Swappable so tests run the server in-process, with no socket."""

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | Path | None,
        timeout: float,
    ) -> tuple[int, Any]: ...


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | Path | None, timeout: float
) -> tuple[int, Any]:
    """Standard library HTTP; streams a ``Path`` body from disk instead of loading it."""
    headers = dict(headers)
    data: Any = body
    file = None
    if isinstance(body, Path):
        file = body.open("rb")
        data = file
        headers["Content-Length"] = str(body.stat().st_size)
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, _json(response.read())
    except urllib.error.HTTPError as e:
        return e.code, _json(e.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RemoteError(f"can't reach the model server at {url}: {e}") from e
    finally:
        if file is not None:
            file.close()


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return {"detail": raw[:200].decode("utf-8", "replace")}


def check_allowed(url: str, allowed_hosts: frozenset[str]) -> str:
    """The URL's host, if it's on the allowlist; otherwise :class:`RemoteNotAllowed`."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise RemoteNotAllowed(f"model server URL must be http(s)://host[:port], got {url!r}")
    host = parts.hostname.lower()
    if host not in allowed_hosts:
        listed = ", ".join(sorted(allowed_hosts)) or "none"
        raise RemoteNotAllowed(
            f"won't send footage to {host!r}: it isn't in VIBE_REMOTE_HOSTS (now: {listed}). "
            "Only team-controlled machines are allowed; a third-party host needs the project's "
            "opt-in (D-017), which isn't built yet."
        )
    return host


@dataclass(frozen=True)
class RemoteClient:
    url: str
    allowed_hosts: frozenset[str]
    token: str | None = None
    timeout: float = 3600.0
    """Seconds. Generous: a whole-clip analysis on a long video can take a while."""
    transport: Transport = urllib_transport

    @classmethod
    def from_env(cls) -> "RemoteClient":
        url = config.remote_url()
        if url is None:
            raise RemoteError(
                "VIBE_BACKEND=remote needs VIBE_REMOTE_URL (e.g. http://alex-5070:8765) and "
                "VIBE_REMOTE_HOSTS listing that host"
            )
        return cls(url.rstrip("/"), config.remote_hosts(), config.remote_token())

    def _headers(self, content_type: str) -> dict[str, str]:
        headers = {"Content-Type": content_type, "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _call(self, method: str, path: str, body: bytes | Path | None, ctype: str) -> Any:
        check_allowed(self.url, self.allowed_hosts)  # every request, not only the first
        status, payload = self.transport(
            method, self.url + path, self._headers(ctype), body, self.timeout
        )
        if status == 200:
            return payload
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        raise _ServerSaid(status, detail)

    def health(self) -> Any:
        return self._call("GET", f"{API}/health", None, "application/json")

    def analyze(self, storage: Storage, video: VideoRef, analysis: Analysis) -> object:
        """Run ``analysis`` on the server and return its data (the caller caches it)."""
        request = json.dumps(
            {
                "module": analysis.module,
                "settings": analysis.settings,
                "video": {"sha256": video.sha256, "ext": video.ext},
            }
        ).encode()
        try:
            answer = self._analyze(request)
        except _ServerSaid as e:
            if not (e.status == 404 and _code(e.detail) == "video_missing"):
                raise e.as_error() from None
            self.upload(storage, video)
            try:
                answer = self._analyze(request)
            except _ServerSaid as again:
                raise again.as_error() from None

        if not isinstance(answer, dict) or "key" not in answer or "data" not in answer:
            raise RemoteError("the model server's answer has no key or data")
        if answer["key"] != analysis.key:
            raise RemoteError(
                f"the model server computed {answer['key']!r} but this machine expects "
                f"{analysis.key!r}: it runs another version of {analysis.module!r}. Update one "
                "of them (same commit) before trusting its results."
            )
        return answer["data"]

    def _analyze(self, body: bytes) -> Any:
        return self._call("POST", f"{API}/analyze", body, "application/json")

    def upload(self, storage: Storage, video: VideoRef) -> None:
        path = storage.video_path(video)
        if not path.is_file():
            raise ModuleError(f"video {video.sha256[:12]} isn't in {storage.videos_dir}")
        try:
            self._call(
                "PUT",
                f"{API}/videos/{video.sha256}?ext={video.ext}",
                path,
                "application/octet-stream",
            )
        except _ServerSaid as e:
            raise e.as_error() from None


class _ServerSaid(Exception):
    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(status, detail)
        self.status = status
        self.detail = detail

    def as_error(self) -> RemoteError:
        message = self.detail.get("message") if isinstance(self.detail, dict) else self.detail
        return RemoteError(f"model server answered {self.status}: {message}")


def _code(detail: Any) -> str | None:
    return detail.get("code") if isinstance(detail, dict) else None


# --- Server -------------------------------------------------------------------------------

MAX_UPLOAD_BYTES = 8 * 1024**3


def create_app(
    storage: Storage | None = None,
    token: str | None = None,
    hardware: Callable[[], dict[str, object]] | None = None,
):
    """The model server's FastAPI app. ``token``: require ``Authorization: Bearer <token>``."""
    from fastapi import Depends, FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse

    from vibe.modules.backend import registered

    storage = storage or Storage()
    lock = threading.Lock()  # one analysis at a time: models take turns in VRAM
    app = FastAPI(title="vibe model server", docs_url=None, redoc_url=None, openapi_url=None)

    def fail(status: int, code: str, message: str) -> HTTPException:
        return HTTPException(status, detail={"code": code, "message": message})

    def authorized(request: Request) -> None:
        if token is None:
            return
        given = request.headers.get("authorization", "")
        if not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
            raise fail(401, "unauthorized", "missing or wrong VIBE_REMOTE_TOKEN")

    @app.exception_handler(ModuleError)
    def module_error(_: Request, e: ModuleError) -> JSONResponse:
        return JSONResponse(
            status_code=422, content={"detail": {"code": type(e).__name__, "message": str(e)}}
        )

    @app.get(f"{API}/health", dependencies=[Depends(authorized)])
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "modules": sorted(registered()),
            "hardware": hardware() if hardware else None,
        }

    @app.post(f"{API}/analyze", dependencies=[Depends(authorized)])
    def analyze(body: dict[str, Any]) -> dict[str, object]:
        try:
            module = str(body["module"])
            settings = dict(body.get("settings") or {})
            video = VideoRef(
                sha256=body["video"]["sha256"],
                ext=body["video"].get("ext", ""),
                original_name="remote",
            )
        except (KeyError, TypeError, ValueError) as e:
            raise fail(400, "bad_request", f"expected module, settings and video: {e}") from None
        analysis = make_analysis(module, settings)
        if not storage.video_path(video).is_file():
            raise fail(404, "video_missing", f"upload video {video.sha256[:12]} first")
        with lock:
            result = run_analysis(storage, video, analysis, backend="local")
        return {"key": result.key, "data": result.data, "source": result.source}

    @app.put(f"{API}/videos/{{sha256}}", dependencies=[Depends(authorized)])
    async def upload(sha256: str, request: Request, ext: str = "") -> dict[str, object]:
        try:
            expected = VideoRef(sha256=sha256, ext=ext, original_name="remote")
        except ValueError:
            raise fail(400, "bad_request", "bad sha256 or ext") from None
        if storage.video_path(expected).is_file():
            return {"sha256": sha256, "stored": False}
        storage.videos_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with tempfile.TemporaryDirectory(dir=storage.root) as tmpdir:
            tmp = Path(tmpdir) / f"upload{ext}"
            with tmp.open("wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise fail(413, "too_large", "video is larger than the server allows")
                    digest.update(chunk)
                    f.write(chunk)
            if digest.hexdigest() != sha256:
                raise fail(400, "hash_mismatch", "uploaded bytes don't match the sha256")
            try:
                storage.add_video(tmp)
            except StorageError as e:
                raise fail(400, "bad_video", str(e)) from None
        return {"sha256": sha256, "stored": True}

    return app
