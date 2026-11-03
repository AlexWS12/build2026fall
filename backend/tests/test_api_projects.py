"""Project routes (F-64) in stub mode: upload, chat request, timeline state, one request at a time.

The test clip is encoded with PyAV, so these tests don't need the ffmpeg CLI.
"""

from collections.abc import Iterator
from pathlib import Path

import av
import numpy as np
import pytest
from fastapi.testclient import TestClient

from vibe.api import projects
from vibe.api.app import app
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


def upload(client: TestClient, clip: Path) -> dict:
    with clip.open("rb") as f:
        response = client.post("/api/projects", files={"file": (clip.name, f, "video/mp4")})
    assert response.status_code == 201, response.text
    return response.json()


# --- POST /api/projects ---------------------------------------------------------------------


def test_upload_creates_a_project_on_disk(client: TestClient, storage: Storage, clip: Path) -> None:
    info = upload(client, clip)
    assert info["name"] == "street walk"
    assert info["video"]["original_name"] == "street walk.mp4"
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


def test_planned_request_replies_and_writes_the_stub_op(client: TestClient, clip: Path) -> None:
    project_id = upload(client, clip)["project_id"]
    response = client.post(f"/api/projects/{project_id}/requests", json={"text": "blur faces"})
    assert response.status_code == 200
    reply = response.json()
    assert reply["reply"].strip()

    state = client.get(f"/api/projects/{project_id}/state").json()
    assert [m["request_id"] for m in state["mutes"]] == [reply["request_id"]]


def test_clarify_replies_without_writing_ops(client: TestClient, clip: Path) -> None:
    project_id = upload(client, clip)["project_id"]
    response = client.post(f"/api/projects/{project_id}/requests", json={"text": "cut it"})
    assert response.status_code == 200
    assert response.json()["reply"].strip()

    state = client.get(f"/api/projects/{project_id}/state").json()
    assert state["mutes"] == [] and state["cuts"] == [] and state["masks"] == {}


def test_each_request_gets_its_own_id(client: TestClient, storage: Storage, clip: Path) -> None:
    project_id = upload(client, clip)["project_id"]
    url = f"/api/projects/{project_id}/requests"
    first = client.post(url, json={"text": "mute 0:10 to 0:20"}).json()
    second = client.post(url, json={"text": "blur faces"}).json()
    assert first["request_id"] != second["request_id"]
    ops = storage.open_project(project_id).log()
    assert [op.request_id for op in ops] == [first["request_id"], second["request_id"]]


@pytest.mark.parametrize("body", [{"text": ""}, {"text": "   "}, {}, {"text": "hi", "x": 1}])
def test_bad_request_body_is_422(client: TestClient, clip: Path, body: dict) -> None:
    project_id = upload(client, clip)["project_id"]
    response = client.post(f"/api/projects/{project_id}/requests", json=body)
    assert response.status_code == 422


def test_busy_project_gets_409_and_writes_nothing(client: TestClient, clip: Path) -> None:
    project_id = upload(client, clip)["project_id"]
    lock = projects._project_lock(project_id)
    with lock:  # another request is in flight
        response = client.post(f"/api/projects/{project_id}/requests", json={"text": "blur faces"})
    assert response.status_code == 409
    assert client.get(f"/api/projects/{project_id}/state").json()["mutes"] == []
    # Free again once that request is done.
    response = client.post(f"/api/projects/{project_id}/requests", json={"text": "blur faces"})
    assert response.status_code == 200
