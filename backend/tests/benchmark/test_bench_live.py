"""F-50 live spike script: runs end to end on the stub (no model, no GPU, no OpenCV needed).

The clip is made with PyAV, which bundles its own ffmpeg, so no ffmpeg CLI is needed either.
These check the script's plumbing and report shape, never a speed.
"""

import json
from pathlib import Path

import av
import numpy as np
import pytest

from vibe.benchmark import live


def _clip(path: Path, n: int = 8) -> Path:
    """A red square moving right over black, 96x64 at 10 fps."""
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width, stream.height, stream.pix_fmt = 96, 64, "yuv420p"
        for i in range(n):
            image = np.zeros((64, 96, 3), dtype=np.uint8)
            image[20:44, 4 + 6 * i : 28 + 6 * i] = (255, 0, 0)
            for packet in stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


STUB = ["--detectors", "stub", "--faces", "none", "--identity", "none", "--tracker", "iou"]


def test_stub_run_writes_a_report_with_every_stage_and_the_budget(tmp_path, capsys):
    clip = _clip(tmp_path / "square.mp4")
    out = tmp_path / "r.json"
    # 12 timed frames from an 8-frame clip, so the clip has to loop.
    args = [str(clip), *STUB, "--heights", "72", "--frames", "12", "--warmup", "2"]
    assert live.main([*args, "--out", str(out)]) == 0

    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["budget_ms"]["30fps"] == pytest.approx(33.33)
    assert report["hardware"]["os"] and "torch" in report["versions"]
    assert report["clip"] == {"name": "square.mp4", "width": 96, "height": 64, "fps": 10.0}
    (result,) = report["results"]
    assert result["resolution"] == "128x72" and result["precision"] == "n/a"
    assert {"decode_source", "detect_objects", "track", "blur:numpy"} <= set(result["stages_ms"])
    assert "detect_faces" not in result["stages_ms"] and result["identity_calls"] is None
    for stats in result["end_to_end_ms"].values():
        assert stats["p50"] <= stats["p95"] <= stats["p99"] <= stats["max"]
        assert isinstance(stats["fits_30fps_p95"], bool)
    assert result["per_frame_avg"]["people_blurred"] > 0  # the square was found and blurred
    assert "results:" in capsys.readouterr().out


def test_detecting_every_k_frames_skips_detection_and_averages_over_k(tmp_path):
    clip = _clip(tmp_path / "square.mp4")
    out = tmp_path / "r.json"
    args = [str(clip), *STUB, "--heights", "72", "--frames", "12", "--warmup", "3"]
    assert live.main([*args, "--every", "1,3", "--blurs", "numpy", "--out", str(out)]) == 0

    every1, every3 = json.loads(out.read_text(encoding="utf-8"))["results"]
    assert (every1["every"], every3["every"]) == (1, 3)
    # Detection on 1 frame in 3; the blur still runs on every frame, on the held boxes.
    assert len(every3["stages_ms"]) == len(every1["stages_ms"])
    e1, e3 = every1["end_to_end_ms"]["numpy"], every3["end_to_end_ms"]["numpy"]
    assert e1["window_mean"] == {k: e1[k] for k in ("mean", "p50", "p95", "p99", "max")}
    assert e3["window_mean"]["max"] <= e3["max"]
    assert every3["per_frame_avg"]["people_blurred"] > 0


def test_canvas_keeps_the_aspect_ratio_and_centres_on_black():
    portrait = np.full((100, 50, 3), 200, dtype=np.uint8)
    out = live._canvas(portrait, 160, 90)
    assert out.shape == (90, 160, 3)
    assert out[:, :50].max() == 0 and out[:, -50:].max() == 0  # black sides
    assert out[45, 80].tolist() == [200, 200, 200]


def test_reduced_resolution_blur_changes_only_the_target(tmp_path):
    pytest.importorskip("cv2")
    rng = np.random.default_rng(0)
    image = rng.integers(0, 256, (60, 80, 3), dtype=np.uint8)
    before = image.copy()
    live._blur("numpy@0.5", image, [(10, 10, 50, 40)])
    assert not np.array_equal(image[10:40, 10:50], before[10:40, 10:50])
    image[10:40, 10:50] = before[10:40, 10:50]
    assert np.array_equal(image, before)


def test_bad_input_is_an_error_not_a_traceback(tmp_path, capsys):
    assert live.main([str(tmp_path / "missing.mp4"), *STUB]) == 2
    clip = _clip(tmp_path / "square.mp4")
    assert live.main([str(clip), "--detectors", "yolo"]) == 2
    assert live.main([str(clip), *STUB, "--every", "0"]) == 2
    assert live.main([str(clip), *STUB, "--face-scales", "2"]) == 2
    assert "error:" in capsys.readouterr().err
