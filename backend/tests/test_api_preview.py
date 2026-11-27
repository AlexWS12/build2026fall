"""Preview before apply (F-44): a cut or keep waits for a person to accept it.

Real call path: chat request -> planner -> executor (dry run) -> pending file -> accept or reject
-> timeline. Cuts and keeps only; every other edit still applies at once.
"""

# ruff: noqa: F811  (the fixtures come from test_api_projects and are used by name)

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_api_projects import (  # noqa: F401
    client,
    clip,
    empty,
    get,
    long_clip,
    storage,
    upload,
)

from vibe.api import projects
from vibe.storage import Storage


def raw_ask(client: TestClient, project_id: str, text: str):
    return client.post(f"/api/projects/{project_id}/requests", json={"text": text})


def preview_route(project_id: str, tail: str = "") -> str:
    return f"/api/projects/{project_id}/preview{tail}"


def log_bytes(storage: Storage, project_id: str) -> bytes:
    return storage.open_project(project_id).timeline_path.read_bytes()


@pytest.fixture
def project_id(client: TestClient, long_clip: Path) -> str:
    return upload(client, long_clip)["project_id"]


def test_a_cut_is_previewed_and_writes_nothing(
    client: TestClient, storage: Storage, project_id: str
) -> None:
    before = log_bytes(storage, project_id)

    reply = raw_ask(client, project_id, "cut 0:02 to 0:04")

    assert reply.status_code == 200
    body = reply.json()
    assert body["status"] == "preview"
    assert "Cut 0:02–0:04." in body["reply"]
    assert body["preview"]["duration_before"] == pytest.approx(10)
    assert body["preview"]["duration_after"] == pytest.approx(8)
    assert body["steps"][-1]["ok"] is True
    assert log_bytes(storage, project_id) == before
    assert empty(get(client, project_id, "state"))


def test_the_preview_route_shows_the_state_it_would_make(
    client: TestClient, project_id: str
) -> None:
    ask_body = raw_ask(client, project_id, "cut 0:02 to 0:04").json()

    shown = get(client, project_id, "preview")

    assert shown["request_id"] == ask_body["request_id"]
    assert [(c["start"], c["end"]) for c in shown["state"]["cuts"]] == [(2, 4)]
    assert shown["preview"]["removed"] == [{"start": 2, "end": 4}]
    assert empty(get(client, project_id, "state"))  # the real timeline is untouched


def test_accepting_applies_the_request_with_its_own_id(
    client: TestClient, storage: Storage, project_id: str
) -> None:
    proposed = raw_ask(client, project_id, "keep 0:01 to 0:08").json()

    accepted = client.post(preview_route(project_id, "/accept"))

    assert accepted.status_code == 200
    body = accepted.json()
    assert body["status"] == "applied"
    assert body["request_id"] == proposed["request_id"]
    assert body["reply"] == "Kept only 0:01–0:08; the rest is cut."
    ops = storage.open_project(project_id).log().ops
    assert [(op.kind, op.request_id, op.source) for op in ops] == [
        ("cut", proposed["request_id"], "agent"),
        ("cut", proposed["request_id"], "agent"),
    ]
    assert client.get(preview_route(project_id)).status_code == 404  # nothing is waiting now


def test_rejecting_discards_it_and_writes_nothing(
    client: TestClient, storage: Storage, project_id: str
) -> None:
    raw_ask(client, project_id, "cut 0:02 to 0:04")
    before = log_bytes(storage, project_id)

    rejected = client.post(preview_route(project_id, "/reject"))

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "not_applied"
    assert "Nothing was changed" in rejected.json()["reply"]
    assert log_bytes(storage, project_id) == before
    assert client.get(preview_route(project_id)).status_code == 404


def test_accept_or_reject_with_nothing_waiting_is_404(client: TestClient, project_id: str) -> None:
    assert client.post(preview_route(project_id, "/accept")).status_code == 404
    assert client.post(preview_route(project_id, "/reject")).status_code == 404
    assert client.get(preview_route(project_id)).status_code == 404


def test_a_new_request_waits_until_the_preview_is_settled(
    client: TestClient, storage: Storage, project_id: str
) -> None:
    raw_ask(client, project_id, "cut 0:02 to 0:04")

    blocked = raw_ask(client, project_id, "mute 0:05 to 0:06")

    assert blocked.status_code == 409
    assert "preview" in blocked.json()["detail"]
    assert empty(get(client, project_id, "state"))


def test_a_mute_still_applies_at_once(client: TestClient, project_id: str) -> None:
    body = raw_ask(client, project_id, "mute 0:01 to 0:02").json()

    assert body["status"] == "applied"
    assert client.get(preview_route(project_id)).status_code == 404


def test_a_refused_cut_is_not_applied_and_leaves_no_preview(
    client: TestClient, storage: Storage, project_id: str
) -> None:
    before = log_bytes(storage, project_id)

    body = raw_ask(client, project_id, "cut 0:20 to 0:30").json()

    assert body["status"] == "not_applied"
    assert "end of the video" in body["reply"]
    assert log_bytes(storage, project_id) == before
    assert client.get(preview_route(project_id)).status_code == 404


def test_accepting_checks_the_plan_again_and_a_refusal_writes_nothing(
    client: TestClient,
    storage: Storage,
    project_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The timeline can change while a preview waits, so accept runs the plan again for real
    and a refusal then leaves the log as it was."""
    from vibe.agent import execute_request as real

    raw_ask(client, project_id, "cut 0:02 to 0:04")
    before = log_bytes(storage, project_id)

    def refuse_when_committing(*args, commit=True, **kwargs):
        done = real(*args, commit=False, **kwargs)
        if commit:
            return done.model_copy(update={"ok": False, "ops": (), "error": "cut_range: changed"})
        return done

    monkeypatch.setattr(projects, "execute_request", refuse_when_committing)

    accepted = client.post(preview_route(project_id, "/accept"))

    assert accepted.status_code == 200
    assert accepted.json()["status"] == "not_applied"
    assert log_bytes(storage, project_id) == before
    assert client.get(preview_route(project_id)).status_code == 404  # settled, not stuck


def test_a_preview_the_timeline_no_longer_allows_says_why_and_can_still_be_discarded(
    client: TestClient,
    storage: Storage,
    project_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vibe.agent import execute_request as real

    raw_ask(client, project_id, "cut 0:02 to 0:04")

    def refuse(*args, **kwargs):
        done = real(*args, **kwargs)
        return done.model_copy(
            update={"ok": False, "ops": (), "failed_step": "s1", "error": "cut_range: changed"}
        )

    monkeypatch.setattr(projects, "execute_request", refuse)

    shown = client.get(preview_route(project_id))

    assert shown.status_code == 200
    assert "changed" in shown.json()["problem"]
    assert empty(shown.json()["state"])  # the real timeline: nothing would change
    assert shown.json()["preview"]["removed"] == []
    assert client.post(preview_route(project_id, "/reject")).status_code == 200
    assert client.get(preview_route(project_id)).status_code == 404


def test_a_good_preview_has_no_problem(client: TestClient, project_id: str) -> None:
    raw_ask(client, project_id, "cut 0:02 to 0:04")
    assert get(client, project_id, "preview")["problem"] is None


def test_a_corrupt_pending_file_is_not_applied_and_does_not_crash(
    client: TestClient, storage: Storage, project_id: str
) -> None:
    raw_ask(client, project_id, "cut 0:02 to 0:04")
    project = storage.open_project(project_id)
    project.pending_path.write_text("{not json")
    before = log_bytes(storage, project_id)

    assert client.get(preview_route(project_id)).status_code == 404
    assert client.post(preview_route(project_id, "/accept")).status_code == 404
    assert log_bytes(storage, project_id) == before


def test_a_preview_survives_a_server_restart(
    client: TestClient, storage: Storage, project_id: str
) -> None:
    raw_ask(client, project_id, "cut 0:02 to 0:04")

    reopened = TestClient(client.app)  # a new client: nothing remembered in memory
    assert reopened.get(preview_route(project_id)).status_code == 200
    assert reopened.post(preview_route(project_id, "/accept")).json()["status"] == "applied"


def test_the_chat_shows_one_entry_per_request_with_its_latest_status(
    client: TestClient, project_id: str
) -> None:
    proposed = raw_ask(client, project_id, "cut 0:02 to 0:04").json()
    client.post(preview_route(project_id, "/accept"))

    messages = get(client, project_id, "messages")

    assert [(m["request_id"], m["status"]) for m in messages] == [
        (proposed["request_id"], "applied")
    ]


def test_undo_takes_back_an_accepted_cut(client: TestClient, project_id: str) -> None:
    raw_ask(client, project_id, "cut 0:02 to 0:04")
    client.post(preview_route(project_id, "/accept"))
    assert not empty(get(client, project_id, "state"))

    assert client.post(f"/api/projects/{project_id}/undo").status_code == 200
    assert empty(get(client, project_id, "state"))


# --- Found in review: crash windows, bad stored plans, other writers, no-op cuts --------------


def test_if_clearing_the_pending_file_fails_after_accept_the_edit_is_not_applied_twice(
    client: TestClient,
    storage: Storage,
    project_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vibe.storage import Project

    proposed = raw_ask(client, project_id, "cut 0:02 to 0:04").json()

    def fail(self: Project) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(Project, "clear_pending", fail)
    first = client.post(preview_route(project_id, "/accept"))
    monkeypatch.undo()

    assert first.status_code == 200  # the edit is in: not a 500 about a cleanup
    assert first.json()["status"] == "applied"
    ops = storage.open_project(project_id).log().ops
    assert [(op.kind, op.request_id) for op in ops] == [("cut", proposed["request_id"])]
    # The leftover file is not a waiting preview: it was applied already.
    assert client.get(preview_route(project_id)).status_code == 404
    assert client.post(preview_route(project_id, "/accept")).status_code == 404
    assert len(storage.open_project(project_id).log().ops) == 1
    assert raw_ask(client, project_id, "mute 0:05 to 0:06").status_code == 200  # not blocked


def test_a_stored_plan_that_cant_run_is_shown_as_a_problem_and_can_be_discarded(
    client: TestClient, storage: Storage, project_id: str
) -> None:
    import json

    raw_ask(client, project_id, "cut 0:02 to 0:04")
    project = storage.open_project(project_id)
    stored = json.loads(project.pending_path.read_text())
    stored["plan"] = {"calls": [{"step_id": "s1", "tool": "no_such_tool", "args": {}}]}
    project.pending_path.write_text(json.dumps(stored))

    shown = client.get(preview_route(project_id))

    assert shown.status_code == 200  # the project must still open
    assert shown.json()["problem"]
    assert client.post(preview_route(project_id, "/reject")).status_code == 200
    assert client.get(preview_route(project_id)).status_code == 404


def test_a_stored_plan_of_the_wrong_shape_is_a_problem_not_a_crash(
    client: TestClient, storage: Storage, project_id: str
) -> None:
    import json

    raw_ask(client, project_id, "cut 0:02 to 0:04")
    project = storage.open_project(project_id)
    stored = json.loads(project.pending_path.read_text())
    stored["plan"] = {"calls": "nonsense"}
    project.pending_path.write_text(json.dumps(stored))
    before = log_bytes(storage, project_id)

    assert client.get(preview_route(project_id)).json()["problem"]
    accepted = client.post(preview_route(project_id, "/accept"))

    assert accepted.status_code == 200
    assert accepted.json()["status"] == "not_applied"
    assert log_bytes(storage, project_id) == before
    assert client.get(preview_route(project_id)).status_code == 404  # settled, not stuck


@pytest.mark.parametrize("tail", ["/undo", "/redo"])
def test_undo_and_redo_wait_while_a_preview_waits(
    client: TestClient, storage: Storage, project_id: str, tail: str
) -> None:
    raw_ask(client, project_id, "mute 0:01 to 0:02")  # something to undo
    raw_ask(client, project_id, "cut 0:02 to 0:04")
    before = log_bytes(storage, project_id)

    blocked = client.post(f"/api/projects/{project_id}{tail}")

    assert blocked.status_code == 409
    assert "preview" in blocked.json()["detail"]
    assert log_bytes(storage, project_id) == before


def test_a_review_action_waits_while_a_preview_waits(
    client: TestClient, storage: Storage, project_id: str
) -> None:
    raw_ask(client, project_id, "cut 0:02 to 0:04")
    before = log_bytes(storage, project_id)

    blocked = client.post(f"/api/projects/{project_id}/review/rv_any", json={"action": "cut"})

    assert blocked.status_code == 409
    assert log_bytes(storage, project_id) == before


def test_a_cut_that_is_already_in_place_is_not_proposed(
    client: TestClient, storage: Storage, project_id: str
) -> None:
    raw_ask(client, project_id, "cut 0:02 to 0:04")
    client.post(preview_route(project_id, "/accept"))
    before = log_bytes(storage, project_id)

    body = raw_ask(client, project_id, "cut 0:02 to 0:04").json()

    assert body["status"] == "not_applied"
    assert "already" in body["reply"]
    assert client.get(preview_route(project_id)).status_code == 404
    assert log_bytes(storage, project_id) == before


def test_a_cut_next_to_an_existing_one_reports_only_the_new_footage_as_removed(
    client: TestClient, project_id: str
) -> None:
    raw_ask(client, project_id, "cut 0:02 to 0:04")
    client.post(preview_route(project_id, "/accept"))

    body = raw_ask(client, project_id, "cut 0:03 to 0:06").json()

    assert body["status"] == "preview"
    assert body["preview"]["removed"] == [{"start": 4, "end": 6}]
    assert body["preview"]["duration_before"] == pytest.approx(8)
    assert body["preview"]["duration_after"] == pytest.approx(6)


def test_a_proposal_that_cant_be_saved_is_an_error_with_nothing_pending_or_written(
    client: TestClient,
    storage: Storage,
    project_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vibe.storage import Project

    def fail(self: Project, pending: object) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(Project, "set_pending", fail)
    before = log_bytes(storage, project_id)

    response = raw_ask(client, project_id, "cut 0:02 to 0:04")

    assert response.status_code == 500
    assert "Nothing was changed" in response.json()["detail"]
    assert log_bytes(storage, project_id) == before
    monkeypatch.undo()
    assert client.get(preview_route(project_id)).status_code == 404
