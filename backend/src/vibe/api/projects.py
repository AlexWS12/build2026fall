"""Project routes (F-64): upload a video, chat with the agent, and everything the editor reads.

    POST /api/projects                      upload a video, create a project
    GET  /api/projects                      every project, newest first
    GET  /api/projects/{id}                 the project and its video's facts
    GET  /api/projects/{id}/video           the source video (HTTP Range works, so it seeks)
    POST /api/projects/{id}/requests        a chat request: plan it, run it, reply
    GET  /api/projects/{id}/messages        the chat so far
    GET  /api/projects/{id}/state           the timeline state
    GET  /api/projects/{id}/history         the requests in the log; what undo and redo would do
    POST /api/projects/{id}/undo, /redo     through the toolbox, like any other edit
    GET  /api/projects/{id}/tracks          where the targets are, for the preview's overlay
    GET  /api/projects/{id}/review          the mask stretches to check (F-41)
    POST /api/projects/{id}/review/{item}   confirm one of them, or cut it out
    POST /api/projects/{id}/analysis        make sure the video has a vision analysis
    POST /api/projects/{id}/exports         render the timeline into a new MP4
    GET  /api/projects/{id}/exports/{name}  that MP4

The server handles requests to a project one at a time, because the contract needs a request's
ops to be contiguous and storage doesn't lock (docs/ARCHITECTURE.md, "Storage"). A second
request for a busy project gets 409 instead of waiting; 409 means nothing else here.

A chat request is planned (``vibe.agent``), then run by the plan executor, which writes all of
its ops or none. The reply says ``applied`` only when the ops are in the timeline: a tool that
refuses (no analysis for "blur faces", a time past the end) gives ``not_applied`` with the
reason, and nothing is written, so no reply claims an edit that didn't happen.

Error details are short fixed texts. Whatever names a file on the server is logged, not sent.
"""

import functools
import logging
import re
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field, ValidationError

from vibe.agent import (
    Clarify,
    LlmError,
    Plan,
    PlanError,
    StepReport,
    ToolCall,
    execute_plan,
    execute_request,
    plan_request,
)
from vibe.agent.describe import clock, describe, explain_failure
from vibe.agent.heavy import needs_preview
from vibe.api.previews import PreviewReply, PreviewSummary, candidate_state, summarise
from vibe.contract import Redo, TimelineState, Track, Undo, fold, new_id
from vibe.contract.common import Model, Source
from vibe.modules import ModuleError
from vibe.modules.denoise import cached_noise_profile
from vibe.modules.transcribe import Transcript, transcribe_video
from vibe.modules.vision import CacheError, load_tracks
from vibe.render import (
    NothingToRender,
    RenderError,
    ReviewItem,
    UnsupportedEdit,
    export,
    review_queue,
)
from vibe.storage import ChatRecord, PendingPreview, Project, ProjectInfo, Storage, StorageError
from vibe.toolbox import UI_TOOLS
from vibe.video import FrameIndex, NoAudioStream, VideoError, VideoInfo, probe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])

MAX_UPLOAD_BYTES = 2 * 1024**3
"""Bigger uploads get 413. The server has already received the body by then (Starlette
buffers it), so this protects storage, not the network; a proxy limit would do that."""

_COPY_CHUNK = 1024**2
_EXT = re.compile(r"^\.[a-z0-9]{1,10}$")
"""Extensions storage accepts (``VideoRef.ext``). Any other is dropped, not refused."""
_NAME_MAX = 100

_VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
}
"""By stored extension, not ``mimetypes``: that reads the registry on Windows and varies."""

_EXPORT_NAME = re.compile(r"export_[0-9a-f]{12}\.mp4")
"""Exactly what ``create_export`` makes. Anything else is 404."""

_NO_ANALYSIS = (
    "no analysis is cached for this video, and this server can't run one yet. Analyse it with "
    "`python -m vibe.modules.vision analyze`, or copy the video's cache folder from a teammate."
)
_BAD_ANALYSIS = (
    "this video's analysis can't be used: it's unreadable, or several are cached and none is "
    "marked as the default (`python -m vibe.modules.vision default`)."
)


def get_storage() -> Storage:
    """Storage under ``VIBE_DATA_DIR``. Tests override this dependency with a temp folder."""
    return Storage()


StorageDep = Annotated[Storage, Depends(get_storage)]


class ChatRequest(Model):
    text: str = Field(min_length=1, pattern=r"\S", description="The user's chat message.")


ReplyStatus = Literal["applied", "clarify", "not_applied", "preview"]


class ChatReply(Model):
    request_id: str = Field(description="Ops written for this request carry this id.")
    status: ReplyStatus = Field(
        description=(
            "applied: the plan ran and its ops are in the timeline. "
            "clarify: the reply is a question; nothing was written. "
            "not_applied: a tool refused, so nothing was written; the reply says why. "
            "preview: a cut or keep is proposed and waits for accept or reject (F-44); "
            "nothing was written yet."
        )
    )
    reply: str = Field(description="Shown to the user in the chat.")
    steps: list[StepReport] = Field(
        default_factory=list,
        description="The tool calls that ran, in order, up to and including one that failed.",
    )
    preview: PreviewSummary | None = Field(
        default=None, description="What a proposed edit does to the length. Only for `preview`."
    )


class ChatMessage(Model):
    """One past chat request with its reply, as stored in the project's ``chat.jsonl``."""

    request_id: str
    text: str
    status: ReplyStatus
    reply: str
    steps: list[StepReport] = Field(default_factory=list)
    at: datetime


class VideoSummary(Model):
    duration: float = Field(description="Seconds.")
    width: int = Field(description="As displayed, after rotation.")
    height: int
    fps: float
    has_audio: bool


class ProjectDetail(Model):
    info: ProjectInfo
    video: VideoSummary
    analysed: bool = Field(
        description="Whether a vision analysis of the video is cached and usable. Until it is, "
        "requests that hide people, faces or plates are refused."
    )


class HistoryEntry(Model):
    request_id: str
    source: Source
    status: Literal["applied", "undone"]
    kinds: list[str] = Field(description="The kind of each op the request wrote, in log order.")
    text: str | None = Field(description="The chat message, if it was a chat request.")
    reply: str | None


class HistoryReply(Model):
    requests: list[HistoryEntry] = Field(
        description="Every request that wrote edits, in log order. Undo and redo aren't listed: "
        "they show as the status of the request they acted on."
    )
    can_undo: bool
    can_redo: bool


class TracksReply(Model):
    analysed: bool
    tracks: list[Track] = Field(
        description="The cached analysis's tracks, then the project's hand-drawn ones."
    )


class ReviewReply(Model):
    analysed: bool = Field(
        description="Whether the video has an analysis. Without one, masks on analysed targets "
        "show as no_track: the whole frame is hidden until the video is analysed."
    )
    items: list[ReviewItem] = Field(
        description="The stretches of the masks to check, most serious first."
    )
    held_seconds: float = Field(
        description="Time in held stretches (a box kept just past a track's end) not yet reviewed."
    )
    rules_not_audited: list[str] = Field(
        description="Standing rules, which can't be audited ahead of a render."
    )


class ReviewAction(Model):
    action: Literal["confirm", "cut"] = Field(
        description="confirm: mark the stretch as checked. cut: cut the stretch out of the video."
    )


class AnalysisReply(Model):
    analysed: bool
    tracks: int = Field(description="How many tracks GET .../tracks now returns.")


class ExportReply(Model):
    name: str
    url: str = Field(description="Where to GET the MP4.")
    duration: float = Field(description="Length of the output in seconds.")
    issues: list[str] = Field(
        description="What the renderer masked more because of (a lost track, a missing one)."
    )


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


@router.get("")
def list_projects(storage: StorageDep) -> list[ProjectInfo]:
    """Every project, newest first."""
    return storage.list_projects()


@router.get("/{project_id}")
def get_project(project_id: str, storage: StorageDep) -> ProjectDetail:
    """The project, its video's facts, and whether the video has been analysed."""
    project = _open(storage, project_id)
    info = _probe(storage, project)
    try:
        analysed = load_tracks(storage, project.info.video.sha256) is not None
    except (CacheError, ValidationError) as e:
        logger.warning("analysis cache for project %s can't be used: %s", project.id, e)
        analysed = False
    return ProjectDetail(
        info=project.info,
        video=VideoSummary(
            duration=info.duration,
            width=info.width,
            height=info.height,
            fps=float(info.fps),
            has_audio=info.audio is not None,
        ),
        analysed=analysed,
    )


@router.get(
    "/{project_id}/video",
    response_class=FileResponse,
    responses={200: {"description": "The video file.", "content": {"video/*": {}}}},
)
def get_video(project_id: str, storage: StorageDep) -> FileResponse:
    """The source video as uploaded. Supports HTTP Range, so a ``<video>`` element can seek."""
    project = _open(storage, project_id)
    path = storage.video_path(project.info.video)
    if not path.is_file():
        logger.error("project %s: its video %s is missing", project.id, path)
        raise HTTPException(status_code=404, detail="this project's video is missing")
    media_type = _VIDEO_TYPES.get(project.info.video.ext, "application/octet-stream")
    return FileResponse(path, media_type=media_type)


@router.post("/{project_id}/requests")
def send_request(project_id: str, body: ChatRequest, storage: StorageDep) -> ChatReply:
    """Plan a chat request, run the plan, and reply. 409 if the project is already handling one.

    ``applied`` means every op of the plan is in the timeline. A step the toolbox refuses gives
    ``not_applied`` and writes nothing.
    """
    project = _open(storage, project_id)
    with _one_request_at_a_time(project.id):
        _refuse_while_waiting(project, "before the next request")
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
            reply = ChatReply(request_id=request_id, status="clarify", reply=result.question)
        elif needs_preview(result):
            reply = _propose(storage, project, result, request_id, body.text)
        else:
            reply = _run(storage, project, result, request_id)
        _remember(project, body.text, reply)
        return reply


@router.get("/{project_id}/messages")
def get_messages(project_id: str, storage: StorageDep) -> list[ChatMessage]:
    """The chat so far, oldest first, so a reload restores the conversation."""
    project = _open(storage, project_id)
    # A request that was proposed and then settled has two records: the latest one wins.
    latest = {record.request_id: record for record in project.chat()}
    return [ChatMessage.model_validate(record.model_dump()) for record in latest.values()]


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
        # F-08's errors: also the model server failing, or a corrupt cached transcript (the
        # log names the file to delete). TranscribeError is one of them.
        except ModuleError as e:
            logger.warning("transcribing %s failed: %s", project.id, e)
            raise HTTPException(
                status_code=503, detail="transcription isn't available on this server"
            ) from e
        except NoAudioStream as e:
            raise HTTPException(status_code=400, detail="this video has no audio") from e
        except (VideoError, ValueError) as e:  # ValueError: e.g. an audio stream with no frames
            logger.exception("reading the audio of %s failed", project.id)
            raise HTTPException(status_code=500, detail="couldn't read the video's audio") from e


@router.get("/{project_id}/history")
def get_history(project_id: str, storage: StorageDep) -> HistoryReply:
    """The requests in the op log, and whether undo and redo have something to act on."""
    return _history(_open(storage, project_id))


@router.post("/{project_id}/undo")
def undo(project_id: str, storage: StorageDep) -> HistoryReply:
    """Undo the latest request in effect. 400 if there is nothing to undo."""
    return _undo_or_redo(storage, project_id, "undo")


@router.post("/{project_id}/redo")
def redo(project_id: str, storage: StorageDep) -> HistoryReply:
    """Bring back the most recently undone request. 400 if there is nothing to redo."""
    return _undo_or_redo(storage, project_id, "redo")


@router.get("/{project_id}/tracks")
def get_tracks(project_id: str, storage: StorageDep) -> TracksReply:
    """Where the targets are over time, for drawing boxes over the preview."""
    analysed, tracks = _tracks(storage, _open(storage, project_id))
    return TracksReply(analysed=analysed, tracks=tracks)


@router.get("/{project_id}/review")
def get_review(project_id: str, storage: StorageDep) -> ReviewReply:
    """The stretches of the masks a person should check (F-41), most serious first."""
    project = _open(storage, project_id)
    analysed, tracks = _tracks(storage, project)
    return _review(project, analysed, tracks, _frame_index(storage, project))


@router.post("/{project_id}/review/{item_id}")
def act_on_review_item(
    project_id: str, item_id: str, body: ReviewAction, storage: StorageDep
) -> ReviewReply:
    """Confirm one review item or cut it out, as a hand edit, and return the new queue.

    404 if the item is no longer in the queue (someone else's edit changed it); 400 if the
    toolbox refuses.
    """
    project = _open(storage, project_id)
    with _one_request_at_a_time(project.id):
        _refuse_while_waiting(project, "before reviewing")
        analysed, tracks = _tracks(storage, project)
        index = _frame_index(storage, project)
        queue = _review(project, analysed, tracks, index)
        item = next((i for i in queue.items if i.item_id == item_id), None)
        if item is None:
            raise HTTPException(
                status_code=404, detail="this spot is no longer in the review queue"
            )
        time_range = {"start": item.start, "end": item.end}
        if body.action == "confirm":
            call = ToolCall(
                step_id="s1",
                tool="confirm_mask",
                args={"targets": [item.track_id], "time_range": time_range},
            )
        else:
            call = ToolCall(step_id="s1", tool="cut_range", args={"time_range": time_range})
        try:
            done = execute_plan(
                Plan(calls=(call,)),
                project,
                request_id=new_id("r"),
                source="user",
                duration=index.end,
                tracks=tracks,
                registry=UI_TOOLS,
            )
        except (OSError, ValueError) as e:  # an unreadable timeline, or a write that failed
            logger.exception("%s on a review item of project %s failed", body.action, project.id)
            raise HTTPException(
                status_code=500,
                detail="couldn't apply this: the project's files can't be read or written.",
            ) from e
        if not done.ok:
            reason = (done.error or "the edit was refused").removeprefix(f"{call.tool}: ")
            raise HTTPException(status_code=400, detail=reason)
        return _review(project, analysed, tracks, index)


@router.post("/{project_id}/analysis")
def analyse(project_id: str, storage: StorageDep) -> AnalysisReply:
    """Make sure the video has a vision analysis (F-10). 503 when this server can't provide one.

    For now this only replays the shared cache: no detector is connected to the API yet, on any
    backend, so a video nobody analysed gets 503 rather than an empty result.
    """
    project = _open(storage, project_id)
    with _one_request_at_a_time(project.id):
        analysed, tracks = _tracks(storage, project)
        if not analysed:
            raise HTTPException(status_code=503, detail=_NO_ANALYSIS)
        return AnalysisReply(analysed=True, tracks=len(tracks))


@router.post("/{project_id}/exports")
def create_export(project_id: str, storage: StorageDep) -> ExportReply:
    """Render the timeline into a new MP4 in the project's ``exports/``. 422 with the reason
    when the timeline has an edit the renderer can't apply yet, or cuts that leave nothing."""
    project = _open(storage, project_id)
    with _one_request_at_a_time(project.id):
        _, tracks = _tracks(storage, project)
        name = f"{new_id('export')}.mp4"
        try:
            result = export(
                storage.video_path(project.info.video),
                project.state(),
                tracks,
                project.exports_dir / name,
                noise_profile=lambda rate, layout: cached_noise_profile(
                    storage, project.info.video, rate, layout
                ),
            )
        except (UnsupportedEdit, NothingToRender) as e:
            # These two only name edits (rule ids, speakers, filters), never files.
            raise HTTPException(status_code=422, detail=f"can't export this timeline: {e}") from e
        except (RenderError, VideoError, ModuleError, OSError, ValueError) as e:
            logger.exception("exporting project %s failed", project.id)
            raise HTTPException(status_code=500, detail="couldn't render this project") from e
        return ExportReply(
            name=name,
            url=f"/api/projects/{project.id}/exports/{name}",
            duration=result.duration,
            issues=[
                f"{i.track_id}: {i.message} ({clock(i.first_seen)}–{clock(i.last_seen)})"
                for i in result.issues
            ],
        )


@router.get(
    "/{project_id}/exports/{name}",
    response_class=FileResponse,
    responses={200: {"description": "The MP4.", "content": {"video/mp4": {}}}},
)
def get_export(project_id: str, name: str, storage: StorageDep) -> FileResponse:
    """One rendered MP4, by the name ``POST .../exports`` returned."""
    project = _open(storage, project_id)
    # The name is checked before it goes anywhere near a path.
    if not _EXPORT_NAME.fullmatch(name) or not (project.exports_dir / name).is_file():
        raise HTTPException(status_code=404, detail="no such export")
    return FileResponse(project.exports_dir / name, media_type="video/mp4")


# --- Preview before apply (F-44) -------------------------------------------------------------


@router.get("/{project_id}/preview")
def get_preview(project_id: str, storage: StorageDep) -> PreviewReply:
    """The edit waiting for a go-ahead, shown as the state it would make. 404 if none waits.

    Worked out again from the current timeline each time, so it never shows a stale picture.
    An edit that can't run any more (the timeline changed, or the saved plan is no good) is
    still answered, with ``problem`` set, so it can be seen and discarded.
    """
    project = _open(storage, project_id)
    pending = _pending_or_404(project)
    duration = _probe(storage, project).duration
    log = project.log()
    before = fold(log.ops)
    try:
        done, plan = _dry_run(storage, project, pending)
    except PreviewProblem as e:
        return _problem_reply(pending, before, duration, str(e), [])
    if not done.ok:  # the timeline moved on: say why, show it as it is, allow only a discard
        why = explain_failure(plan, done)
        return _problem_reply(pending, before, duration, why, list(done.steps))
    after = candidate_state(log, done.ops)
    return PreviewReply(
        request_id=pending.request_id,
        text=pending.text,
        reply=describe(plan, done),
        steps=list(done.steps),
        state=after,
        preview=summarise(before, after, duration),
    )


@router.post("/{project_id}/preview/accept")
def accept_preview(project_id: str, storage: StorageDep) -> ChatReply:
    """Apply the waiting edit. It is checked again against the timeline as it is now, so a
    timeline that changed meanwhile can still refuse it (``not_applied``, nothing written)."""
    project = _open(storage, project_id)
    with _one_request_at_a_time(project.id):
        pending = _pending_or_404(project)
        try:
            plan = _plan_of(pending)
        except PreviewProblem as e:  # the saved plan is no good: settle it, write nothing
            reply = ChatReply(
                request_id=pending.request_id,
                status="not_applied",
                reply=f"I couldn't apply this preview: {e} Nothing was changed.",
            )
        else:
            reply = _run(storage, project, plan, pending.request_id)
        _settle(project, pending, reply)
        return reply


@router.post("/{project_id}/preview/reject")
def reject_preview(project_id: str, storage: StorageDep) -> ChatReply:
    """Throw the waiting edit away. Nothing is written."""
    project = _open(storage, project_id)
    with _one_request_at_a_time(project.id):
        pending = _pending_or_404(project)
        reply = ChatReply(
            request_id=pending.request_id,
            status="not_applied",
            reply="Discarded the preview. Nothing was changed.",
        )
        _settle(project, pending, reply)
        return reply


# --- Helpers --------------------------------------------------------------------------------


def _probe(storage: Storage, project: Project) -> VideoInfo:
    try:
        return probe(storage.video_path(project.info.video))
    except (VideoError, OSError) as e:
        logger.exception("probing the video of project %s failed", project.id)
        raise HTTPException(status_code=500, detail="couldn't read this project's video") from e


def _tracks(storage: Storage, project: Project) -> tuple[bool, list[Track]]:
    """``(analysed, tracks)``: the video's cached tracks plus the project's hand-drawn ones.

    Hand-drawn tracks come back even when the video was never analysed, because the preview and
    the renderer need them. (The toolbox doesn't get them then: ``project_tracks``.) An analysis
    that can't be trusted is 503, not "none", so nothing renders without tracks it should have.
    """
    try:
        cached = load_tracks(storage, project.info.video.sha256)
        drawn = project.user_tracks()
    except (CacheError, ValidationError) as e:
        logger.warning("tracks for project %s can't be used: %s", project.id, e)
        raise HTTPException(status_code=503, detail=_BAD_ANALYSIS) from e
    return cached is not None, [*(cached or []), *drawn]


@functools.lru_cache(maxsize=8)
def _cached_frame_index(path: str) -> FrameIndex:
    # Videos are content-addressed by sha256, so a path never changes what it holds and the
    # cache needs no invalidation.
    return FrameIndex.build(path)


def _frame_index(storage: Storage, project: Project) -> FrameIndex:
    try:
        return _cached_frame_index(str(storage.video_path(project.info.video)))
    except (VideoError, OSError, ValueError) as e:
        logger.exception("reading the frames of project %s failed", project.id)
        raise HTTPException(status_code=500, detail="couldn't read this project's video") from e


def _review(
    project: Project, analysed: bool, tracks: list[Track], index: FrameIndex
) -> ReviewReply:
    queue = review_queue(
        project.state(), tracks, duration=index.end, frame_times=index.times.tolist()
    )
    return ReviewReply(
        analysed=analysed,
        items=queue.items,
        held_seconds=queue.held_seconds,
        rules_not_audited=queue.rules_not_audited,
    )


def _run(storage: Storage, project: Project, plan: Plan, request_id: str) -> ChatReply:
    """Run a plan and say what happened. Only a plan whose ops were written is ``applied``."""
    try:
        done = execute_request(storage, project, plan, request_id)
    except (PlanError, VideoError, OSError, ValueError) as e:
        # A missing video, an unreadable timeline, a failed write: not the user's fault.
        logger.exception("running %s on project %s failed", request_id, project.id)
        raise HTTPException(
            status_code=500,
            detail="couldn't apply this request: the project's files can't be read or written.",
        ) from e
    status: ReplyStatus = "not_applied"
    if done.ok and done.ops:
        status, text = "applied", describe(plan, done)
    elif done.ok:  # no tool does this today; never report an edit that wrote nothing
        text = "That changes nothing, so nothing was written."
    else:
        text = explain_failure(plan, done)
    return ChatReply(request_id=request_id, status=status, reply=text, steps=list(done.steps))


class PreviewProblem(Exception):
    """A saved plan that can't be run at all (unknown tool, wrong shape): not a server fault,
    and not something to retry. The message says why, for the user."""


def _waiting(project: Project) -> PendingPreview | None:
    """The preview waiting for a go-ahead, or None.

    A saved preview whose request already wrote its ops was applied; only the cleanup after it
    didn't finish (a crash, a full disk). It is not waiting, so it is cleared and never applied
    a second time.
    """
    pending = project.pending()
    if pending is None:
        return None
    if any(op.request_id == pending.request_id for op in project.log()):
        try:
            project.clear_pending()
        except OSError:
            logger.exception("clearing the applied preview of project %s failed", project.id)
        return None
    return pending


def _refuse_while_waiting(project: Project, what: str) -> None:
    """409 while a cut or keep waits: its preview was made on the timeline as it is now."""
    if _waiting(project) is not None:
        raise HTTPException(
            status_code=409, detail=f"a preview is waiting: apply it or discard it {what}"
        )


def _pending_or_404(project: Project) -> PendingPreview:
    pending = _waiting(project)
    if pending is None:
        raise HTTPException(status_code=404, detail="no preview is waiting")
    return pending


def _plan_of(pending: PendingPreview) -> Plan:
    try:
        return Plan.model_validate(pending.plan)
    except ValidationError as e:
        logger.warning("the saved plan of %s is not a plan: %s", pending.request_id, e)
        raise PreviewProblem("the saved plan is damaged.") from e


def _dry_run(storage: Storage, project: Project, pending: PendingPreview):
    plan = _plan_of(pending)
    try:
        return execute_request(storage, project, plan, pending.request_id, commit=False), plan
    except PlanError as e:  # e.g. a tool the toolbox no longer has
        logger.warning("the saved plan of %s is not valid now: %s", pending.request_id, e)
        raise PreviewProblem("the saved plan is no longer valid.") from e
    except (VideoError, OSError, ValueError) as e:
        logger.exception("previewing %s on project %s failed", pending.request_id, project.id)
        raise HTTPException(
            status_code=500, detail="couldn't preview this request: the project's files fail"
        ) from e


def _problem_reply(
    pending: PendingPreview,
    now: TimelineState,
    duration: float,
    why: str,
    steps: list[StepReport],
) -> PreviewReply:
    """A preview that can't be applied: the timeline as it is, and why. Only a discard is left."""
    return PreviewReply(
        request_id=pending.request_id,
        text=pending.text,
        reply=why,
        steps=steps,
        state=now,
        preview=summarise(now, now, duration),
        problem=why,
    )


def _propose(
    storage: Storage, project: Project, plan: Plan, request_id: str, text: str
) -> ChatReply:
    """Dry-run a heavy plan and keep it for a go-ahead. A plan a tool refuses is reported as
    ``not_applied`` right away, like any other, and leaves nothing waiting."""
    pending = PendingPreview(
        request_id=request_id, text=text, plan=plan.model_dump(mode="json"), at=datetime.now(UTC)
    )
    try:
        done, _ = _dry_run(storage, project, pending)
    except PreviewProblem as e:  # a plan the planner just made: a bug, not the user's fault
        logger.exception("proposing %s failed: %s", request_id, e)
        raise HTTPException(
            status_code=500, detail="couldn't plan this request. Nothing was changed."
        ) from e
    if not done.ok:
        return ChatReply(
            request_id=request_id,
            status="not_applied",
            reply=explain_failure(plan, done),
            steps=list(done.steps),
        )
    log = project.log()
    before, after = fold(log.ops), candidate_state(log, done.ops)
    if before == after:  # e.g. cutting what is already cut: nothing to look at or apply
        return ChatReply(
            request_id=request_id,
            status="not_applied",
            reply="That's already in place, so nothing would change. Nothing was changed.",
            steps=list(done.steps),
        )
    try:
        project.set_pending(pending)
    except OSError as e:
        logger.exception("saving the preview of project %s failed", project.id)
        raise HTTPException(
            status_code=500, detail="couldn't keep this preview. Nothing was changed."
        ) from e
    summary = summarise(before, after, _probe(storage, project).duration)
    return ChatReply(
        request_id=request_id,
        status="preview",
        reply=f"{describe(plan, done)} Nothing is changed until you apply it.",
        steps=list(done.steps),
        preview=summary,
    )


def _settle(project: Project, pending: PendingPreview, reply: ChatReply) -> None:
    """The preview is answered: forget it, and let the chat show how it ended."""
    try:
        project.clear_pending()
    except OSError:
        # The edit (or its refusal) is done; a leftover file is recognised as settled later
        # (`_waiting`), so this must not turn a success into an error.
        logger.exception("clearing the preview of project %s failed", project.id)
    _remember(project, pending.text, reply)


def _remember(project: Project, text: str, reply: ChatReply) -> None:
    """Add the request and its reply to the project's chat. The edit is already written (or
    refused) by now, so a chat that can't be saved is logged and doesn't fail the request."""
    record = ChatRecord(
        request_id=reply.request_id,
        text=text,
        status=reply.status,
        reply=reply.reply,
        steps=[step.model_dump(mode="json") for step in reply.steps],
        at=datetime.now(UTC),
    )
    try:
        project.append_chat(record)
    except OSError:
        logger.exception("saving the chat of project %s failed", project.id)


def _history(project: Project) -> HistoryReply:
    log = project.log()
    history = log.history()
    chat = {record.request_id: record for record in project.chat()}
    kinds: dict[str, list[str]] = {}
    sources: dict[str, Source] = {}
    for op in log:
        if isinstance(op, Undo | Redo):
            continue
        kinds.setdefault(op.request_id, []).append(op.kind)
        sources.setdefault(op.request_id, op.source)
    return HistoryReply(
        requests=[
            HistoryEntry(
                request_id=request_id,
                source=sources[request_id],
                status="undone" if request_id in history.undone else "applied",
                kinds=kinds[request_id],
                text=chat[request_id].text if request_id in chat else None,
                reply=chat[request_id].reply if request_id in chat else None,
            )
            for request_id in kinds
        ],
        can_undo=history.can_undo is not None,
        can_redo=history.can_redo is not None,
    )


def _undo_or_redo(storage: Storage, project_id: str, tool: Literal["undo", "redo"]) -> HistoryReply:
    """A hand edit: through the toolbox like the agent's, as the user, with its own request id."""
    project = _open(storage, project_id)
    with _one_request_at_a_time(project.id):
        _refuse_while_waiting(project, f"before {tool}")
        plan = Plan(calls=(ToolCall(step_id="s1", tool=tool),))
        try:
            done = execute_plan(plan, project, request_id=new_id("r"), source="user")
        except (OSError, ValueError) as e:  # an unreadable timeline, or a write that failed
            logger.exception("%s on project %s failed", tool, project.id)
            raise HTTPException(
                status_code=500,
                detail=f"couldn't {tool}: the project's files can't be read or written.",
            ) from e
        if not done.ok:
            reason = (done.error or f"nothing to {tool}").removeprefix(f"{tool}: ")
            raise HTTPException(status_code=400, detail=reason)
        return _history(project)
