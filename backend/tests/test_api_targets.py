"""Click or draw a box to target (F-14): ``POST /api/projects/{id}/targets``, in stub mode."""

# ruff: noqa: F811  (the fixtures come from test_api_projects and are used by name)

from pathlib import Path

import av
import numpy as np
import pytest
from fastapi.testclient import TestClient
from test_api_projects import (  # noqa: F401
    analysed,
    client,
    get,
    log_bytes,
    long_clip,
    storage,
    upload,
)

from vibe.api import projects, targets
from vibe.storage import Storage

W, H, FPS, SECONDS = 160, 96, 20, 3


def _target_frames(visible) -> list[np.ndarray]:
    """A textured 28 × 24 target moving right 1 px a frame over a textured background, on
    the frames where ``visible(i)`` is true."""
    rng = np.random.default_rng(5)
    background = np.repeat(np.repeat(rng.integers(40, 120, (H // 2, W // 2)), 2, 0), 2, 1)
    target = np.repeat(np.repeat(rng.integers(0, 256, (12, 14)), 2, 0), 2, 1)
    images = []
    for i in range(FPS * SECONDS):
        grey = background.astype(np.uint8)
        if visible(i):
            grey[30:54, 40 + i : 68 + i] = target
        images.append(np.stack([grey, grey, grey], axis=-1))
    return images


def _write(path: Path, images: list[np.ndarray]) -> Path:
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=FPS)
        stream.width, stream.height, stream.pix_fmt = W, H, "yuv420p"
        stream.bit_rate = 4_000_000
        for image in images:
            for packet in stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


@pytest.fixture(scope="module")
def target_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Three seconds with the target on screen the whole time."""
    path = tmp_path_factory.mktemp("clips") / "target.mp4"
    return _write(path, _target_frames(lambda i: True))


@pytest.fixture(scope="module")
def leaving_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The same target, gone after frame 39 (2 s)."""
    path = tmp_path_factory.mktemp("clips") / "leaving.mp4"
    return _write(path, _target_frames(lambda i: i < 40))


@pytest.fixture(autouse=True)
def fresh_frame_index_cache() -> None:
    projects._cached_frame_index.cache_clear()


def target(client: TestClient, project_id: str, **body: object):
    return client.post(f"/api/projects/{project_id}/targets", json=body)


def at(i: int) -> list[float]:
    """The target's box on frame ``i`` of the target clips."""
    return [(40 + i) / W, 30 / H, (68 + i) / W, 54 / H]


# --- Click a followed target ------------------------------------------------------------------


def test_a_click_on_a_detected_face_masks_it_while_tracked(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    face_id = analysed(storage, project_id)  # one face at (0.25..0.75), seen at 0 s

    response = target(client, project_id, t=0.2, point=[0.5, 0.5], effect="pixelate")

    assert response.status_code == 200, response.text
    reply = response.json()
    assert (reply["how"], reply["track_id"], reply["label"]) == ("picked", face_id, "face")
    assert reply["followed"] is None and reply["lost_after"] is None
    assert reply["reply"] == "Pixelated the face you clicked while it's on screen."
    [span] = get(client, project_id, "state")["masks"][face_id]
    assert span["effect"]["type"] == "pixelate"
    assert span["request_id"] == reply["request_id"]
    assert span["end"] is not None  # while tracked: not the whole 10 s clip
    last = get(client, project_id, "history")["requests"][-1]
    assert (last["source"], last["kinds"], last["status"]) == ("user", ["add_mask"], "applied")
    assert storage.open_project(project_id).user_tracks() == []  # nothing new was drawn


def test_undo_takes_a_click_back(client: TestClient, storage: Storage, long_clip: Path) -> None:
    project_id = upload(client, long_clip)["project_id"]
    analysed(storage, project_id)
    assert target(client, project_id, t=0, point=[0.5, 0.5]).status_code == 200

    assert client.post(f"/api/projects/{project_id}/undo").status_code == 200

    assert get(client, project_id, "state")["masks"] == {}


def test_a_click_on_nothing_is_400_and_writes_nothing(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    analysed(storage, project_id)
    before = log_bytes(storage, project_id)

    for point, t in (([0.05, 0.05], 0.0), ([0.5, 0.5], 9.0)):  # beside it; long after it's seen
        response = target(client, project_id, t=t, point=point)
        assert response.status_code == 400
        assert "drag a box" in response.json()["detail"]

    assert log_bytes(storage, project_id) == before


def test_a_click_on_an_unanalysed_video_asks_for_a_box(client: TestClient, long_clip: Path) -> None:
    project_id = upload(client, long_clip)["project_id"]
    response = target(client, project_id, t=0, point=[0.5, 0.5])
    assert response.status_code == 400
    assert "drag a box" in response.json()["detail"]


def test_a_click_picks_the_smallest_target_under_it(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    sha = storage.open_project(project_id).info.video.sha256
    from test_api_projects import face

    from vibe.contract import Track
    from vibe.modules.vision import save_tracks

    person = Track.model_validate(
        {
            "track_id": "trk_9",
            "label": "person",
            "source": "detector",
            "keyframes": [{"t": 0, "box": (0.1, 0.1, 0.9, 0.9), "conf": 0.9, "state": "detected"}],
        }
    )
    saved = save_tracks(storage, sha, "k-aaaa1111", [person, face()])
    by_label = {t.label: t.track_id for t in saved}

    reply = target(client, project_id, t=0, point=[0.5, 0.5]).json()
    assert reply["track_id"] == by_label["face"]
    reply = target(client, project_id, t=0, point=[0.15, 0.15]).json()
    assert reply["track_id"] == by_label["person"]


# --- Draw a box -------------------------------------------------------------------------------


def test_a_box_over_a_detected_face_masks_that_face(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    face_id = analysed(storage, project_id)

    response = target(client, project_id, t=0, box=[0.27, 0.22, 0.76, 0.74])

    assert response.status_code == 200, response.text
    reply = response.json()
    assert (reply["how"], reply["track_id"]) == ("matched", face_id)
    assert reply["reply"] == "Blurred the face in your box while it's on screen."
    assert storage.open_project(project_id).user_tracks() == []


def test_a_drawn_box_is_followed_saved_and_masked_over_the_whole_clip(
    client: TestClient, storage: Storage, target_clip: Path
) -> None:
    project_id = upload(client, target_clip)["project_id"]

    response = target(client, project_id, t=1.0, box=at(20), effect="box", label="person")

    assert response.status_code == 200, response.text
    reply = response.json()
    assert reply["how"] == "followed"
    assert reply["track_id"].startswith("usr_")
    assert reply["label"] == "person"
    assert (reply["lost_before"], reply["lost_after"]) == (None, None)
    assert reply["followed"]["start"] == pytest.approx(0.0)
    assert reply["followed"]["end"] == pytest.approx((FPS * SECONDS - 2) / FPS)
    assert reply["reply"] == (
        "Covered your box with a black box and followed it from 0:00 to 0:02.9."
    )

    [track] = storage.open_project(project_id).user_tracks()
    assert (track.track_id, track.source, track.label) == (reply["track_id"], "user", "person")
    assert [k.state for k in track.keyframes].count("user") == 1
    for k in track.keyframes:  # it went with the target, both ways from the drawn frame
        assert k.box[0] * W == pytest.approx(40 + round(k.t * FPS), abs=2.0)

    tracks = get(client, project_id, "tracks")
    assert tracks["analysed"] is False
    assert [t["track_id"] for t in tracks["tracks"]] == [reply["track_id"]]
    [span] = get(client, project_id, "state")["masks"][reply["track_id"]]
    assert (span["start"], span["end"], span["effect"]["type"]) == (0, None, "box")
    assert get(client, project_id, "review")["items"] == []  # followed all the way: clean


def test_drawing_on_a_box_already_drawn_reuses_its_track(
    client: TestClient, storage: Storage, target_clip: Path
) -> None:
    project_id = upload(client, target_clip)["project_id"]
    first = target(client, project_id, t=1.0, box=at(20)).json()

    second = target(client, project_id, t=2.0, box=at(40), effect="pixelate").json()

    assert (second["how"], second["track_id"]) == ("matched", first["track_id"])
    assert len(storage.open_project(project_id).user_tracks()) == 1
    clicked = target(client, project_id, t=0.5, point=[(40 + 10 + 14) / W, 42 / H]).json()
    assert (clicked["how"], clicked["track_id"]) == ("picked", first["track_id"])


def test_a_target_that_leaves_is_masked_on_and_sent_to_review(
    client: TestClient, storage: Storage, leaving_clip: Path
) -> None:
    project_id = upload(client, leaving_clip)["project_id"]

    reply = target(client, project_id, t=1.0, box=at(20)).json()

    assert reply["lost_before"] is None
    assert reply["lost_after"] == pytest.approx(1.9, abs=0.11)
    assert "I lost it after 0:01.9" in reply["reply"]
    assert "review queue" in reply["reply"]
    [span] = get(client, project_id, "state")["masks"][reply["track_id"]]
    assert span["end"] is None  # fail closed: still masked after it was lost
    kinds = {i["kind"] for i in get(client, project_id, "review")["items"]}
    assert "lost" in kinds


def test_undo_takes_the_mask_back_and_keeps_the_drawn_track(
    client: TestClient, storage: Storage, target_clip: Path
) -> None:
    project_id = upload(client, target_clip)["project_id"]
    reply = target(client, project_id, t=1.0, box=at(20)).json()

    assert client.post(f"/api/projects/{project_id}/undo").status_code == 200

    assert get(client, project_id, "state")["masks"] == {}
    assert [t.track_id for t in storage.open_project(project_id).user_tracks()] == [
        reply["track_id"]
    ]


def test_a_failed_mask_takes_the_drawn_track_back(
    client: TestClient, storage: Storage, target_clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = upload(client, target_clip)["project_id"]
    before = log_bytes(storage, project_id)

    def broken(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(targets, "execute_plan", broken)
    response = target(client, project_id, t=1.0, box=at(20))

    assert response.status_code == 500
    assert "disk" not in response.json()["detail"]
    assert storage.open_project(project_id).user_tracks() == []
    assert log_bytes(storage, project_id) == before


# --- Bad requests -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"t": 0},  # neither a point nor a box
        {"t": 0, "point": [0.5, 0.5], "box": [0.1, 0.1, 0.4, 0.4]},  # both
        {"t": 0, "point": [1.5, 0.5]},
        {"t": 0, "box": [0.5, 0.1, 0.4, 0.4]},  # x1 > x2
        {"t": 0, "box": [0.1, 0.1, 0.102, 0.4]},  # a slip, not a box
        {"t": -1, "point": [0.5, 0.5]},
        {"t": 0, "point": [0.5, 0.5], "effect": "sparkle"},
        {"t": 0, "box": [0.1, 0.1, 0.4, 0.4], "label": "../etc"},
    ],
)
def test_bad_bodies_are_422(client: TestClient, target_clip: Path, body: dict) -> None:
    project_id = upload(client, target_clip)["project_id"]
    assert target(client, project_id, **body).status_code == 422


def test_a_time_past_the_end_is_400(client: TestClient, target_clip: Path) -> None:
    project_id = upload(client, target_clip)["project_id"]
    response = target(client, project_id, t=60, box=at(0))
    assert response.status_code == 400
    assert "past the end" in response.json()["detail"]


def test_an_unknown_project_is_404(client: TestClient) -> None:
    assert target(client, "nope", t=0, point=[0.5, 0.5]).status_code == 404


def test_a_busy_project_is_409(client: TestClient, target_clip: Path) -> None:
    project_id = upload(client, target_clip)["project_id"]
    with projects._one_request_at_a_time(project_id):
        assert target(client, project_id, t=0, box=at(0)).status_code == 409
