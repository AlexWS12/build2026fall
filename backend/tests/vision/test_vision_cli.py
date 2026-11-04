import json

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
