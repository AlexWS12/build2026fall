"""F-09 clip manifest: consented clips, stored once by hash, checked on every machine."""

import json
from pathlib import Path

import pytest
from bench_helpers import register
from pydantic import ValidationError

from vibe.benchmark.manifest import (
    MANIFEST_PATH,
    BenchmarkError,
    ClipEntry,
    ClipManifest,
    add_clip,
    clip_status,
    load_manifest,
)
from vibe.storage import Storage, VideoRef
from vibe.video import VideoError

# --- The committed manifest ---------------------------------------------------------------


def test_committed_manifest_is_valid() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    assert all(c.source and c.consent for c in manifest.clips)


def test_committed_manifest_is_written_by_the_code() -> None:
    """``add-clip`` writes it, so hand edits that drift from that format show up here."""
    manifest = load_manifest(MANIFEST_PATH)
    assert MANIFEST_PATH.read_text(encoding="utf-8") == manifest.model_dump_json(indent=2) + "\n"


# --- Registering clips --------------------------------------------------------------------


def test_add_clip_records_metadata_and_stores_the_video(
    clip: Path, manifest_path: Path, storage: Storage
) -> None:
    entry, added = add_clip(
        clip,
        manifest_path=manifest_path,
        storage=storage,
        clip_id="street-walk",
        source="Jean, Brickell",
        consent="agreed on camera",
        tags=["speech", "faces", "faces"],
    )
    assert added
    assert (entry.width, entry.height, entry.fps, entry.has_audio) == (160, 120, "10", True)
    assert entry.duration == pytest.approx(1.0, abs=0.1)
    assert (entry.video.ext, entry.video.original_name) == (".mp4", "Street Walk.MP4")
    assert entry.consent_cloud is False  # footage stays on team machines unless consented
    assert entry.tags == ["faces", "speech"]
    assert storage.video_path(entry.video).read_bytes() == clip.read_bytes()
    assert load_manifest(manifest_path).clips == [entry]


def test_cloud_consent_is_recorded_when_given(
    clip: Path, manifest_path: Path, storage: Storage
) -> None:
    assert register(clip, manifest_path, storage, consent_cloud=True).consent_cloud


def test_a_teammate_adding_the_same_file_only_stores_it(
    clip: Path, manifest_path: Path, tmp_path: Path
) -> None:
    register(clip, manifest_path, Storage(tmp_path / "jean"))
    teammate = Storage(tmp_path / "pablo")
    entry, added = add_clip(clip, manifest_path=manifest_path, storage=teammate)
    assert not added and entry.clip_id == "street-walk"
    assert teammate.video_path(entry.video).is_file()
    assert len(load_manifest(manifest_path).clips) == 1


def test_a_new_clip_needs_id_source_and_consent(
    clip: Path, manifest_path: Path, storage: Storage
) -> None:
    with pytest.raises(BenchmarkError, match="--id, --source, --consent"):
        add_clip(clip, manifest_path=manifest_path, storage=storage)
    assert not storage.videos_dir.exists(), "nothing is stored when a check fails"
    assert load_manifest(manifest_path).clips == []


def test_clip_ids_are_unique(
    clip: Path, other_clip: Path, manifest_path: Path, storage: Storage
) -> None:
    register(clip, manifest_path, storage)
    with pytest.raises(BenchmarkError, match="already used"):
        register(other_clip, manifest_path, storage)


def test_one_file_cannot_get_a_second_id(clip: Path, manifest_path: Path, storage: Storage) -> None:
    register(clip, manifest_path, storage)
    with pytest.raises(BenchmarkError, match="already in the manifest as 'street-walk'"):
        register(clip, manifest_path, storage, clip_id="again")


def test_rejects_different_extension_for_same_content(
    fake_clip: Path, manifest_path: Path, storage: Storage
) -> None:
    entry = register(fake_clip, manifest_path, storage)
    mov = fake_clip.with_suffix(".mov")
    mov.write_bytes(fake_clip.read_bytes())
    with pytest.raises(BenchmarkError, match=r"clip\.mov has the same content as 'street-walk'"):
        add_clip(mov, manifest_path=manifest_path, storage=storage)
    assert not (storage.videos_dir / f"{entry.video.sha256}.mov").exists(), "no orphan copy"


def test_cloud_ok_on_existing_clip_errors_instead_of_silently_dropping(
    fake_clip: Path, manifest_path: Path, storage: Storage
) -> None:
    register(fake_clip, manifest_path, storage)
    with pytest.raises(BenchmarkError, match="--cloud-ok, --tag would be ignored"):
        add_clip(
            fake_clip,
            manifest_path=manifest_path,
            storage=storage,
            consent_cloud=True,
            tags=["faces"],
        )
    assert load_manifest(manifest_path).clips[0].consent_cloud is False
    # Defaults aren't "passed": a teammate fetching the clip still just stores it.
    assert add_clip(fake_clip, manifest_path=manifest_path, storage=storage)[1] is False


def test_detects_file_changed_during_copy(
    fake_clip: Path, manifest_path: Path, storage: Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_add_video = Storage.add_video

    def add_video_while_syncing(self: Storage, source: Path) -> VideoRef:
        ref = real_add_video(self, source)  # hashed the source, then copied it
        self.video_path(ref).write_bytes(b"the rest of the sync arrived mid-copy")
        return ref

    monkeypatch.setattr(Storage, "add_video", add_video_while_syncing)
    with pytest.raises(BenchmarkError, match="changed while it was being copied"):
        register(fake_clip, manifest_path, storage)
    assert list(storage.videos_dir.iterdir()) == [], "the bad copy is deleted"
    assert load_manifest(manifest_path).clips == []


def test_non_media_is_rejected(tmp_path: Path, manifest_path: Path, storage: Storage) -> None:
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(bytes(range(256)) * 20)
    with pytest.raises(VideoError):
        register(junk, manifest_path, storage)


@pytest.mark.parametrize(
    "bad",
    [{"source": ""}, {"consent": ""}, {"clip_id": "Bad Name"}, {"fps": "0"}, {"fps": "fast"}],
)
def test_an_entry_must_say_where_it_came_from(bad: dict, entry: ClipEntry) -> None:
    with pytest.raises(ValidationError):
        ClipEntry.model_validate({**entry.model_dump(), **bad})


def test_duplicate_videos_are_rejected(entry: ClipEntry) -> None:
    with pytest.raises(ValidationError, match="duplicate video"):
        ClipManifest(clips=[entry, entry.model_copy(update={"clip_id": "other"})])


def test_broken_manifest_names_itself(tmp_path: Path) -> None:
    path = tmp_path / "clips.json"
    path.write_text('<<<<<<< HEAD\n{"manifest_version": 1, "clips": []}\n=======\n')
    with pytest.raises(BenchmarkError, match="clips.json: not valid JSON"):
        load_manifest(path)
    path.write_text(json.dumps({"manifest_version": 1, "clips": [{"clip_id": "x"}]}))
    with pytest.raises(BenchmarkError, match="clips.json: .*validation error"):
        load_manifest(path)


def test_a_future_manifest_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "clips.json"
    path.write_text(json.dumps({"manifest_version": 2, "clips": []}))
    with pytest.raises(BenchmarkError, match="manifest_version 2"):
        load_manifest(path)


# --- Is the clip here, and is it the right one? -------------------------------------------


def test_status_ok_missing_and_wrong_size(
    entry: ClipEntry, storage: Storage, tmp_path: Path
) -> None:
    assert clip_status(entry, storage) == ("ok", "")
    assert clip_status(entry, Storage(tmp_path / "empty"))[0] == "missing"
    stored = storage.video_path(entry.video)
    stored.write_bytes(stored.read_bytes() + b"x")
    assert clip_status(entry, storage)[0] == "size_mismatch"


def test_verify_catches_a_different_file_of_the_same_size(
    entry: ClipEntry, storage: Storage
) -> None:
    stored = storage.video_path(entry.video)
    data = bytearray(stored.read_bytes())
    data[-1] ^= 0xFF
    stored.write_bytes(bytes(data))
    assert clip_status(entry, storage)[0] == "ok"  # the fast check can't see it
    assert clip_status(entry, storage, verify=True)[0] == "hash_mismatch"


def test_verify_passes_on_the_real_file(entry: ClipEntry, storage: Storage) -> None:
    assert clip_status(entry, storage, verify=True) == ("ok", "hash and metadata verified")
