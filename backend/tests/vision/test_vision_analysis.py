"""VisionAnalysis: detect + track as an F-08 analysis, so stub / local / remote all run it (F-10b).

Everything here uses the stub color detector, so no model package and no GPU are needed.
"""

from pathlib import Path

import pytest

from vibe import config
from vibe.contract import Track
from vibe.modules import ModuleError, NoAnalysis, run_analysis
from vibe.modules.backend import load_modules, make_analysis, registered
from vibe.modules.vision import VisionAnalysis, load_tracks
from vibe.modules.vision.analysis import VisionNotInstalled
from vibe.modules.vision.detection import NotInstalled
from vibe.modules.vision.detectors import check_spec, make_detector, weights_refs
from vibe.storage import Storage

RED_FACE = "stub:color=red,label=face"


def test_vision_is_registered_for_the_model_server() -> None:
    load_modules()
    assert registered()["vision"] is VisionAnalysis


def test_settings_rebuild_the_same_analysis_by_name() -> None:
    a = VisionAnalysis([RED_FACE], every=2, tracker="iou", tracker_settings={"max_gap": 0.5})
    b = make_analysis("vision", a.settings)
    assert b.key == a.key
    assert b.settings == a.settings
    assert a.key.startswith("vision-stub-k2-iou-")


@pytest.mark.parametrize(
    "change",
    [
        {"detectors": ["stub:color=blue,label=face"]},
        {"every": 3},
        {"tracker_settings": {"min_conf": 0.5}},
    ],
)
def test_any_setting_changes_the_key(change: dict) -> None:
    base = {"detectors": [RED_FACE], "every": 1, "tracker": "iou"}
    assert VisionAnalysis(**base).key != VisionAnalysis(**{**base, **change}).key


@pytest.mark.parametrize(
    "settings",
    [
        {"detectors": []},
        {"detectors": ["nope"]},
        {"detectors": ["stub:size=3"]},  # not an option of the stub detector
        {"detectors": [RED_FACE], "every": 0},
        {"detectors": [RED_FACE], "tracker": "nope"},
        {"detectors": [RED_FACE], "tracker_settings": {"min_conf": 2}},
        {"detectors": ["yunet:weights=@nope"]},
    ],
)
def test_bad_settings_are_a_module_error_before_anything_runs(settings: dict) -> None:
    with pytest.raises(ModuleError, match="bad settings"):
        make_analysis("vision", settings)


def test_compute_returns_namespaced_tracks_in_the_contract_shape(square_clip: Path) -> None:
    analysis = VisionAnalysis([RED_FACE])

    data = analysis.compute(square_clip)

    tracks = [Track.model_validate(t) for t in data]
    assert len(tracks) == 1
    assert tracks[0].label == "face"
    digest = analysis.key.rsplit("-", 1)[-1]
    assert tracks[0].track_id.startswith(f"trk_{digest}_")


def test_local_run_caches_tracks_where_the_app_reads_them(
    tmp_path: Path, square_clip: Path
) -> None:
    storage = Storage(tmp_path / "data")
    video = storage.add_video(square_clip)
    analysis = VisionAnalysis([RED_FACE])

    result = run_analysis(storage, video, analysis, backend="local")

    assert result.source == "local"
    cached = load_tracks(storage, video.sha256)  # one set, no default needed
    assert cached is not None and [t.track_id for t in cached] == [
        t["track_id"] for t in result.data
    ]
    again = run_analysis(storage, video, analysis, backend="stub")
    assert again.source == "cache"


def test_stub_with_nothing_cached_is_no_analysis(tmp_path: Path, square_clip: Path) -> None:
    storage = Storage(tmp_path / "data")
    video = storage.add_video(square_clip)
    with pytest.raises(NoAnalysis):
        run_analysis(storage, video, VisionAnalysis([RED_FACE]), backend="stub")


def test_missing_weights_say_how_to_get_them(
    tmp_path: Path, square_clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_MODELS_DIR", str(tmp_path / "models"))
    analysis = VisionAnalysis(["yunet:weights=@yunet"])
    with pytest.raises(VisionNotInstalled, match="(download yunet|opencv)"):
        analysis.compute(square_clip)


def test_weights_by_name_resolve_to_the_models_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_MODELS_DIR", str(tmp_path))
    check_spec("yunet:weights=@yunet")
    assert set(weights_refs(["yunet:weights=@yunet", RED_FACE])) == {"yunet"}
    # Not downloaded: the ref is resolved (and refused) before the detector imports OpenCV.
    with pytest.raises(NotInstalled, match="download yunet"):
        make_detector("yunet:weights=@yunet")


def test_two_detectors_of_one_kind_run_side_by_side(square_clip: Path) -> None:
    data = VisionAnalysis([RED_FACE, "stub:color=blue,label=face"]).compute(square_clip)
    assert len(data) == 2


def test_default_detectors_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIBE_VISION_DETECTORS", raising=False)
    assert config.vision_detectors() == ["yunet:weights=@yunet"]
    monkeypatch.setenv("VIBE_VISION_DETECTORS", f" {RED_FACE} ; stub:color=blue ")
    assert config.vision_detectors() == [RED_FACE, "stub:color=blue"]
    assert VisionAnalysis.default().detectors == [RED_FACE, "stub:color=blue"]


def test_remote_runs_vision_on_the_model_server_and_caches_here(
    tmp_path: Path, square_clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from vibe.modules.remote import RemoteClient, create_app

    laptop, server = Storage(tmp_path / "laptop"), Storage(tmp_path / "server")
    http = TestClient(create_app(server))

    def transport(method, url, headers, body, timeout):
        content = body.read_bytes() if isinstance(body, Path) else body
        r = http.request(
            method, url.removeprefix("http://gpu-box:8765"), headers=headers, content=content
        )
        return r.status_code, r.json()

    client = RemoteClient("http://gpu-box:8765", frozenset({"gpu-box"}), None, transport=transport)
    monkeypatch.setattr(RemoteClient, "from_env", classmethod(lambda cls: client))
    video = laptop.add_video(square_clip)

    result = run_analysis(laptop, video, VisionAnalysis([RED_FACE]), backend="remote")

    assert result.source == "remote"
    assert load_tracks(laptop, video.sha256) == load_tracks(server, video.sha256)
    assert load_tracks(laptop, video.sha256)
