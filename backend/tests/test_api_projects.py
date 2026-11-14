"""Project routes (F-64) in stub mode, end to end: upload, chat requests that run, history and
undo, tracks and analysis, export, the chat, one request at a time.

The test clips are encoded with PyAV, so these tests don't need the ffmpeg CLI.
"""

import re
import tempfile
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest
from fastapi.testclient import TestClient

from vibe.api import projects
from vibe.api.app import app
from vibe.contract import Track, Undo
from vibe.modules.vision import save_tracks
from vibe.storage import Storage
from vibe.toolbox import ToolContext, call_tool
from vibe.video import extract_audio, probe

LONG_SECONDS, FPS, SAMPLE_RATE = 10, 10, 16000


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[Storage]:
    store = Storage(tmp_path / "data")
    app.dependency_overrides[projects.get_storage] = lambda: store
    yield store
    app.dependency_overrides.clear()


@pytest.fixture
def client(storage: Storage) -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One second, no sound: enough for the upload tests."""
    path = tmp_path_factory.mktemp("clips") / "street walk.mp4"
    with av.open(str(path), "w") as out:
        stream = out.add_stream("mpeg4", rate=10)
        stream.width, stream.height, stream.pix_fmt = 32, 32, "yuv420p"
        for i in range(10):
            image = np.full((32, 32, 3), i * 20, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            out.mux(stream.encode(frame))
        out.mux(stream.encode())
    return path


@pytest.fixture(scope="module")
def long_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Ten seconds, 32x32 at 10 fps, with a 440 Hz tone: long enough to mute and cut parts of."""
    path = tmp_path_factory.mktemp("clips") / "demo.mp4"
    per_frame = SAMPLE_RATE // FPS
    t = np.arange(LONG_SECONDS * SAMPLE_RATE) / SAMPLE_RATE
    tone = (0.25 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    with av.open(str(path), "w") as out:
        video = out.add_stream("mpeg4", rate=FPS)
        video.width, video.height, video.pix_fmt = 32, 32, "yuv420p"
        sound = out.add_stream("aac", rate=SAMPLE_RATE, layout="mono")
        for i in range(LONG_SECONDS * FPS):
            image = np.full((32, 32, 3), i * 2, dtype=np.uint8)
            out.mux(video.encode(av.VideoFrame.from_ndarray(image, format="rgb24")))
            samples = tone[None, i * per_frame : (i + 1) * per_frame]
            frame = av.AudioFrame.from_ndarray(samples, format="fltp", layout="mono")
            frame.sample_rate, frame.pts = SAMPLE_RATE, i * per_frame
            frame.time_base = Fraction(1, SAMPLE_RATE)
            out.mux(sound.encode(frame))
        out.mux(video.encode())
        out.mux(sound.encode())
    return path


def upload(client: TestClient, clip: Path, name: str | None = None) -> dict:
    with clip.open("rb") as f:
        files = {"file": (name or clip.name, f, "video/mp4")}
        response = client.post("/api/projects", files=files)
    assert response.status_code == 201, response.text
    return response.json()


def empty(state: dict) -> bool:
    return state["mutes"] == [] and state["cuts"] == [] and state["masks"] == {}


# --- POST /api/projects ---------------------------------------------------------------------


def test_upload_creates_a_project_on_disk(client: TestClient, storage: Storage, clip: Path) -> None:
    info = upload(client, clip)
    assert info["name"] == "street walk"
    # The client's name is never a path: storage sees the upload as upload<ext>.
    assert info["video"]["original_name"] == "upload.mp4"
    assert info["video"]["ext"] == ".mp4"
    project = storage.open_project(info["project_id"])
    assert project.timeline_path.exists()
    assert (storage.videos_dir / f"{info['video']['sha256']}.mp4").exists()


def test_upload_rejects_a_file_that_is_not_a_video(
    client: TestClient, storage: Storage, tmp_path: Path
) -> None:
    response = client.post("/api/projects", files={"file": ("notes.mp4", b"not a video")})
    assert response.status_code == 400
    assert storage.list_projects() == []


def test_upload_needs_a_file(client: TestClient) -> None:
    assert client.post("/api/projects").status_code == 422


@pytest.mark.parametrize(
    ("filename", "name", "ext"),
    [
        ("..", "upload", ""),
        ("../../etc/passwd.mp4", "passwd", ".mp4"),
        ("C:\\Users\\me\\clip.MOV", "clip", ".mov"),
        ("a" * 300 + ".mp4", "a" * 100, ".mp4"),
        ("CON.mp4", "CON", ".mp4"),
        ("\u00e9t\u00e9 \u6771\u4eac.mkv", "\u00e9t\u00e9 \u6771\u4eac", ".mkv"),
        (".mp4", "upload", ".mp4"),
    ],
)
def test_unsafe_upload_names_are_cleaned(
    client: TestClient, clip: Path, filename: str, name: str, ext: str
) -> None:
    info = upload(client, clip, name=filename)
    assert info["name"] == name
    assert info["video"]["ext"] == ext
    assert info["video"]["original_name"] == f"upload{ext}"


def test_nul_byte_in_upload_name_is_dropped(client: TestClient, clip: Path) -> None:
    # Built by hand: the test client would percent-encode the NUL instead of sending it.
    body = (
        b"--b\r\n"
        b'Content-Disposition: form-data; name="file"; filename="bad\x00name.mp4"\r\n'
        b"Content-Type: video/mp4\r\n\r\n" + clip.read_bytes() + b"\r\n--b--\r\n"
    )
    headers = {"content-type": "multipart/form-data; boundary=b"}
    response = client.post("/api/projects", content=body, headers=headers)
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "badname"


def test_readable_video_with_an_odd_extension_is_accepted(client: TestClient, clip: Path) -> None:
    info = upload(client, clip, name="street walk.final-cut")
    assert info["name"] == "street walk.final-cut"
    assert info["video"]["ext"] == ""


def test_too_big_an_upload_is_413(
    client: TestClient, storage: Storage, clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects, "MAX_UPLOAD_BYTES", clip.stat().st_size - 1)
    with clip.open("rb") as f:
        response = client.post("/api/projects", files={"file": (clip.name, f, "video/mp4")})
    assert response.status_code == 413
    assert storage.list_projects() == []


def test_errors_dont_reveal_server_paths(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    sha = storage.open_project(project_id).info.video.sha256
    for key in ("k-aaaa1111", "k-bbbb2222"):  # two analyses and no default: unusable
        save_tracks(storage, sha, key, [face()])
    ask(client, project_id, "reduce the noise")  # an edit the renderer can't apply yet
    base = f"/api/projects/{project_id}"
    responses = [
        client.post("/api/projects", files={"file": ("notes.mp4", b"not a video")}),
        client.get("/api/projects/prj_missing"),
        client.get("/api/projects/prj_missing/state"),
        client.get("/api/projects/bad id!/state"),
        client.get("/api/projects/prj_missing/video"),
        client.post("/api/projects/prj_missing/requests", json={"text": "blur faces"}),
        client.post("/api/projects/prj_missing/undo"),
        client.post("/api/projects/prj_missing/exports"),
        client.get(f"{base}/exports/export_000000000000.mp4"),
        client.get(f"{base}/exports/project.json"),
        client.get(f"{base}/tracks"),
        client.post(f"{base}/analysis"),
        client.post(f"{base}/exports"),
        client.post(f"{base}/redo"),
    ]
    # And the failures that come from the files themselves.
    storage.video_path(storage.open_project(project_id).info.video).unlink()
    responses += [
        client.get(base),
        client.get(f"{base}/video"),
        client.post(f"{base}/requests", json={"text": "mute the audio"}),
    ]
    for response in responses:
        assert response.status_code >= 400, response.text
        for secret in (str(storage.root), tempfile.gettempdir(), "vibe-upload-", sha):
            assert secret not in response.text, response.text


# --- Helpers for the editing routes ---------------------------------------------------------


def ask(client: TestClient, project_id: str, text: str) -> dict:
    response = client.post(f"/api/projects/{project_id}/requests", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json()


def get(client: TestClient, project_id: str, what: str) -> dict:
    response = client.get(f"/api/projects/{project_id}/{what}")
    assert response.status_code == 200, response.text
    return response.json()


def face(track_id: str = "trk_1", source: str = "detector") -> Track:
    keyframes = [{"t": 0, "box": (0.25, 0.25, 0.75, 0.75), "conf": 0.9, "state": "detected"}]
    return Track.model_validate(
        {"track_id": track_id, "label": "face", "source": source, "keyframes": keyframes}
    )


def analysed(storage: Storage, project_id: str) -> str:
    """Put one face track in the video's analysis cache, as a GPU machine would. Its id."""
    sha = storage.open_project(project_id).info.video.sha256
    return save_tracks(storage, sha, "k-aaaa1111", [face()])[0].track_id


def log_bytes(storage: Storage, project_id: str) -> bytes:
    return storage.open_project(project_id).timeline_path.read_bytes()


# --- GET /api/projects and /api/projects/{id} -----------------------------------------------


def test_projects_are_listed_newest_first(client: TestClient, clip: Path) -> None:
    assert client.get("/api/projects").json() == []
    first = upload(client, clip, name="first.mp4")
    second = upload(client, clip, name="second.mp4")

    response = client.get("/api/projects")

    assert response.status_code == 200
    assert response.json() == [second, first]


def test_project_detail_has_the_videos_facts(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    info = upload(client, long_clip)

    detail = get(client, info["project_id"], "")

    assert detail["info"] == info
    assert detail["video"] == {
        "duration": pytest.approx(LONG_SECONDS, abs=0.11),
        "width": 32,
        "height": 32,
        "fps": FPS,
        "has_audio": True,
    }
    assert detail["analysed"] is False

    analysed(storage, info["project_id"])
    assert get(client, info["project_id"], "")["analysed"] is True


def test_project_detail_of_a_silent_clip(client: TestClient, clip: Path) -> None:
    detail = get(client, upload(client, clip)["project_id"], "")
    assert detail["video"]["has_audio"] is False
    assert detail["video"]["duration"] == pytest.approx(1.0)


# --- GET /api/projects/{id}/video -----------------------------------------------------------


def test_video_route_serves_the_stored_file(client: TestClient, long_clip: Path) -> None:
    project_id = upload(client, long_clip)["project_id"]

    response = client.get(f"/api/projects/{project_id}/video")

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.content == long_clip.read_bytes()


def test_video_route_honours_a_range_header(client: TestClient, long_clip: Path) -> None:
    project_id = upload(client, long_clip)["project_id"]
    data = long_clip.read_bytes()

    part = client.get(f"/api/projects/{project_id}/video", headers={"Range": "bytes=100-299"})
    tail = client.get(f"/api/projects/{project_id}/video", headers={"Range": "bytes=-50"})
    beyond = client.get(
        f"/api/projects/{project_id}/video", headers={"Range": f"bytes={len(data)}-"}
    )

    assert part.status_code == 206
    assert part.headers["content-range"] == f"bytes 100-299/{len(data)}"
    assert part.headers["content-type"] == "video/mp4"
    assert part.content == data[100:300]
    assert (tail.status_code, tail.content) == (206, data[-50:])
    assert beyond.status_code == 416


@pytest.mark.parametrize(
    ("filename", "media_type"),
    [
        ("clip.MOV", "video/quicktime"),
        ("clip.webm", "video/webm"),
        ("clip.final-cut", "application/octet-stream"),
    ],
)
def test_video_media_type_follows_the_stored_extension(
    client: TestClient, clip: Path, filename: str, media_type: str
) -> None:
    project_id = upload(client, clip, name=filename)["project_id"]
    response = client.get(f"/api/projects/{project_id}/video")
    assert response.headers["content-type"] == media_type


def test_missing_video_file_is_404(client: TestClient, storage: Storage, clip: Path) -> None:
    project_id = upload(client, clip)["project_id"]
    storage.video_path(storage.open_project(project_id).info.video).unlink()
    assert client.get(f"/api/projects/{project_id}/video").status_code == 404


# --- GET /api/projects/{id}/state -----------------------------------------------------------


def test_new_project_has_an_empty_timeline(client: TestClient, clip: Path) -> None:
    project_id = upload(client, clip)["project_id"]
    response = client.get(f"/api/projects/{project_id}/state")
    assert response.status_code == 200
    state = response.json()
    assert state["schema_version"] == 1
    assert state["masks"] == {} and state["cuts"] == [] and state["mutes"] == []


GETS = ["", "/state", "/video", "/history", "/messages", "/tracks", "/exports/export_0.mp4"]
POSTS = ["/undo", "/redo", "/analysis", "/exports"]


@pytest.mark.parametrize("project_id", ["prj_missing", "bad id!"])
def test_unknown_project_is_404(client: TestClient, project_id: str) -> None:
    for path in GETS:
        assert client.get(f"/api/projects/{project_id}{path}").status_code == 404, path
    for path in POSTS:
        assert client.post(f"/api/projects/{project_id}{path}").status_code == 404, path
    response = client.post(f"/api/projects/{project_id}/requests", json={"text": "blur faces"})
    assert response.status_code == 404


# --- POST /api/projects/{id}/requests -------------------------------------------------------


def test_mute_request_is_applied_and_shows_in_the_state(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]

    reply = ask(client, project_id, "mute the first 5 seconds")

    assert reply["status"] == "applied"
    assert reply["reply"] == "Muted 0:00–0:05."
    assert [(s["tool"], s["ok"]) for s in reply["steps"]] == [
        ("resolve_time", True),
        ("mute", True),
    ]
    assert reply["steps"][1]["args"] == {"time_range": {"start": 0.0, "end": 5.0}}
    (mute,) = get(client, project_id, "state")["mutes"]
    assert (mute["start"], mute["end"], mute["speaker"]) == (0, 5, None)
    assert mute["request_id"] == reply["request_id"]
    (op,) = storage.open_project(project_id).log().ops
    assert (op.request_id, op.source) == (reply["request_id"], "agent")
    assert reply["steps"][1]["result"] == {"op_ids": [op.op_id]}


def test_blur_without_an_analysis_is_not_applied_and_writes_nothing(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    # Fail closed: "blur faces" must not look done when nothing knows where the faces are.
    project_id = upload(client, long_clip)["project_id"]
    before = log_bytes(storage, project_id)

    reply = ask(client, project_id, "blur faces")

    assert reply["status"] == "not_applied"
    assert "no analysis for this video" in reply["reply"]
    assert "Nothing was changed" in reply["reply"]
    assert [(s["tool"], s["ok"], s["result"]) for s in reply["steps"]] == [
        ("find_objects", False, None)
    ]
    assert log_bytes(storage, project_id) == before
    assert empty(get(client, project_id, "state"))


def test_blur_with_tracks_in_the_cache_is_applied(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    track_id = analysed(storage, project_id)

    reply = ask(client, project_id, "blur faces from 0:02 to 0:04")

    assert reply["status"] == "applied"
    assert reply["reply"] == "Blurred 1 face (0:02–0:04)."
    (span,) = get(client, project_id, "state")["masks"][track_id]
    assert (span["start"], span["end"], span["effect"]["type"]) == (2, 4, "blur")
    assert span["request_id"] == reply["request_id"]


def test_blur_when_the_analysis_found_no_such_thing_is_not_applied(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    analysed(storage, project_id)  # a face, no plates

    reply = ask(client, project_id, "blur license plates")

    assert reply["status"] == "not_applied"
    assert reply["reply"].startswith("I found no license plates in this video")
    assert len(storage.open_project(project_id).log()) == 0


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("mute 0:20 to 0:30", "end of the video"),
        ("cut the first 2 minutes", "as long as the whole video"),
        ("unmute the audio", "isn't muted"),
        # The first edit is fine and the second isn't: neither is written.
        ("mute 0:01 to 0:02 and cut 0:20 to 0:30", "end of the video"),
        ("cut 0:01 to 0:02, then blur faces from 0:03 to 0:04", "no analysis"),
    ],
)
def test_a_refused_request_is_not_applied_and_writes_nothing(
    client: TestClient, storage: Storage, long_clip: Path, text: str, reason: str
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    before = log_bytes(storage, project_id)

    reply = ask(client, project_id, text)

    assert reply["status"] == "not_applied"
    assert reason in reply["reply"]
    assert reply["steps"][-1]["ok"] is False
    assert log_bytes(storage, project_id) == before
    assert empty(get(client, project_id, "state"))


def test_a_requests_ops_are_contiguous_and_share_its_id(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    first = ask(client, project_id, "mute 0:01 to 0:02")

    second = ask(client, project_id, "keep 0:01 to 0:08 and mute 0:03 to 0:04")

    assert second["reply"] == "Kept only 0:01–0:08; the rest is cut. Muted 0:03–0:04."
    ops = storage.open_project(project_id).log().ops
    assert [(op.kind, op.request_id) for op in ops] == [
        ("mute", first["request_id"]),
        ("cut", second["request_id"]),
        ("cut", second["request_id"]),
        ("mute", second["request_id"]),
    ]


def test_clarify_replies_without_writing_ops(client: TestClient, clip: Path) -> None:
    project_id = upload(client, clip)["project_id"]
    response = client.post(f"/api/projects/{project_id}/requests", json={"text": "cut it"})
    assert response.status_code == 200
    assert response.json()["status"] == "clarify"
    assert response.json()["reply"].strip()
    assert response.json()["steps"] == []

    assert empty(client.get(f"/api/projects/{project_id}/state").json())


def test_each_request_gets_its_own_id(client: TestClient, long_clip: Path) -> None:
    project_id = upload(client, long_clip)["project_id"]
    first = ask(client, project_id, "mute 0:01 to 0:02")
    second = ask(client, project_id, "blur faces")
    third = ask(client, project_id, "cut it")
    ids = [first["request_id"], second["request_id"], third["request_id"]]
    assert all(i.startswith("r_") for i in ids)
    assert len(set(ids)) == 3


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (projects.PlanError("template plan is invalid"), 500),
        (ValueError("bad value"), 500),
        (projects.LlmError("rate limited"), 503),
    ],
)
def test_planner_failures_get_a_clear_error_and_write_nothing(
    client: TestClient,
    storage: Storage,
    clip: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: int,
) -> None:
    project_id = upload(client, clip)["project_id"]

    def fail(text: str) -> None:
        raise error

    monkeypatch.setattr(projects, "plan_request", fail)
    response = client.post(f"/api/projects/{project_id}/requests", json={"text": "blur faces"})
    assert response.status_code == status
    assert "Nothing was changed" in response.json()["detail"]
    assert str(error) not in response.text
    assert len(storage.open_project(project_id).log()) == 0
    assert get(client, project_id, "messages") == []  # a failed request isn't part of the chat
    assert projects._busy == set()  # the project is free again


@pytest.mark.parametrize(
    "error",
    [
        OSError("[Errno 28] No space left on device: '/srv/data/timeline.jsonl'"),
        projects.PlanError("can't run an invalid plan"),
        projects.VideoError("/srv/data/videos/x.mp4: no decodable video frame"),
    ],
)
def test_a_failure_while_running_the_plan_is_a_500_with_a_fixed_message(
    client: TestClient,
    storage: Storage,
    long_clip: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    project_id = upload(client, long_clip)["project_id"]

    def fail(*args: object) -> None:
        raise error

    monkeypatch.setattr(projects, "execute_request", fail)
    response = client.post(f"/api/projects/{project_id}/requests", json={"text": "mute the audio"})

    assert response.status_code == 500
    assert response.json()["detail"].startswith("couldn't apply this request")
    assert str(error) not in response.text and "/srv" not in response.text
    assert get(client, project_id, "messages") == []
    assert projects._busy == set()


@pytest.mark.parametrize("body", [{"text": ""}, {"text": "   "}, {}, {"text": "hi", "x": 1}])
def test_bad_request_body_is_422(client: TestClient, clip: Path, body: dict) -> None:
    project_id = upload(client, clip)["project_id"]
    response = client.post(f"/api/projects/{project_id}/requests", json=body)
    assert response.status_code == 422


# --- One request at a time ------------------------------------------------------------------


def test_busy_project_gets_409_and_writes_nothing(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    ask(client, project_id, "mute 0:01 to 0:02")
    before = log_bytes(storage, project_id)
    base = f"/api/projects/{project_id}"

    with projects._one_request_at_a_time(project_id):  # another request is in flight
        responses = [client.post(f"{base}/requests", json={"text": "mute the audio"})]
        responses += [client.post(f"{base}{path}") for path in POSTS]
        reads = [client.get(f"{base}{path}") for path in GETS[:-1]]

    assert [r.status_code for r in responses] == [409] * 5
    assert {r.json()["detail"] for r in responses} == {"this project is busy with another request"}
    assert [r.status_code for r in reads] == [200] * len(reads)  # reading never waits
    assert log_bytes(storage, project_id) == before
    assert list(storage.open_project(project_id).exports_dir.iterdir()) == []
    # Free again once that request is done, and nothing is left behind per project.
    assert ask(client, project_id, "mute the audio")["status"] == "applied"
    assert client.post(f"{base}/undo").status_code == 200
    assert projects._busy == set()


# --- GET /api/projects/{id}/messages --------------------------------------------------------


def test_the_chat_persists_with_every_kind_of_reply(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    assert get(client, project_id, "messages") == []
    texts = ["cut it", "mute the first 5 seconds", "blur faces"]
    replies = [ask(client, project_id, text) for text in texts]

    # A new client and a new storage handle: what comes back was read from disk.
    reopened = Storage(storage.root)
    app.dependency_overrides[projects.get_storage] = lambda: reopened
    messages = get(TestClient(app), project_id, "messages")

    assert [m["text"] for m in messages] == texts
    assert [m["status"] for m in messages] == ["clarify", "applied", "not_applied"]
    for message, reply in zip(messages, replies, strict=True):
        assert {k: message[k] for k in reply} == reply
        assert message["at"].startswith("20")
    assert [r.request_id for r in reopened.open_project(project_id).chat()] == [
        r["request_id"] for r in replies
    ]


def test_a_chat_that_cant_be_saved_doesnt_undo_the_reply(
    client: TestClient,
    storage: Storage,
    long_clip: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_id = upload(client, long_clip)["project_id"]

    def fail(self: object, record: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(projects.Project, "append_chat", fail)
    reply = ask(client, project_id, "mute the audio")

    # The edit is in the timeline, so the reply says so; the lost chat line is logged.
    assert reply["status"] == "applied"
    assert len(get(client, project_id, "state")["mutes"]) == 1
    assert "saving the chat" in caplog.text


# --- GET /api/projects/{id}/history, POST .../undo and .../redo -----------------------------


def test_history_lists_the_requests_in_log_order_with_their_chat(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    assert get(client, project_id, "history") == {
        "requests": [],
        "can_undo": False,
        "can_redo": False,
    }
    mute = ask(client, project_id, "mute the first 5 seconds")
    ask(client, project_id, "cut it")  # a question: not in the log
    ask(client, project_id, "blur faces")  # refused: not in the log
    keep = ask(client, project_id, "keep 0:01 to 0:08")
    by_hand = ToolContext(request_id="r_hand", source="user", log=storage.open_project(project_id))
    call_tool("cut_range", {"time_range": {"start": 2, "end": 3}}, by_hand)

    history = get(client, project_id, "history")

    assert history["requests"] == [
        {
            "request_id": mute["request_id"],
            "source": "agent",
            "status": "applied",
            "kinds": ["mute"],
            "text": "mute the first 5 seconds",
            "reply": "Muted 0:00–0:05.",
        },
        {
            "request_id": keep["request_id"],
            "source": "agent",
            "status": "applied",
            "kinds": ["cut", "cut"],
            "text": "keep 0:01 to 0:08",
            "reply": keep["reply"],
        },
        {
            "request_id": "r_hand",
            "source": "user",
            "status": "applied",
            "kinds": ["cut"],
            "text": None,
            "reply": None,
        },
    ]
    assert (history["can_undo"], history["can_redo"]) == (True, False)


def test_undo_then_redo_round_trip(client: TestClient, storage: Storage, long_clip: Path) -> None:
    project_id = upload(client, long_clip)["project_id"]
    ask(client, project_id, "mute the first 5 seconds")
    after_mute = get(client, project_id, "state")
    ask(client, project_id, "keep 0:01 to 0:08")
    after_keep = get(client, project_id, "state")

    undone = client.post(f"/api/projects/{project_id}/undo")

    assert undone.status_code == 200
    assert [r["status"] for r in undone.json()["requests"]] == ["applied", "undone"]
    assert (undone.json()["can_undo"], undone.json()["can_redo"]) == (True, True)
    assert undone.json() == get(client, project_id, "history")
    assert get(client, project_id, "state") == after_mute  # both cuts of the keep are gone
    op = storage.open_project(project_id).log().ops[-1]
    assert isinstance(op, Undo) and op.source == "user" and op.request_id.startswith("r_")

    redone = client.post(f"/api/projects/{project_id}/redo")

    assert redone.status_code == 200
    assert [r["status"] for r in redone.json()["requests"]] == ["applied", "applied"]
    assert (redone.json()["can_undo"], redone.json()["can_redo"]) == (True, False)
    assert get(client, project_id, "state") == after_keep
    assert [o.kind for o in storage.open_project(project_id).log()] == [
        "mute", "cut", "cut", "undo", "redo",
    ]  # fmt: skip


def test_undo_all_the_way_back_then_a_new_edit_clears_redo(
    client: TestClient, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    base = f"/api/projects/{project_id}"
    ask(client, project_id, "mute 0:01 to 0:02")
    ask(client, project_id, "cut 0:03 to 0:04")

    client.post(f"{base}/undo")
    emptied = client.post(f"{base}/undo").json()

    assert [r["status"] for r in emptied["requests"]] == ["undone", "undone"]
    assert (emptied["can_undo"], emptied["can_redo"]) == (False, True)
    assert empty(get(client, project_id, "state"))

    ask(client, project_id, "mute 0:05 to 0:06")
    history = get(client, project_id, "history")
    assert [r["status"] for r in history["requests"]] == ["undone", "undone", "applied"]
    assert (history["can_undo"], history["can_redo"]) == (True, False)
    assert client.post(f"{base}/redo").status_code == 400


@pytest.mark.parametrize("action", ["undo", "redo"])
def test_nothing_to_undo_or_redo_is_400_and_writes_nothing(
    client: TestClient, storage: Storage, long_clip: Path, action: str
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    before = log_bytes(storage, project_id)

    response = client.post(f"/api/projects/{project_id}/{action}")

    assert response.status_code == 400
    assert response.json() == {"detail": f"nothing to {action}"}
    assert log_bytes(storage, project_id) == before
    assert projects._busy == set()


@pytest.mark.parametrize("action", ["undo", "redo"])
def test_undo_on_a_timeline_that_cant_be_read_is_a_500_with_a_fixed_message(
    client: TestClient, storage: Storage, long_clip: Path, action: str
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    storage.open_project(project_id).timeline_path.unlink()

    response = client.post(f"/api/projects/{project_id}/{action}")

    assert response.status_code == 500
    assert response.json()["detail"].startswith(f"couldn't {action}: ")
    assert str(storage.root) not in response.text
    assert projects._busy == set()


def test_undo_and_redo_work_from_the_chat_too(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    mute = ask(client, project_id, "mute the first 5 seconds")

    undo = ask(client, project_id, "undo")

    assert (undo["status"], undo["reply"]) == ("applied", "Undid the last edit.")
    assert undo["steps"][0]["result"]["undone_request_id"] == mute["request_id"]
    assert empty(get(client, project_id, "state"))
    assert storage.open_project(project_id).log().ops[-1].source == "agent"
    history = get(client, project_id, "history")
    assert [(r["request_id"], r["status"]) for r in history["requests"]] == [
        (mute["request_id"], "undone")
    ]

    redo = ask(client, project_id, "redo")
    again = ask(client, project_id, "redo")

    assert (redo["status"], redo["reply"]) == ("applied", "Brought back the last undone edit.")
    assert len(get(client, project_id, "state")["mutes"]) == 1
    assert again["status"] == "not_applied" and "nothing to redo" in again["reply"]


# --- GET /api/projects/{id}/tracks, POST .../analysis ---------------------------------------


def test_tracks_of_a_video_nobody_analysed(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    assert get(client, project_id, "tracks") == {"analysed": False, "tracks": []}

    drawn = face("usr_1", source="user")
    storage.open_project(project_id).save_user_tracks([drawn])

    # Drawn boxes show in the overlay, but they don't make the video analysed.
    tracks = get(client, project_id, "tracks")
    assert tracks["analysed"] is False
    assert [t["track_id"] for t in tracks["tracks"]] == ["usr_1"]
    assert ask(client, project_id, "blur faces")["status"] == "not_applied"


def test_tracks_are_the_cached_ones_then_the_drawn_ones(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    track_id = analysed(storage, project_id)
    storage.open_project(project_id).save_user_tracks([face("usr_1", source="user")])

    tracks = get(client, project_id, "tracks")

    assert tracks["analysed"] is True
    assert [(t["track_id"], t["source"]) for t in tracks["tracks"]] == [
        (track_id, "detector"),
        ("usr_1", "user"),
    ]
    assert tracks["tracks"][0]["keyframes"][0]["box"] == [0.25, 0.25, 0.75, 0.75]


def test_analysis_replays_what_is_cached(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    analysed(storage, project_id)

    response = client.post(f"/api/projects/{project_id}/analysis")

    assert response.status_code == 200
    assert response.json() == {"analysed": True, "tracks": 1}


@pytest.mark.parametrize("backend", ["stub", "local", "remote"])
def test_analysis_with_nothing_cached_is_503_not_an_empty_result(
    client: TestClient,
    storage: Storage,
    long_clip: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
) -> None:
    # No detector is connected to the API yet, whatever the backend: say so, never "0 tracks".
    monkeypatch.setenv("VIBE_BACKEND", backend)
    project_id = upload(client, long_clip)["project_id"]

    response = client.post(f"/api/projects/{project_id}/analysis")

    assert response.status_code == 503
    assert response.json() == {"detail": projects._NO_ANALYSIS}
    assert get(client, project_id, "tracks")["analysed"] is False
    assert not (storage.cache_dir / storage.open_project(project_id).info.video.sha256).exists()
    assert projects._busy == set()


def test_an_analysis_that_cant_be_trusted_is_503_everywhere_it_matters(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    sha = storage.open_project(project_id).info.video.sha256
    for key in ("k-aaaa1111", "k-bbbb2222"):  # two track sets and no default
        save_tracks(storage, sha, key, [face()])
    base = f"/api/projects/{project_id}"

    responses = [
        client.get(f"{base}/tracks"),
        client.post(f"{base}/analysis"),
        client.post(f"{base}/exports"),
    ]

    assert [r.status_code for r in responses] == [503] * 3
    assert {r.json()["detail"] for r in responses} == {projects._BAD_ANALYSIS}
    assert get(client, project_id, "")["analysed"] is False
    blur = ask(client, project_id, "blur faces")
    assert blur["status"] == "not_applied" and "analysis can't be used" in blur["reply"]
    assert ask(client, project_id, "mute the audio")["status"] == "applied"


# --- POST /api/projects/{id}/exports, GET .../exports/{name} --------------------------------


def export(client: TestClient, project_id: str) -> dict:
    response = client.post(f"/api/projects/{project_id}/exports")
    assert response.status_code == 200, response.text
    return response.json()


def test_export_with_a_mute_and_a_cut_is_a_playable_mp4_of_the_right_length(
    client: TestClient, storage: Storage, long_clip: Path, tmp_path: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    ask(client, project_id, "mute the first 5 seconds")
    ask(client, project_id, "cut 0:06 to 0:08")

    made = export(client, project_id)

    assert re.fullmatch(r"export_[0-9a-f]{12}\.mp4", made["name"])
    assert made["url"] == f"/api/projects/{project_id}/exports/{made['name']}"
    assert made["duration"] == pytest.approx(8.0, abs=0.05)
    assert made["issues"] == []
    assert [p.name for p in storage.open_project(project_id).exports_dir.iterdir()] == [
        made["name"]
    ]

    response = client.get(made["url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    downloaded = tmp_path / "downloaded.mp4"
    downloaded.write_bytes(response.content)
    info = probe(downloaded)
    assert info.duration == pytest.approx(8.0, abs=0.15)
    assert (info.codec, info.width, info.height) == ("h264", 32, 32)
    assert info.frame_count == (LONG_SECONDS - 2) * FPS
    assert info.audio is not None and info.audio.codec == "aac"
    samples, rate = extract_audio(downloaded)
    assert np.abs(samples[: 4 * rate]).max() < 0.01  # the muted start is silent
    assert np.abs(samples[6 * rate : 7 * rate]).max() > 0.1  # the tone is back after it


def test_each_export_is_a_new_file_and_supports_range(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    ask(client, project_id, "keep 0:01 to 0:02")

    first, second = export(client, project_id), export(client, project_id)

    assert first["name"] != second["name"]
    assert first["duration"] == second["duration"] == pytest.approx(1.0, abs=0.05)
    assert len(list(storage.open_project(project_id).exports_dir.iterdir())) == 2
    part = client.get(first["url"], headers={"Range": "bytes=0-9"})
    assert (part.status_code, len(part.content)) == (206, 10)


def test_export_masks_with_the_cached_tracks_and_reports_what_it_had_to_guess(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    track_id = analysed(storage, project_id)  # one keyframe at 0 s: lost for the rest
    ask(client, project_id, "blur faces")
    ask(client, project_id, "keep 0:00 to 0:02")

    made = export(client, project_id)

    assert made["issues"] == [
        f"{track_id}: lost, so its nearest known box stays masked (0:00.6–0:01.9)"
    ]


@pytest.mark.parametrize(
    ("requests", "reason"),
    [
        (["reduce the noise"], "audio filters (denoise)"),
        (["cut the first half", "cut the second half"], "nothing to render"),
    ],
)
def test_export_the_renderer_refuses_is_422_with_the_reason_and_no_file(
    client: TestClient, storage: Storage, long_clip: Path, requests: list[str], reason: str
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    for text in requests:
        assert ask(client, project_id, text)["status"] == "applied"

    response = client.post(f"/api/projects/{project_id}/exports")

    assert response.status_code == 422
    assert response.json()["detail"].startswith("can't export this timeline: ")
    assert reason in response.json()["detail"]
    assert list(storage.open_project(project_id).exports_dir.iterdir()) == []
    assert projects._busy == set()


def test_a_render_that_breaks_is_a_500_with_a_fixed_message(
    client: TestClient, long_clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = upload(client, long_clip)["project_id"]

    def fail(*args: object) -> None:
        raise projects.RenderError("encoding /srv/data/projects/x/exports/.tmp.mp4 failed")

    monkeypatch.setattr(projects, "export", fail)
    response = client.post(f"/api/projects/{project_id}/exports")

    assert response.status_code == 500
    assert response.json() == {"detail": "couldn't render this project"}


@pytest.mark.parametrize(
    "name",
    [
        # Percent-encoded, so the client sends them as they are instead of resolving the dots.
        "%2e%2e",
        "..%2Fproject.json",
        "%2e%2e%2ftimeline.jsonl",
        "..%5Ctimeline.jsonl",
        "project.json",
        "export_0123456789ab.mp4%2F..%2F..%2Fproject.json",
        "export_0123456789ab.mp4%00.json",
        "export_0123456789ab.mp4%0A",
        "EXPORT_0123456789AB.MP4",
        "export_0123456789ab.mov",
        "export_0123456789ab.mp4",  # a valid name, but no such file
    ],
)
def test_export_names_cant_reach_other_files(
    client: TestClient, storage: Storage, long_clip: Path, name: str
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    project = storage.open_project(project_id)
    ask(client, project_id, "mute the audio")
    export(client, project_id)
    (project.exports_dir / "notes.txt").write_text("private")  # not something we made

    response = client.get(f"/api/projects/{project_id}/exports/{name}")

    assert response.status_code == 404
    assert "schema_version" not in response.text and project_id not in response.text
    assert client.get(f"/api/projects/{project_id}/exports/notes.txt").status_code == 404


@pytest.mark.parametrize(
    "name",
    [
        "..",
        "../project.json",
        "..\\timeline.jsonl",
        "/etc/passwd",
        "notes.txt",
        "",
        "x/../y.mp4",
        "export_0123456789ab.mp4\n",
    ],
)
def test_the_export_handler_itself_refuses_names_it_didnt_make(
    client: TestClient, storage: Storage, long_clip: Path, name: str
) -> None:
    # Whatever a client or a proxy does to the URL, the handler checks the name it is given.
    project_id = upload(client, long_clip)["project_id"]
    (storage.open_project(project_id).exports_dir / "notes.txt").write_text("private")

    with pytest.raises(projects.HTTPException) as refused:
        projects.get_export(project_id, name, storage)

    assert (refused.value.status_code, refused.value.detail) == (404, "no such export")
