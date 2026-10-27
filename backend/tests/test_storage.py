"""R-2: project storage. Uses a temporary data dir and small fake "video" files (bytes only)."""

from pathlib import Path

import pytest

from vibe import config
from vibe.contract import AddMask, ContractError, Effect, Keyframe, Track, Undo
from vibe.storage import Storage, StorageError


@pytest.fixture
def store(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "data")


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    path = tmp_path / "Street Walk.MP4"
    path.write_bytes(b"not really a video, but storage only cares about bytes")
    return path


def blur(rid: str, target: str = "trk_1") -> AddMask:
    return AddMask(request_id=rid, source="agent", targets=[target], effect=Effect(type="blur"))


def test_data_dir_defaults_to_repo_and_follows_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("VIBE_DATA_DIR", raising=False)
    assert config.data_dir().name == "data"
    assert (config.data_dir().parent / "backend").is_dir()  # the repo root
    monkeypatch.setenv("VIBE_DATA_DIR", str(tmp_path))
    assert config.data_dir() == tmp_path


# --- Videos -------------------------------------------------------------------------------


def test_videos_are_stored_once_by_content(store: Storage, clip: Path, tmp_path: Path) -> None:
    first = store.add_video(clip)
    copy = tmp_path / "copy.mp4"
    copy.write_bytes(clip.read_bytes())
    assert store.add_video(copy).sha256 == first.sha256
    assert len(list(store.videos_dir.iterdir())) == 1
    assert store.video_path(first).read_bytes() == clip.read_bytes()
    assert (first.ext, first.original_name) == (".mp4", "Street Walk.MP4")


def test_missing_video_is_an_error(store: Storage, tmp_path: Path) -> None:
    with pytest.raises(StorageError, match="no such video"):
        store.add_video(tmp_path / "nope.mp4")


# --- Projects -----------------------------------------------------------------------------


def test_create_and_reopen_a_project(store: Storage, clip: Path) -> None:
    project = store.create_project(clip)
    assert project.info.name == "Street Walk"
    assert project.timeline_path.read_text(encoding="utf-8") == '{"schema_version": 1}\n'
    assert project.exports_dir.is_dir()

    again = store.open_project(project.id)
    assert again.info == project.info
    assert len(again.log()) == 0
    assert [p.project_id for p in store.list_projects()] == [project.id]


def test_two_projects_share_one_video(store: Storage, clip: Path) -> None:
    a, b = store.create_project(clip, "a"), store.create_project(clip, "b")
    assert a.id != b.id and a.info.video == b.info.video
    assert len(list(store.videos_dir.iterdir())) == 1


@pytest.mark.parametrize("bad", ["../etc", "a/b", "", "..", "prj_1/../../x"])
def test_project_ids_cannot_escape_the_folder(store: Storage, bad: str) -> None:
    with pytest.raises(StorageError, match="invalid project id"):
        store.open_project(bad)


def test_unknown_project_is_an_error(store: Storage) -> None:
    with pytest.raises(StorageError, match="no project"):
        store.open_project("prj_missing")


def test_unreadable_projects_are_skipped_when_listing(store: Storage, clip: Path) -> None:
    good = store.create_project(clip)
    broken = store.projects_dir / "prj_broken"
    broken.mkdir()
    (broken / "project.json").write_text("{not json", encoding="utf-8")
    assert [p.project_id for p in store.list_projects()] == [good.id]


# --- The op log on disk -------------------------------------------------------------------


def test_appended_ops_survive_reopening(store: Storage, clip: Path) -> None:
    project = store.create_project(clip)
    project.append(blur("r_1"))
    project.append(Undo(request_id="r_2", source="user", undo_request_id="r_1"))
    reopened = store.open_project(project.id)
    assert len(reopened.log()) == 2
    assert reopened.state().masks == {}


def test_a_rejected_op_writes_nothing(store: Storage, clip: Path) -> None:
    project = store.create_project(clip)
    project.append(blur("r_1"))
    before = project.timeline_path.read_bytes()
    with pytest.raises(ContractError):
        project.append(Undo(request_id="r_2", source="user", undo_request_id="r_9"))
    assert project.timeline_path.read_bytes() == before


def test_an_incomplete_last_line_is_dropped(store: Storage, clip: Path) -> None:
    project = store.create_project(clip)
    project.append(blur("r_1"))
    with project.timeline_path.open("a", encoding="utf-8") as f:
        f.write('{"kind": "add_mask", "op_id": "op_cut_off')  # crash mid-append
    assert len(project.log()) == 1
    assert set(project.state().masks) == {"trk_1"}


# --- User tracks --------------------------------------------------------------------------


def test_user_tracks_round_trip(store: Storage, clip: Path) -> None:
    project = store.create_project(clip)
    assert project.user_tracks() == []
    track = Track(
        track_id="trk_user_1",
        label="face",
        source="user",
        keyframes=[Keyframe(t=1.0, box=(0.1, 0.1, 0.2, 0.2), conf=1.0, state="user")],
    )
    project.save_user_tracks([track])
    assert store.open_project(project.id).user_tracks() == [track]


# --- Analysis cache -----------------------------------------------------------------------


def test_cache_path_layout(store: Storage, clip: Path) -> None:
    ref = store.add_video(clip)
    path = store.cache_path(ref.sha256, "tracks", "yoloe-11s@1-3f9a1c")
    assert path == store.cache_dir / ref.sha256 / "tracks" / "yoloe-11s@1-3f9a1c.json"


@pytest.mark.parametrize(
    ("sha", "kind", "key"),
    [
        ("not-a-hash", "tracks", "k"),
        ("0" * 64, "../x", "k"),
        ("0" * 64, "tracks", "a/b"),
        ("0" * 64, "tracks", ".."),
    ],
)
def test_cache_path_rejects_unsafe_parts(store: Storage, sha: str, kind: str, key: str) -> None:
    with pytest.raises((StorageError, ValueError)):
        store.cache_path(sha, kind, key)
