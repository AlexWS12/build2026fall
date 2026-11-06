"""F-08: the stub / local / remote backends, the shared cache and the model server.

No GPU, no network, no ffmpeg: "videos" are a few random bytes (only their hash matters here),
and the model server runs in-process behind FastAPI's TestClient.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vibe.contract import Track
from vibe.modules import (
    AmbiguousAnalysis,
    Analysis,
    BiometricRefused,
    ModuleError,
    NoAnalysis,
    cache_key,
    cached_keys,
    register,
    replay,
    run_analysis,
    write_json,
)
from vibe.modules.remote import RemoteClient, RemoteError, RemoteNotAllowed, create_app
from vibe.storage import Storage, VideoRef

TRACKS = [
    {
        "track_id": "trk_1",
        "label": "face",
        "source": "detector",
        "keyframes": [
            {"t": 0.0, "box": [0.1, 0.1, 0.2, 0.2], "conf": 0.9, "state": "detected"},
            {"t": 1.0, "box": [0.15, 0.1, 0.25, 0.2], "conf": 0.4, "state": "uncertain"},
        ],
    }
]


@register
class CountingAnalysis(Analysis):
    """A fake module: returns fixed tracks and counts how often the "model" really ran."""

    module = "test-counting"
    kind = "tracks"
    calls = 0

    @property
    def name(self) -> str:
        return f"fake-{self.settings.get('size', 's')}"

    def compute(self, video_path: Path) -> object:
        assert video_path.is_file()
        type(self).calls += 1
        return TRACKS


@pytest.fixture(autouse=True)
def _reset_calls():
    CountingAnalysis.calls = 0


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "data")


def _video(storage: Storage, tmp_path: Path, content: bytes = b"not really a video") -> VideoRef:
    src = tmp_path / "clip.mp4"
    src.write_bytes(content)
    return storage.add_video(src)


# --- The exit check -------------------------------------------------------------------------


def test_stub_replays_a_cached_result(storage: Storage, tmp_path: Path) -> None:
    """F-08 exit check: results computed elsewhere replay on a machine with no model."""
    video = _video(storage, tmp_path)
    analysis = CountingAnalysis(size="l")
    # As if copied from the 5070: the file another machine wrote under the same key.
    write_json(storage, video.sha256, "tracks", analysis.key, TRACKS)

    result = run_analysis(storage, video, analysis, backend="stub")

    assert result.source == "cache"
    assert result.key == analysis.key
    assert result.data == TRACKS
    assert [Track.model_validate(t) for t in result.data][0].label == "face"
    assert CountingAnalysis.calls == 0


def test_stub_with_nothing_cached_fails_closed(storage: Storage, tmp_path: Path) -> None:
    video = _video(storage, tmp_path)
    with pytest.raises(NoAnalysis, match="copy cache/"):
        run_analysis(storage, video, CountingAnalysis(), backend="stub")


def test_stub_miss_names_results_cached_under_other_settings(
    storage: Storage, tmp_path: Path
) -> None:
    video = _video(storage, tmp_path)
    other = CountingAnalysis(size="l")
    write_json(storage, video.sha256, "tracks", other.key, TRACKS)
    with pytest.raises(NoAnalysis, match=other.key):
        run_analysis(storage, video, CountingAnalysis(size="n"), backend="stub")


def test_default_backend_is_stub(storage: Storage, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("VIBE_BACKEND", raising=False)
    video = _video(storage, tmp_path)
    with pytest.raises(NoAnalysis):
        run_analysis(storage, video, CountingAnalysis())
    assert CountingAnalysis.calls == 0


# --- local -------------------------------------------------------------------------------


def test_local_computes_once_then_hits_the_cache(storage: Storage, tmp_path: Path) -> None:
    video = _video(storage, tmp_path)
    first = run_analysis(storage, video, CountingAnalysis(), backend="local")
    second = run_analysis(storage, video, CountingAnalysis(), backend="local")
    assert (first.source, second.source) == ("local", "cache")
    assert first.data == second.data == TRACKS
    assert CountingAnalysis.calls == 1
    # ...and a stub laptop given this cache folder gets the same thing.
    assert run_analysis(storage, video, CountingAnalysis(), backend="stub").data == TRACKS


def test_local_refuses_to_cache_nothing(storage: Storage, tmp_path: Path) -> None:
    class Empty(CountingAnalysis):
        module = "test-empty"

        def compute(self, video_path: Path) -> object:
            return None

    video = _video(storage, tmp_path)
    with pytest.raises(ModuleError, match="refusing to cache nothing"):
        run_analysis(storage, video, Empty(), backend="local")
    assert cached_keys(storage, video.sha256, "tracks") == []


# --- keys and the cache -------------------------------------------------------------------


def test_key_changes_with_settings_and_version() -> None:
    base = cache_key("rfdetr-l", 1, {"every": 1, "threshold": 0.3})
    assert base == cache_key("rfdetr-l", 1, {"threshold": 0.3, "every": 1})  # order-free
    assert base != cache_key("rfdetr-l", 1, {"every": 2, "threshold": 0.3})
    assert base != cache_key("rfdetr-l", 2, {"every": 1, "threshold": 0.3})
    assert base != cache_key("rfdetr-n", 1, {"every": 1, "threshold": 0.3})
    assert base.startswith("rfdetr-l-") and len(base) == len("rfdetr-l-") + 8


def test_key_is_always_a_safe_cache_name(storage: Storage) -> None:
    key = cache_key("yoloe: coffee mug / poster" + "x" * 200, 1, {})
    assert len(key) <= 128
    storage.cache_path("0" * 64, "tracks", key)  # Storage would raise on an unsafe name


def test_replay_picks_the_only_result_and_refuses_to_guess(
    storage: Storage, tmp_path: Path
) -> None:
    video = _video(storage, tmp_path)
    with pytest.raises(NoAnalysis):
        replay(storage, video.sha256, "tracks")

    write_json(storage, video.sha256, "tracks", "fake-s-00000001", TRACKS)
    assert replay(storage, video.sha256, "tracks").key == "fake-s-00000001"

    write_json(storage, video.sha256, "tracks", "fake-l-00000002", [])
    with pytest.raises(AmbiguousAnalysis, match="fake-l-00000002, fake-s-00000001"):
        replay(storage, video.sha256, "tracks")
    # Picking one is fine, and an empty result is a real answer ("nothing found"), not None.
    assert replay(storage, video.sha256, "tracks", key="fake-l-00000002").data == []


@pytest.mark.parametrize(
    "kind",
    [
        "embeddings",
        "identity",
        "face_embeddings",
        "Biometric",
        "face-embeddings",
        "speaker_embedding",
        "Identities",
        "voiceprints",
    ],
)
def test_biometric_kinds_never_reach_the_shared_cache(storage: Storage, kind: str) -> None:
    with pytest.raises(BiometricRefused, match="D-017"):
        write_json(storage, "0" * 64, kind, "sface-00000000", [[0.1, 0.2]])
    assert not storage.cache_dir.exists()


class BiometricFlagged(Analysis):
    module = "test-biometric-flag"
    kind = "faces"  # an innocent-looking kind: the flag alone must refuse it
    biometric = True
    calls = 0

    def compute(self, video_path: Path) -> object:
        type(self).calls += 1
        return [[0.1, 0.2]]


class BiometricByKind(BiometricFlagged):
    module = "test-biometric-kind"
    kind = "face-embeddings"
    biometric = False


@pytest.mark.parametrize("cls", [BiometricFlagged, BiometricByKind])
@pytest.mark.parametrize("backend", ["stub", "local", "remote"])
def test_biometric_analyses_are_refused_before_any_work(
    storage: Storage, tmp_path: Path, cls: type[Analysis], backend: str
) -> None:
    video = _video(storage, tmp_path)
    cls.calls = 0
    with pytest.raises(BiometricRefused, match="D-017"):
        run_analysis(storage, video, cls(), backend=backend)  # remote: never reaches the env
    assert cls.calls == 0
    assert not storage.cache_dir.exists()


def test_corrupt_cache_file_is_an_error_not_a_miss(storage: Storage, tmp_path: Path) -> None:
    video = _video(storage, tmp_path)
    analysis = CountingAnalysis()
    path = storage.cache_path(video.sha256, "tracks", analysis.key)
    path.parent.mkdir(parents=True)
    path.write_text("{half a file", encoding="utf-8")
    with pytest.raises(ModuleError, match="isn't valid JSON"):
        run_analysis(storage, video, analysis, backend="local")
    assert CountingAnalysis.calls == 0


def test_register_refuses_a_second_module_with_the_same_name() -> None:
    with pytest.raises(ValueError, match="already registered"):

        @register
        class Clash(CountingAnalysis):
            module = "test-counting"


# --- remote -------------------------------------------------------------------------------


def _client(server_storage: Storage, token: str | None = None, **kw) -> RemoteClient:
    """A RemoteClient whose HTTP goes to an in-process model server."""
    http = TestClient(create_app(server_storage, token=token))

    def transport(method, url, headers, body, timeout):
        path = url.removeprefix("http://gpu-box:8765")
        content = body.read_bytes() if isinstance(body, Path) else body
        r = http.request(method, path, headers=headers, content=content)
        return r.status_code, r.json()

    options = {"allowed_hosts": frozenset({"gpu-box"}), "token": token} | kw
    return RemoteClient("http://gpu-box:8765", transport=transport, **options)


def test_remote_uploads_once_runs_on_the_server_and_caches_here(
    storage: Storage, tmp_path: Path
) -> None:
    server = Storage(tmp_path / "server")
    video = _video(storage, tmp_path)
    client = _client(server)

    data = client.analyze(storage, video, CountingAnalysis(size="l"))
    assert data == TRACKS
    assert server.video_path(video).read_bytes() == storage.video_path(video).read_bytes()
    assert CountingAnalysis.calls == 1
    # The server kept its own copy: asking again runs no model there.
    assert client.analyze(storage, video, CountingAnalysis(size="l")) == TRACKS
    assert CountingAnalysis.calls == 1


def test_remote_backend_through_run_analysis(storage: Storage, tmp_path: Path, monkeypatch) -> None:
    server = Storage(tmp_path / "server")
    client = _client(server)
    monkeypatch.setattr(RemoteClient, "from_env", classmethod(lambda cls: client))
    video = _video(storage, tmp_path)

    result = run_analysis(storage, video, CountingAnalysis(), backend="remote")
    assert (result.source, result.data) == ("remote", TRACKS)
    # Cached here, so this laptop can now replay it with the stub.
    assert run_analysis(storage, video, CountingAnalysis(), backend="stub").source == "cache"


def test_remote_refuses_hosts_off_the_allowlist(storage: Storage, tmp_path: Path) -> None:
    video = _video(storage, tmp_path)
    client = _client(Storage(tmp_path / "server"), allowed_hosts=frozenset({"other-box"}))
    with pytest.raises(RemoteNotAllowed, match="gpu-box.*D-017"):
        client.analyze(storage, video, CountingAnalysis())
    assert CountingAnalysis.calls == 0


def test_remote_needs_an_allowlist_from_env(monkeypatch) -> None:
    monkeypatch.setenv("VIBE_REMOTE_URL", "https://api.example.com")
    monkeypatch.delenv("VIBE_REMOTE_HOSTS", raising=False)
    client = RemoteClient.from_env()
    with pytest.raises(RemoteNotAllowed, match="none"):
        client.health()


def test_remote_without_url_says_what_to_set(monkeypatch) -> None:
    monkeypatch.delenv("VIBE_REMOTE_URL", raising=False)
    with pytest.raises(RemoteError, match="VIBE_REMOTE_URL"):
        RemoteClient.from_env()


def test_remote_token_is_checked(storage: Storage, tmp_path: Path) -> None:
    server = Storage(tmp_path / "server")
    video = _video(storage, tmp_path)
    good = _client(server, token="s3cret")
    assert good.analyze(storage, video, CountingAnalysis()) == TRACKS

    http = TestClient(create_app(server, token="s3cret"))
    assert http.get("/v1/health").status_code == 401
    assert http.get("/v1/health", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_remote_rejects_a_server_running_another_version(storage: Storage, tmp_path: Path) -> None:
    class Newer(CountingAnalysis):
        module = "test-newer"
        version = 2

    register(Newer)  # the server's copy
    video = _video(storage, tmp_path)

    class Older(Newer):  # this machine's copy: same module name, older code
        version = 1

    with pytest.raises(RemoteError, match="another version"):
        _client(Storage(tmp_path / "server")).analyze(storage, video, Older())
    assert cached_keys(storage, video.sha256, "tracks") == []


def test_server_checks_the_uploaded_hash(tmp_path: Path) -> None:
    http = TestClient(create_app(Storage(tmp_path / "server")))
    r = http.put(f"/v1/videos/{'a' * 64}?ext=.mp4", content=b"something else")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "hash_mismatch"
    assert not (tmp_path / "server" / "videos" / f"{'a' * 64}.mp4").exists()


def test_server_upload_leaves_no_temp_files(storage: Storage, tmp_path: Path) -> None:
    server = Storage(tmp_path / "server")
    video = _video(storage, tmp_path, b"x" * 300_000)  # several stream chunks
    http = TestClient(create_app(server))
    url = f"/v1/videos/{video.sha256}?ext={video.ext}"
    r = http.put(url, content=storage.video_path(video).read_bytes())
    assert r.status_code == 200 and r.json()["stored"] is True
    assert server.video_path(video).read_bytes() == storage.video_path(video).read_bytes()
    http.put(f"/v1/videos/{'a' * 64}?ext=.mp4", content=b"bad")  # hash mismatch
    assert sorted(p.name for p in server.videos_dir.iterdir()) == [video.filename]


def test_server_reports_unknown_modules(tmp_path: Path) -> None:
    http = TestClient(create_app(Storage(tmp_path / "server")))
    r = http.post(
        "/v1/analyze", json={"module": "nope", "settings": {}, "video": {"sha256": "a" * 64}}
    )
    assert r.status_code == 422 and "unknown analysis module" in r.json()["detail"]["message"]


# --- no GPU packages on the stub path ------------------------------------------------------


def test_stub_path_imports_no_model_packages() -> None:
    code = (
        "import sys, vibe.modules, vibe.modules.remote, vibe.modules.doctor, "
        "vibe.modules.download\n"
        "bad = [m for m in ('torch', 'torchvision', 'onnxruntime', 'cv2') if m in sys.modules]\n"
        "print(','.join(bad))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


def test_cli_cache_lists_keys(storage: Storage, tmp_path: Path, monkeypatch, capsys) -> None:
    from vibe.modules.__main__ import main

    monkeypatch.setenv("VIBE_DATA_DIR", str(storage.root))
    video = _video(storage, tmp_path)
    write_json(storage, video.sha256, "tracks", "fake-s-00000001", TRACKS)
    assert main(["cache", video.sha256]) == 0
    assert capsys.readouterr().out.strip() == "tracks/fake-s-00000001"
    assert json.loads(storage.cache_path(video.sha256, "tracks", "fake-s-00000001").read_text())


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20"])
def test_cli_serve_refuses_an_open_network_server_without_a_token(
    monkeypatch, capsys, host: str
) -> None:
    import uvicorn

    from vibe.modules.__main__ import main

    ran = []
    monkeypatch.delenv("VIBE_REMOTE_TOKEN", raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: ran.append(kw))
    assert main(["serve", "--host", host]) == 2
    assert "refusing" in capsys.readouterr().err and ran == []

    assert main(["serve", "--host", host, "--insecure"]) == 0
    assert ran == [{"host": host, "port": 8765}]

    monkeypatch.setenv("VIBE_REMOTE_TOKEN", "s3cret")
    assert main(["serve", "--host", host]) == 0
    assert main(["serve"]) == 0  # localhost needs no token
    assert len(ran) == 3
