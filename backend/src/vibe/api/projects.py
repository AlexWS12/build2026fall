"""Project routes (F-64): upload a video, send a chat request, read the timeline state.

The server applies requests to a project one at a time, because the contract needs a request's
ops to be contiguous and storage doesn't lock (docs/ARCHITECTURE.md, "Storage"). A second
request for a busy project gets 409 instead of waiting.
"""

import json
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import Field, ValidationError

from vibe.agent import Clarify, Plan, plan_request
from vibe.contract import ContractError, Mute, TimelineState, TimeRange, new_id
from vibe.contract.common import Model
from vibe.storage import Project, ProjectInfo, Storage, StorageError
from vibe.video import VideoError, probe

router = APIRouter(prefix="/api/projects", tags=["projects"])


def get_storage() -> Storage:
    """Storage under ``VIBE_DATA_DIR``. Tests override this dependency with a temp folder."""
    return Storage()


StorageDep = Annotated[Storage, Depends(get_storage)]


class ChatRequest(Model):
    text: str = Field(min_length=1, pattern=r"\S", description="The user's chat message.")


class ChatReply(Model):
    request_id: str = Field(description="Ops written for this request carry this id.")
    reply: str = Field(description="Shown to the user in the chat.")


# --- One request at a time per project --------------------------------------------------
# Per process only: running several server workers would need a lock on disk instead.

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _project_lock(project_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(project_id, threading.Lock())


def _open(storage: Storage, project_id: str) -> Project:
    try:
        return storage.open_project(project_id)
    except StorageError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# --- Routes ---------------------------------------------------------------------------------


@router.post("", status_code=201)
def create_project(file: UploadFile, storage: StorageDep) -> ProjectInfo:
    """Upload a video and create a project on it. Files that aren't a readable video get 400."""
    # Keep the original file name: storage records it, and takes the extension from it.
    name = Path(file.filename or "").name or "upload"
    with tempfile.TemporaryDirectory(prefix="vibe-upload-") as tmp:
        path = Path(tmp) / name
        with path.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        try:
            probe(path)
        except VideoError as e:
            raise HTTPException(status_code=400, detail=f"not a readable video: {e}") from e
        try:
            project = storage.create_project(path)
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=f"unsupported file name {name!r}") from e
    return project.info


@router.post("/{project_id}/requests")
def send_request(project_id: str, body: ChatRequest, storage: StorageDep) -> ChatReply:
    """Plan a chat request and reply. 409 if the project is already handling one."""
    project = _open(storage, project_id)
    lock = _project_lock(project.id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="this project is busy with another request")
    try:
        request_id = new_id("r")
        result = plan_request(body.text)
        if isinstance(result, Clarify):
            return ChatReply(request_id=request_id, reply=result.question)
        try:
            note = apply_plan(project, request_id, result)
        except ContractError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return ChatReply(request_id=request_id, reply=f"{_describe(result)} {note}")
    finally:
        lock.release()


@router.get("/{project_id}/state")
def get_state(project_id: str, storage: StorageDep) -> TimelineState:
    """The project's timeline state, folded from its op log."""
    return _open(storage, project_id).state()


# --- Placeholder until the toolbox and the plan executor exist ------------------------------


def apply_plan(project: Project, request_id: str, plan: Plan) -> str:
    """Apply ``plan`` to ``project`` and return a note for the reply.

    TODO(F-06 toolbox + plan executor): replace this stub. The executor should run the plan's
    calls in order through ``vibe.toolbox.call_tool``, filling each ``{"$ref": ...}`` with an
    earlier step's result, and append the resulting ops for ``request_id``.

    STUB: ``plan`` is ignored. It appends one example op (mute 0:00-0:01) so the state route
    returns a real timeline, and says so in the reply.
    """
    project.append(
        Mute(request_id=request_id, source="agent", time_range=TimeRange(start=0, end=1))
    )
    return (
        "Nothing was applied yet: the toolbox and plan executor aren't connected. "
        "(Stub: added an example mute from 0:00 to 0:01 so the timeline has something to show.)"
    )


def _describe(plan: Plan) -> str:
    steps = "; ".join(f"{c.tool} {json.dumps(c.args)}" for c in plan.calls)
    return f"Planned: {steps}."
