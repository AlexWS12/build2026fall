import json

import pytest

from vibe.modules.vision import cache
from vibe.modules.vision.__main__ import main
from vibe.storage import Storage


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_detectors_lists_every_detector_with_its_license(capsys):
    code, out, _ = run(capsys, "detectors")
    assert code == 0
    for name in ("stub", "tv-frcnn", "rfdetr-l", "egoblur-face", "yunet", "yolo", "yoloe"):
        assert f"\n{name} " in out
    assert "AGPL" in out


def test_analyze_writes_detections_and_tracks_then_retrack_and_list(capsys, tmp_path, square_clip):
    data = str(tmp_path / "data")
    code, out, err = run(
        capsys, "--data-dir", data, "analyze", str(square_clip),
        "-d", "stub:color=red,label=person", "-d", "stub:color=blue,label=face",
    )  # fmt: skip
    assert code == 0, err
    assert "tracks:  2 (1 face, 1 person)" in out
    det_key = next(line.split()[-1] for line in out.splitlines() if "detections key" in line)

    store = Storage(tmp_path / "data")
    ref = store.add_video(square_clip)
    [tracks_file] = store.cache_path(ref.sha256, "tracks", "x").parent.glob("*.json")
    assert {t["label"] for t in json.loads(tracks_file.read_text())} == {"face", "person"}

    code, out, err = run(
        capsys, "--data-dir", data, "retrack", str(square_clip), "--from", det_key,
        "--min-conf", "0.5",
    )  # fmt: skip
    assert code == 0, err
    code, out, _ = run(capsys, "--data-dir", data, "list", str(square_clip))
    assert code == 0
    assert out.count("-iou-") == 2  # two track sets: the default and --min-conf 0.5


def test_analyze_and_list_do_not_copy_the_video_into_the_data_folder(capsys, tmp_path, square_clip):
    data = tmp_path / "data"
    code, _, err = run(
        capsys, "--data-dir", str(data), "analyze", str(square_clip), "-d", "stub:color=red"
    )
    assert code == 0, err
    run(capsys, "--data-dir", str(data), "list", str(square_clip))
    videos = data / "videos"
    assert not videos.exists() or not any(videos.iterdir())
    sha = cache.video_sha256(square_clip)
    assert (data / "cache" / sha / "tracks").is_dir()  # the cache is keyed by the same hash


def test_several_track_sets_need_a_default_and_list_shows_it(capsys, tmp_path, square_clip):
    data, store = str(tmp_path / "data"), Storage(tmp_path / "data")
    sha = cache.video_sha256(square_clip)
    for conf in ("0.3", "0.5"):
        code, out, err = run(
            capsys, "--data-dir", data, "analyze", str(square_clip),
            "-d", "stub:color=red", "--min-conf", conf,
        )  # fmt: skip
        assert code == 0, err
    keys = cache.track_keys(store, sha)
    assert len(keys) == 2
    with pytest.raises(cache.AmbiguousAnalysis):
        cache.load_tracks(store, sha)

    code, out, err = run(capsys, "--data-dir", data, "default", str(square_clip), "--key", keys[1])
    assert code == 0, err
    assert cache.load_tracks(store, sha) is not None
    _, out, _ = run(capsys, "--data-dir", data, "list", str(square_clip))
    assert f"{keys[1]}  (default)" in out
    assert f"{keys[0]}  (default)" not in out


def test_default_with_an_unknown_key_is_an_error(capsys, tmp_path, square_clip):
    code, _, err = run(
        capsys, "--data-dir", str(tmp_path / "d"), "default", str(square_clip), "--key", "nope-0"
    )
    assert code == 1
    assert "no cached tracks" in err


def test_option_values_that_look_like_numbers_stay_text_or_fail_with_a_message(
    capsys, tmp_path, square_clip
):
    data = str(tmp_path / "data")
    code, _, err = run(
        capsys, "--data-dir", data, "analyze", str(square_clip),
        "-d", "stub:color=red,label=1984",
    )  # fmt: skip
    assert code == 0, err  # label=1984 used to be parsed as an int and crash
    code, _, err = run(
        capsys, "--data-dir", data, "analyze", str(square_clip), "-d", "stub:tolerance=lots"
    )
    assert code == 1
    assert "tolerance='lots'" in err


def test_missing_packages_or_weights_are_an_error_not_a_traceback(capsys, tmp_path, square_clip):
    code, _, err = run(
        capsys, "--data-dir", str(tmp_path / "d"), "analyze", str(square_clip), "-d", "yunet"
    )
    assert code == 1
    assert err.startswith("error: yunet needs")


def test_unknown_detector_is_an_error(capsys, tmp_path, square_clip):
    code, _, err = run(
        capsys, "--data-dir", str(tmp_path / "d"), "analyze", str(square_clip), "-d", "yolo99"
    )
    assert code == 1
    assert "unknown detector" in err
