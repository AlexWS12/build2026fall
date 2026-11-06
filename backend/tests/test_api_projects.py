"""Project routes (F-64) in stub mode: upload, chat request, timeline state, one request at a time.

The test clip is encoded with PyAV, so these tests don't need the ffmpeg CLI.
"""

import tempfile
from collections.abc import Iterator
from pathlib import Path

import av
import numpy as np
import pytest
from fastapi.testclient import TestClient

from vibe.api import projects
from vibe.api.app import app
from vibe.modules.transcribe import Segment, Transcript, Word, transcript_key
from vibe.storage import Storage


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


def test_errors_dont_reveal_server_paths(client: TestClient, storage: Storage) -> None:
    responses = [
        client.post("/api/projects", files={"file": ("notes.mp4", b"not a video")}),
        client.get("/api/projects/prj_missing/state"),
        client.get("/api/projects/bad id!/state"),
        client.post("/api/projects/prj_missing/requests", json={"text": "blur faces"}),
    ]
    for response in responses:
        assert response.status_code >= 400
        for secret in (str(storage.root), tempfile.gettempdir(), "vibe-upload-"):
            assert secret not in response.text, response.text


# --- GET /api/projects/{id}/state -----------------------------------------------------------


def test_new_project_has_an_empty_timeline(client: TestClient, clip: Path) -> None:
    project_id = upload(client, clip)["project_id"]
    response = client.get(f"/api/projects/{project_id}/state")
    assert response.status_code == 200
    state = response.json()
    assert state["schema_version"] == 1
    assert state["masks"] == {} and state["cuts"] == [] and state["mutes"] == []


@pytest.mark.parametrize("project_id", ["prj_missing", "bad id!"])
def test_unknown_project_is_404(client: TestClient, project_id: str) -> None:
    assert client.get(f"/api/projects/{project_id}/state").status_code == 404
    response = client.post(f"/api/projects/{project_id}/requests", json={"text": "blur faces"})
    assert response.status_code == 404


# --- POST /api/projects/{id}/requests -------------------------------------------------------


@pytest.mark.parametrize("text", ["blur faces", "mute 0:10 to 0:20", "cut the first 5 seconds"])
def test_planned_request_is_not_applied_and_writes_nothing(
    client: TestClient, storage: Storage, clip: Path, text: str
) -> None:
    # Fail closed until the plan executor exists: "blur faces" must not look done.
    project_id = upload(client, clip)["project_id"]
    response = client.post(f"/api/projects/{project_id}/requests", json={"text": text})
    assert response.status_code == 200
    reply = response.json()
    assert reply["status"] == "not_applied"
    assert reply["reply"].strip()

    assert len(storage.open_project(project_id).log()) == 0
    assert empty(client.get(f"/api/projects/{project_id}/state").json())


def test_clarify_replies_without_writing_ops(client: TestClient, clip: Path) -> None:
    project_id = upload(client, clip)["project_id"]
    response = client.post(f"/api/projects/{project_id}/requests", json={"text": "cut it"})
    assert response.status_code == 200
    assert response.json()["status"] == "clarify"
    assert response.json()["reply"].strip()

    assert empty(client.get(f"/api/projects/{project_id}/state").json())


def test_each_request_gets_its_own_id(client: TestClient, clip: Path) -> None:
    project_id = upload(client, clip)["project_id"]
    url = f"/api/projects/{project_id}/requests"
    first = client.post(url, json={"text": "mute 0:10 to 0:20"}).json()
    second = client.post(url, json={"text": "blur faces"}).json()
    assert first["request_id"].startswith("r_")
    assert first["request_id"] != second["request_id"]


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
    assert projects._busy == set()  # the project is free again


@pytest.mark.parametrize("body", [{"text": ""}, {"text": "   "}, {}, {"text": "hi", "x": 1}])
def test_bad_request_body_is_422(client: TestClient, clip: Path, body: dict) -> None:
    project_id = upload(client, clip)["project_id"]
    response = client.post(f"/api/projects/{project_id}/requests", json=body)
    assert response.status_code == 422


def test_busy_project_gets_409_and_writes_nothing(client: TestClient, clip: Path) -> None:
    project_id = upload(client, clip)["project_id"]
    with projects._one_request_at_a_time(project_id):  # another request is in flight
        response = client.post(f"/api/projects/{project_id}/requests", json={"text": "blur faces"})
    assert response.status_code == 409
    assert empty(client.get(f"/api/projects/{project_id}/state").json())
    # Free again once that request is done, and nothing is left behind per project.
    response = client.post(f"/api/projects/{project_id}/requests", json={"text": "blur faces"})
    assert response.status_code == 200
    assert projects._busy == set()


# --- POST /api/projects/{id}/transcript (F-31) ----------------------------------------------

HOLA = Transcript(
    model="test",
    language="es",
    segments=[
        Segment(
            start=0.2,
            end=0.6,
            text="Hola",
            words=[Word(start=0.2, end=0.6, text="Hola", probability=0.9)],
        )
    ],
)


def cache_transcript(storage: Storage, project_id: str, transcript: Transcript = HOLA) -> None:
    """Put a transcript in the analysis cache, as if the project's video had been transcribed."""
    video = storage.open_project(project_id).info.video
    path = storage.cache_path(video.sha256, "transcript", transcript_key())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(transcript.model_dump_json(), encoding="utf-8")


def test_transcript_comes_from_the_cache(client: TestClient, storage: Storage, clip: Path) -> None:
    project_id = upload(client, clip)["project_id"]
    cache_transcript(storage, project_id)

    response = client.post(f"/api/projects/{project_id}/transcript")

    assert response.status_code == 200
    assert Transcript.model_validate(response.json()) == HOLA


def test_stub_without_a_cached_transcript_is_503(
    client: TestClient, clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_BACKEND", "stub")
    project_id = upload(client, clip)["project_id"]

    response = client.post(f"/api/projects/{project_id}/transcript")

    assert response.status_code == 503
    assert projects._busy == set()


def test_a_video_without_audio_is_400(
    client: TestClient, clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_BACKEND", "local")  # the test clip has no audio track
    project_id = upload(client, clip)["project_id"]

    response = client.post(f"/api/projects/{project_id}/transcript")

    assert response.status_code == 400
    assert response.json()["detail"] == "this video has no audio"


def test_transcript_of_an_unknown_project_is_404(client: TestClient) -> None:
    assert client.post("/api/projects/prj_nope/transcript").status_code == 404
