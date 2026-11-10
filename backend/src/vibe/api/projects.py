"""Project routes (F-64): upload a video, send a chat request, read the timeline state.

The server handles requests to a project one at a time, because the contract needs a request's
ops to be contiguous and storage doesn't lock (docs/ARCHITECTURE.md, "Storage"). A second
request for a busy project gets 409 instead of waiting.

Requests are planned but not applied yet: the plan executor is a separate card. Until it
exists, a plan comes back with ``status: "not_applied"`` and nothing is written, so the reply
never claims an edit (a privacy edit above all) that didn't happen.
"""

import json
import logging
import re
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import Field, ValidationError

from vibe.agent import Clarify, LlmError, Plan, PlanError, plan_request
from vibe.contract import TimelineState, new_id
from vibe.contract.common import Model
from vibe.modules.transcribe import TranscribeError, Transcript, transcribe_video
from vibe.storage import Project, ProjectInfo, Storage, StorageError
from vibe.video import NoAudioStream, VideoError, probe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])

MAX_UPLOAD_BYTES = 2 * 1024**3
"""Bigger uploads get 413. The server has already received the body by then (Starlette
buffers it), so this protects storage, not the network; a proxy limit would do that."""

_COPY_CHUNK = 1024**2
_EXT = re.compile(r"^\.[a-z0-9]{1,10}$")
"""Extensions storage accepts (``VideoRef.ext``). Any other is dropped, not refused."""
_NAME_MAX = 100


def get_storage() -> Storage:
    """Storage under ``VIBE_DATA_DIR``. Tests override this dependency with a temp folder."""
    return Storage()


StorageDep = Annotated[Storage, Depends(get_storage)]


class ChatRequest(Model):
    text: str = Field(min_length=1, pattern=r"\S", description="The user's chat message.")


ReplyStatus = Literal["applied", "clarify", "not_applied"]


class ChatReply(Model):
    request_id: str = Field(description="Ops written for this request carry this id.")
    status: ReplyStatus = Field(
        description=(
            "applied: the edit is in the timeline (once the plan executor exists). "
            "clarify: the reply is a question; nothing was written. "
            "not_applied: the request was planned or understood but nothing was written; "
            "the reply says why."
        )
    )
    reply: str = Field(description="Shown to the user in the chat.")


# --- One request at a time per project --------------------------------------------------
# Per process only: running several server workers would need a lock on disk instead.

_busy: set[str] = set()
_busy_guard = threading.Lock()


@contextmanager
def _one_request_at_a_time(project_id: str) -> Iterator[None]:
    """Mark the project busy for the duration, or raise 409 if it already is."""
    with _busy_guard:
        if project_id in _busy:
            raise HTTPException(status_code=409, detail="this project is busy with another request")
        _busy.add(project_id)
    try:
        yield
    finally:
        with _busy_guard:
            _busy.discard(project_id)


def _open(storage: Storage, project_id: str) -> Project:
    try:
        return storage.open_project(project_id)
    except StorageError as e:
        # The error names the data folder: log it, don't send it.
        logger.info("open_project(%r) failed: %s", project_id, e)
        raise HTTPException(status_code=404, detail=f"no project {project_id!r}") from e


# --- Upload names ---------------------------------------------------------------------------
# The client's file name is never used as a path: the upload is saved as ``upload<ext>``, and the
# cleaned name only becomes the project's name.


def _split_name(filename: str | None) -> tuple[str, str]:
    """``(project name, ext)`` from a client file name, both safe to use."""
    base = re.split(r"[\\/]", filename or "")[-1]
    base = "".join(c for c in base if c.isprintable())
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    ext = f".{ext.lower()}" if ext else ""
    if not _EXT.match(ext):
        stem, ext = base, ""  # keep an odd "extension" in the name rather than lose it
    name = stem.strip(" .")[:_NAME_MAX].strip(" .")
    return name or "upload", ext


def _save_upload(file: UploadFile, path: Path) -> None:
    size = 0
    with path.open("wb") as out:
        while chunk := file.file.read(_COPY_CHUNK):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"upload is larger than {MAX_UPLOAD_BYTES // 1024**2} MiB",
                )
            out.write(chunk)


# --- Routes ---------------------------------------------------------------------------------


@router.post("", status_code=201)
def create_project(file: UploadFile, storage: StorageDep) -> ProjectInfo:
    """Upload a video and create a project on it. Files that aren't a readable video get 400."""
    name, ext = _split_name(file.filename)
    with tempfile.TemporaryDirectory(prefix="vibe-upload-") as tmp:
        path = Path(tmp) / f"upload{ext}"
        try:
            _save_upload(file, path)
        except OSError as e:
            logger.exception("saving an upload failed")
            raise HTTPException(status_code=500, detail="couldn't save the upload") from e
        try:
            probe(path)
        except VideoError as e:
            logger.info("upload %r isn't a readable video: %s", file.filename, e)
            raise HTTPException(status_code=400, detail="not a readable video") from e
        try:
            project = storage.create_project(path, name=name)
        except (OSError, StorageError, ValidationError) as e:
            logger.exception("storing upload %r failed", file.filename)
            raise HTTPException(status_code=500, detail="couldn't store the upload") from e
    return project.info


@router.post("/{project_id}/requests")
def send_request(project_id: str, body: ChatRequest, storage: StorageDep) -> ChatReply:
    """Plan a chat request and reply. 409 if the project is already handling one.

    Until the plan executor exists, nothing is applied: a plan replies ``not_applied``.
    """
    project = _open(storage, project_id)
    with _one_request_at_a_time(project.id):
        request_id = new_id("r")
        try:
            result = plan_request(body.text)
        except LlmError as e:
            logger.warning("planner LLM failed for %s: %s", request_id, e)
            raise HTTPException(
                status_code=503,
                detail="the planner is unavailable; try again. Nothing was changed.",
            ) from e
        except (PlanError, ValueError) as e:  # a planner bug, not the user's fault
            logger.exception("planning %s (%r) failed", request_id, body.text)
            raise HTTPException(
                status_code=500, detail="couldn't plan this request. Nothing was changed."
            ) from e
        if isinstance(result, Clarify):
            return ChatReply(request_id=request_id, status="clarify", reply=result.question)
        return ChatReply(
            request_id=request_id,
            status="not_applied",
            reply=(
                f"{_describe(result)} Not applied: the plan executor isn't connected yet, "
                "so nothing was changed."
            ),
        )


@router.get("/{project_id}/state")
def get_state(project_id: str, storage: StorageDep) -> TimelineState:
    """The project's timeline state, folded from its op log."""
    return _open(storage, project_id).state()


@router.post("/{project_id}/transcript")
def transcribe_project(project_id: str, storage: StorageDep) -> Transcript:
    """The video's transcript with word timestamps (F-31), from the cache or made now.

    Making one runs Whisper and can take a while; the project is busy (409) until it's done.
    503 when the server can't transcribe (stub backend with no cached transcript, or no model).
    """
    project = _open(storage, project_id)
    with _one_request_at_a_time(project.id):
        try:
            return transcribe_video(storage, project.info.video)
        except TranscribeError as e:
            logger.warning("transcribing %s failed: %s", project.id, e)
            raise HTTPException(
                status_code=503, detail="transcription isn't available on this server"
            ) from e
        except NoAudioStream as e:
            raise HTTPException(status_code=400, detail="this video has no audio") from e
        except (VideoError, ValueError) as e:  # ValueError: e.g. an audio stream with no frames
            logger.exception("reading the audio of %s failed", project.id)
            raise HTTPException(status_code=500, detail="couldn't read the video's audio") from e


def _describe(plan: Plan) -> str:
    steps = "; ".join(f"{c.tool} {json.dumps(c.args)}" for c in plan.calls)
    return f"Planned: {steps}."
