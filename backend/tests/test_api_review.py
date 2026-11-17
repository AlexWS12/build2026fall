"""Review queue routes (F-41): list the stretches to check, confirm or cut one, in stub mode."""

# ruff: noqa: F811  (the fixtures come from test_api_projects and are used by name)

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_api_projects import (  # noqa: F401
    ask,
    client,
    clip,
    get,
    long_clip,
    storage,
    upload,
)

from vibe.api import projects
from vibe.contract import AddMask, Effect, Track
from vibe.modules.vision import save_tracks
from vibe.storage import Storage
from vibe.video import FrameIndex


@pytest.fixture(autouse=True)
def fresh_frame_index_cache() -> None:
    projects._cached_frame_index.cache_clear()


def sparse_face(track_id: str = "trk_1") -> Track:
    """Seen at 0 s and 5 s only: a longer gap than a box can be trusted across."""
    box = (0.25, 0.25, 0.75, 0.75)
    keyframes = [{"t": t, "box": box, "conf": 0.9, "state": "detected"} for t in (0, 5)]
    return Track.model_validate(
        {"track_id": track_id, "label": "face", "source": "detector", "keyframes": keyframes}
    )


def lost_project(client: TestClient, storage: Storage, long_clip: Path) -> tuple[str, dict]:
    """A project with a blur on a face that nobody saw for most of the video, and its first item."""
    project_id = upload(client, long_clip)["project_id"]
    sha = storage.open_project(project_id).info.video.sha256
    save_tracks(storage, sha, "k-aaaa1111", [sparse_face()])
    assert ask(client, project_id, "blur faces")["status"] == "applied"
    items = get(client, project_id, "review")["items"]
    assert items[0]["kind"] == "lost"
    return project_id, items[0]


def act(client: TestClient, project_id: str, item_id: str, action: str) -> object:
    return client.post(f"/api/projects/{project_id}/review/{item_id}", json={"action": action})


def test_an_unanalysed_project_has_nothing_to_review(client: TestClient, long_clip: Path) -> None:
    project_id = upload(client, long_clip)["project_id"]

    assert get(client, project_id, "review") == {
        "analysed": False,
        "items": [],
        "held_seconds": 0,
        "rules_not_audited": [],
    }


def test_a_mask_over_a_long_gap_is_listed_as_lost(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id, item = lost_project(client, storage, long_clip)

    reply = get(client, project_id, "review")

    assert reply["analysed"] is True
    assert (item["track_id"].startswith("trk_"), item["label"]) == (True, "face")
    assert item["end"] > item["start"]
    assert item in reply["items"]


def test_confirming_removes_the_item_and_undo_brings_it_back(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id, item = lost_project(client, storage, long_clip)

    response = act(client, project_id, item["item_id"], "confirm")

    assert response.status_code == 200, response.text
    assert item["item_id"] not in [i["item_id"] for i in response.json()["items"]]
    assert response.json() == get(client, project_id, "review")
    last = get(client, project_id, "history")["requests"][-1]
    assert (last["source"], last["kinds"], last["status"]) == ("user", ["confirm_mask"], "applied")

    assert client.post(f"/api/projects/{project_id}/undo").status_code == 200
    assert item["item_id"] in [i["item_id"] for i in get(client, project_id, "review")["items"]]


def test_cutting_removes_the_item_and_leaves_the_cut_in_the_state(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id, item = lost_project(client, storage, long_clip)

    response = act(client, project_id, item["item_id"], "cut")

    assert response.status_code == 200, response.text
    assert item["item_id"] not in [i["item_id"] for i in response.json()["items"]]
    cuts = get(client, project_id, "state")["cuts"]
    assert [(c["start"], c["end"]) for c in cuts] == [(item["start"], item["end"])]
    last = get(client, project_id, "history")["requests"][-1]
    assert (last["source"], last["kinds"]) == ("user", ["cut"])


def test_an_item_that_is_gone_is_404_and_writes_nothing(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id, item = lost_project(client, storage, long_clip)
    assert act(client, project_id, item["item_id"], "confirm").status_code == 200
    before = storage.open_project(project_id).timeline_path.read_bytes()

    for item_id in (item["item_id"], "rv_nope"):  # confirmed already; never existed
        response = act(client, project_id, item_id, "confirm")
        assert response.status_code == 404
        assert response.json()["detail"] == "this spot is no longer in the review queue"
    assert storage.open_project(project_id).timeline_path.read_bytes() == before


def test_a_busy_project_gets_409_and_writes_nothing(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id, item = lost_project(client, storage, long_clip)
    before = storage.open_project(project_id).timeline_path.read_bytes()

    with projects._one_request_at_a_time(project_id):
        response = act(client, project_id, item["item_id"], "confirm")
        reading = client.get(f"/api/projects/{project_id}/review")

    assert response.status_code == 409
    assert reading.status_code == 200  # reading never waits
    assert storage.open_project(project_id).timeline_path.read_bytes() == before


@pytest.mark.parametrize("body", [{}, {"action": "delete"}, {"action": "confirm", "x": 1}])
def test_a_bad_action_is_422(
    client: TestClient, storage: Storage, long_clip: Path, body: dict
) -> None:
    project_id, item = lost_project(client, storage, long_clip)

    response = client.post(f"/api/projects/{project_id}/review/{item['item_id']}", json=body)

    assert response.status_code == 422


def test_a_masked_id_with_no_track_is_a_no_track_item(
    client: TestClient, storage: Storage, long_clip: Path
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    mask = AddMask(
        request_id="r_hand", source="user", targets=["trk_9"], effect=Effect(type="blur")
    )
    storage.open_project(project_id).append(mask)

    reply = get(client, project_id, "review")

    assert reply["analysed"] is False  # nothing cached, but the hand-made mask is still audited
    assert [(i["kind"], i["track_id"], i["label"]) for i in reply["items"]] == [
        ("no_track", "trk_9", None)
    ]


def test_the_frame_index_is_built_once_per_video(
    client: TestClient, long_clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = upload(client, long_clip)["project_id"]
    real_build = FrameIndex.build
    calls: list[str] = []

    def counting(path: str | Path) -> FrameIndex:
        calls.append(str(path))
        return real_build(path)

    monkeypatch.setattr(FrameIndex, "build", staticmethod(counting))

    get(client, project_id, "review")
    get(client, project_id, "review")

    assert len(calls) == 1
