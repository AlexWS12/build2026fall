"""Live feasibility spike (F-50): how long one frame of the live pipeline takes, stage by stage.

Plays a clip as if it were a camera and times, per frame: object detection, face detection,
tracking, the identity check and the blur. Live mode needs the whole frame done in 33.3 ms (30
fps); the delay buffer absorbs spikes, so the number that decides is p95 (docs/ARCHITECTURE.md,
"Live mode"; docs/research/r3-model-shortlist.md, "What F-50 should measure").

    uv run python -m vibe.benchmark.live CLIP [--detectors rfdetr-n,rfdetr-s] [--precisions
        fp32,fp16] [--heights 720,1080] [--frames 300] [--out results.json]

How it measures, so the numbers mean something:

- Each clip frame is scaled to fit the target height and centred on a black 16:9 frame, so every
  stage works at the target resolution whatever the clip's size. That scaling stands in for the
  camera and isn't timed. Decoding is timed on its own, at the clip's size, and kept out of the
  end-to-end total: a webcam hands over raw frames.
- The first ``--warmup`` frames aren't counted (GPU start-up). GPU work is synchronized before a
  stage's clock stops. The clip loops until ``--frames`` frames are timed.
- **Blur is worst case:** every person and face is blurred, as if nobody were the enrolled user.
  Each blur variant runs on its own copy of the frame, and the end-to-end total is computed once
  per variant: ``numpy`` is the renderer's own effect (``vibe.render.effects``), ``numpy@0.5``
  and ``numpy@0.25`` blur a downscaled region and scale it back, and ``opencv`` runs the same
  three box-blur passes with OpenCV.
- **Identity:** SFace runs only when a new face track starts (R-3, pipeline 1). The first face
  is taken as the enrolled user, which is fine for timing and says nothing about accuracy.
  Embeddings stay in memory and are never written anywhere (D-017).
- YuNet and SFace run on the CPU (the ``opencv-python`` wheel has no CUDA); RF-DETR on the GPU.

Only numbers this script prints count as measured, with the hardware it records (CLAUDE.md,
"Honesty"). Speed only: whether faces are found and "me" is recognized needs labelled clips.

``--detectors stub --faces none --identity none --tracker iou`` runs with no model and no GPU.
"""

import argparse
import json
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import av
import numpy as np

from vibe import config
from vibe.contract import Effect
from vibe.modules import doctor
from vibe.modules.vision.detection import Detection, NotInstalled, iou, normalize_box
from vibe.modules.vision.detectors.stub import ColorDetector
from vibe.modules.vision.tracking import TRACKERS, TrackerSettings
from vibe.render.effects import BLUR_PASSES, BLUR_RADIUS, DEFAULT_STRENGTH, apply_effect

BUDGET_MS = 1000 / 30
"""One frame at 30 fps. p95 has to fit."""
BUDGET_60_MS = 1000 / 60

RFDETR_SIZES = {"rfdetr-n": "RFDETRNano", "rfdetr-s": "RFDETRSmall", "rfdetr-m": "RFDETRMedium"}
BLUR_VARIANTS = ("numpy", "numpy@0.5", "numpy@0.25", "opencv")
BLUR_EFFECT = Effect(type="blur")
IDENTITY_CALLS = 30
"""Extra SFace checks timed after the run, for the cost of one call."""
YUNET_FILE = "face_detection_yunet_2023mar.onnx"
SFACE_FILE = "face_recognition_sface_2021dec.onnx"


# --- the stages ----------------------------------------------------------------------------


class _RFDetr:
    """RF-DETR at a chosen precision. Same model and output as ``detectors/rfdetr_det.py``, but
    through rfdetr's ``inference(dtype=...)`` path, which is what offers FP16."""

    def __init__(self, name: str, precision: str, threshold: float) -> None:
        try:
            import rfdetr
            import torch
        except ImportError as e:
            raise NotInstalled(
                f"{name} needs `uv pip install rfdetr` and CUDA PyTorch ({e})"
            ) from e
        try:
            from rfdetr.assets.coco_classes import COCO_CLASSES
        except ImportError:
            from rfdetr.util.coco_classes import COCO_CLASSES
        dtype = {"fp32": torch.float32, "fp16": torch.float16}[precision]
        self._model = getattr(rfdetr, RFDETR_SIZES[name])()
        self._run = self._model.inference(compile=False, dtype=dtype)
        self._classes = COCO_CLASSES
        self._threshold = threshold
        self.version = getattr(rfdetr, "__version__", "?")

    def detect(self, image: np.ndarray) -> list[Detection]:
        height, width = image.shape[:2]
        out = self._run.predict(image, threshold=self._threshold, include_source_image=False)
        found = []
        for (x1, y1, x2, y2), score, cls in zip(
            out.xyxy.tolist(), out.confidence.tolist(), out.class_id.tolist(), strict=True
        ):
            box = normalize_box(x1, y1, x2, y2, width, height)
            if box is not None:
                found.append(Detection(self._classes[int(cls)], box, float(score)))
        return found


class _YuNet:
    """YuNet with the same settings as ``detectors/yunet_det.py``, but keeping each face's
    landmark row, which SFace needs to align the face."""

    def __init__(self, weights: Path, scale: float = 1.0, threshold: float = 0.3) -> None:
        import cv2

        if not weights.is_file():
            raise NotInstalled(f"no YuNet weights at {weights}: run `python -m vibe.modules "
                               "download yunet --pin`")  # fmt: skip
        self._cv2 = cv2
        self._model = cv2.FaceDetectorYN.create(str(weights), "", (320, 320), threshold, 0.3, 5000)
        self._size: tuple[int, int] | None = None
        self._scale = scale

    def detect(self, bgr: np.ndarray) -> tuple[list[Detection], list[np.ndarray]]:
        """Faces, with landmark rows in full-frame pixels. With ``scale`` < 1 it looks at a
        smaller copy of the frame (the resize is part of the timed cost)."""
        if self._scale < 1:
            h, w = bgr.shape[:2]
            size = (max(1, round(w * self._scale)), max(1, round(h * self._scale)))
            bgr = self._cv2.resize(bgr, size, interpolation=self._cv2.INTER_AREA)
        height, width = bgr.shape[:2]
        if self._size != (width, height):
            self._model.setInputSize((width, height))
            self._size = (width, height)
        _, faces = self._model.detect(bgr)
        dets, rows = [], []
        for row in faces if faces is not None else []:
            x, y, w, h = (float(v) for v in row[:4])
            box = normalize_box(x, y, x + w, y + h, width, height)
            if box is not None:
                full = row.copy()
                full[:14] /= self._scale  # box and 5 landmarks back to full-frame pixels
                dets.append(Detection("face", box, float(row[-1])))
                rows.append(full)
        return dets, rows


class _SFace:
    """Embeds a face and compares it with the enrolled one. Embeddings live only in memory."""

    def __init__(self, weights: Path) -> None:
        import cv2

        if not weights.is_file():
            raise NotInstalled(f"no SFace weights at {weights}: run `python -m vibe.modules "
                               "download sface --pin`")  # fmt: skip
        self._cv2 = cv2
        self._model = cv2.FaceRecognizerSF.create(str(weights), "")
        self._enrolled: np.ndarray | None = None

    def check(self, bgr: np.ndarray, row: np.ndarray) -> None:
        embedding = self._model.feature(self._model.alignCrop(bgr, row))
        if self._enrolled is None:
            self._enrolled = embedding.copy()
            return
        self._model.match(self._enrolled, embedding, self._cv2.FaceRecognizerSF_FR_COSINE)


def _rect(box: tuple[float, float, float, float], width: int, height: int) -> tuple[int, ...]:
    """A normalized box grown by the effect's padding, in pixels, clipped to the frame."""
    x1, y1, x2, y2 = box
    pad_x, pad_y = (x2 - x1) * BLUR_EFFECT.padding, (y2 - y1) * BLUR_EFFECT.padding
    return (
        max(0, int((x1 - pad_x) * width)),
        max(0, int((y1 - pad_y) * height)),
        min(width, int(np.ceil((x2 + pad_x) * width))),
        min(height, int(np.ceil((y2 + pad_y) * height))),
    )


def _blur(variant: str, image: np.ndarray, rects: list[tuple[int, ...]]) -> None:
    cv2 = _cv2() if variant != "numpy" else None
    for x1, y1, x2, y2 in rects:
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        if variant == "numpy":
            apply_effect(image, (x1, y1, x2, y2), BLUR_EFFECT)
        elif variant == "opencv":
            region = image[y1:y2, x1:x2]
            lo, hi = BLUR_RADIUS
            radius = max(2, round(min(region.shape[:2]) * (lo + (hi - lo) * DEFAULT_STRENGTH)))
            out = region
            for _ in range(BLUR_PASSES):
                out = cv2.blur(out, (2 * radius + 1, 2 * radius + 1),
                               borderType=cv2.BORDER_REPLICATE)  # fmt: skip
            region[...] = out
        else:  # numpy@<scale>: blur a smaller copy, then scale it back up
            scale = float(variant.split("@")[1])
            region = image[y1:y2, x1:x2]
            h, w = region.shape[:2]
            small = cv2.resize(region, (max(2, round(w * scale)), max(2, round(h * scale))),
                               interpolation=cv2.INTER_AREA)  # fmt: skip
            apply_effect(small, (0, 0, small.shape[1], small.shape[0]), BLUR_EFFECT)
            region[...] = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


# --- one run -------------------------------------------------------------------------------


@dataclass
class _Clock:
    sync: Callable[[], None]
    times: dict[str, list[float]] = field(default_factory=dict)
    """Every recorded run of each stage. A stage skipped on a frame has no entry for it."""
    frame: dict[str, float] = field(default_factory=dict)
    """This frame's stages, for the end-to-end total."""
    recording: bool = False

    @contextmanager
    def stage(self, name: str, gpu: bool = False) -> Iterator[None]:
        start = time.perf_counter()
        yield
        if gpu:
            self.sync()  # GPU work is queued: wait for it before stopping the clock
        ms = (time.perf_counter() - start) * 1000
        self.frame[name] = ms
        if self.recording:
            self.times.setdefault(name, []).append(ms)


def _frames(clip: Path, clock: _Clock) -> Iterator[np.ndarray]:
    """The clip's frames as RGB, looping forever, with decoding timed as ``decode_source``."""
    while True:
        with av.open(str(clip)) as container:
            decoded = container.decode(video=0)
            while True:
                with clock.stage("decode_source"):
                    frame = next(decoded, None)
                if frame is None:
                    break
                yield frame.to_ndarray(format="rgb24")


def _canvas(image: np.ndarray, width: int, height: int) -> np.ndarray:
    """The frame scaled to fit ``width`` x ``height`` and centred on black (not timed)."""
    h, w = image.shape[:2]
    scale = min(width / w, height / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    out = np.zeros((height, width, 3), dtype=np.uint8)
    x, y = (width - nw) // 2, (height - nh) // 2
    cv2 = _cv2()
    if cv2 is not None:
        scaled = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
    else:  # nearest neighbour, so the stub run needs no OpenCV
        scaled = image[(np.arange(nh) * h // nh)[:, None], np.arange(nw) * w // nw]
    out[y : y + nh, x : x + nw] = scaled
    return out


def _stats(values: list[float]) -> dict[str, float]:
    a = np.asarray(values)
    return {
        "mean": round(float(a.mean()), 2),
        "p50": round(float(np.percentile(a, 50)), 2),
        "p95": round(float(np.percentile(a, 95)), 2),
        "p99": round(float(np.percentile(a, 99)), 2),
        "max": round(float(a.max()), 2),
    }


def _new_face_tracks(tracker: Any, before: int, t: float) -> list[tuple[float, ...]]:
    """Boxes of face tracks the last update started. Peeks at the tracker's track builder, which
    is fine for a benchmark and would need a real API on the live path."""
    builder = tracker._builder
    return [
        builder._keyframes[key][-1].box
        for key, label in builder._labels.items()
        if key > before and label == "face" and builder._keyframes[key][-1].t == t
    ]


PIPELINE = ("detect_objects", "detect_faces", "track", "identity")


def run_config(
    args: argparse.Namespace,
    detector: str,
    precision: str,
    height: int,
    every: int = 1,
    face_scale: float = 1.0,
) -> dict:
    torch = _torch()
    variants = tuple(args.blurs) if _cv2() is not None else ("numpy",)
    cuda = bool(torch and torch.cuda.is_available())
    if cuda:
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
    clock = _Clock(sync=torch.cuda.synchronize if cuda else lambda: None)

    if detector == "stub":
        objects: Any = ColorDetector(label="person", color="red")
    else:
        objects = _RFDetr(detector, precision, args.threshold)
    faces = _YuNet(args.yunet, face_scale) if args.faces == "yunet" else None
    identity = _SFace(args.sface) if args.identity == "sface" else None
    if identity and not faces:
        raise SystemExit("--identity sface needs --faces yunet (it aligns faces with YuNet)")
    settings = TrackerSettings()
    tracker = TRACKERS[args.tracker](settings)

    width = round(height * 16 / 9)
    total = args.warmup + args.frames
    persons = face_count = identity_calls = 0
    last_face: tuple[np.ndarray, np.ndarray] | None = None
    hide: list[Detection] = []
    totals: dict[str, list[float]] = {v: [] for v in variants}
    source = _frames(args.clip, clock)
    for i in range(total):
        clock.recording = i >= args.warmup
        clock.frame = {}
        image = _canvas(next(source), width, height)
        bgr = np.ascontiguousarray(image[..., ::-1])  # OpenCV wants BGR (not timed)
        t = i / 30

        # Detect every ``every`` frames. In between, the last boxes are blurred again: no motion
        # prediction, which a real tracker would add for about the cost of the ``track`` stage.
        if i % every == 0:
            with clock.stage("detect_objects", gpu=cuda):
                dets = objects.detect(image)
            face_dets: list[Detection] = []
            rows: list[np.ndarray] = []
            if faces:
                with clock.stage("detect_faces"):
                    face_dets, rows = faces.detect(bgr)

            everything = [d for d in dets if settings.keeps(d)] + face_dets
            before = len(tracker._builder._labels)
            with clock.stage("track"):
                tracker.update(t, everything, (width, height))

            if identity:
                with clock.stage("identity"):
                    for box in _new_face_tracks(tracker, before, t):
                        scores = [iou(d.box, box) for d in face_dets]
                        if scores:
                            identity.check(bgr, rows[int(np.argmax(scores))])
                            identity_calls += int(clock.recording)
            hide = [d for d in everything if d.label in ("person", "face")]
            if clock.recording:
                face_count += len(face_dets)
            if identity and rows:
                last_face = (bgr, rows[0])

        rects = [_rect(d.box, width, height) for d in hide]
        for variant in variants:
            copy = image.copy()
            with clock.stage(f"blur:{variant}"):
                _blur(variant, copy, rects)
        if clock.recording:
            persons += sum(d.label == "person" for d in hide)
            base = sum(clock.frame.get(s, 0.0) for s in PIPELINE)
            for variant in variants:
                totals[variant].append(base + clock.frame[f"blur:{variant}"])

    # New face tracks are rare (one when someone walks in), so the in-loop ``identity`` stage is
    # the amortized cost. The cost of one check is timed on its own, on the last face seen.
    if identity and last_face is not None:
        clock.recording = True
        for _ in range(IDENTITY_CALLS):
            with clock.stage("identity_per_call"):
                identity.check(*last_face)

    end_to_end = {}
    for variant in variants:
        per_frame = np.asarray(totals[variant])
        stats: dict[str, Any] = _stats(list(per_frame))
        # With detection every k frames, one frame in k is heavy. The delay buffer lets a heavy
        # frame borrow time from the light ones after it, so what has to fit is the average over
        # each run of k frames (every k in a row hold exactly one detection frame).
        n = len(per_frame) // every * every
        windows = per_frame[:n].reshape(-1, every).mean(axis=1)
        stats["window_mean"] = _stats(list(windows))
        stats["fits_30fps_p95"] = stats["window_mean"]["p95"] <= BUDGET_MS
        stats["fits_60fps_p95"] = stats["window_mean"]["p95"] <= BUDGET_60_MS
        end_to_end[variant] = stats

    result = {
        "detector": detector,
        "precision": precision if detector != "stub" else "n/a",
        "every": every,
        "face_scale": face_scale if faces else None,
        "resolution": f"{width}x{height}",
        "frames_timed": args.frames,
        "warmup_frames": args.warmup,
        "stages_ms": {s: _stats(v) for s, v in clock.times.items()},
        "end_to_end_ms": end_to_end,
        "per_frame_avg": {
            "people_blurred": round(persons / args.frames, 2),
            "faces_per_detection": round(face_count / max(1, len(clock.times.get("track", []))), 2),
        },
        "identity_calls": identity_calls if identity else None,
        "vram_peak_mb": None,
    }
    if cuda:
        result["vram_peak_mb"] = {
            "allocated": round(torch.cuda.max_memory_allocated() / 2**20),
            "reserved": round(torch.cuda.max_memory_reserved() / 2**20),
        }
    return result


# --- CLI -----------------------------------------------------------------------------------


def _cv2() -> Any:
    """OpenCV if installed. Only the stub run works without it (YuNet, SFace and three of the
    four blur variants need it)."""
    try:
        import cv2
    except ImportError:
        return None
    return cv2


def _torch() -> Any:
    try:
        import torch
    except ImportError:
        return None
    return torch


def _versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {"python": sys.version.split()[0], "av": av.__version__}
    for name in ("torch", "rfdetr", "cv2", "supervision", "numpy"):
        try:
            out[name] = getattr(__import__(name), "__version__", "?")
        except ImportError:
            out[name] = None
    torch = _torch()
    if torch is not None:
        out["cudnn"] = str(torch.backends.cudnn.version())
    try:
        from importlib.metadata import version

        out["trackers"] = version("trackers")
    except Exception:
        out["trackers"] = None
    return out


def _list(text: str) -> list[str]:
    return [p.strip() for p in text.split(",") if p.strip()]


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m vibe.benchmark.live",
        description="F-50 live feasibility spike: per-stage latency of the live pipeline.",
    )
    p.add_argument("clip", type=Path, help="a video with people in it (stays on this machine)")
    p.add_argument("--detectors", type=_list, default=["rfdetr-n", "rfdetr-s"],
                   help=f"comma list from {sorted(RFDETR_SIZES)} or stub")  # fmt: skip
    p.add_argument("--precisions", type=_list, default=["fp32", "fp16"])
    p.add_argument("--heights", type=_list, default=["720", "1080"])
    p.add_argument("--every", type=_list, default=["1"],
                   help="comma list: detect every k frames, reuse boxes in between")  # fmt: skip
    p.add_argument("--face-scales", type=_list, default=["1"],
                   help="comma list: run YuNet on the frame scaled by this (e.g. 0.5)")  # fmt: skip
    p.add_argument("--blurs", type=_list, default=list(BLUR_VARIANTS),
                   help=f"comma list from {list(BLUR_VARIANTS)}")  # fmt: skip
    p.add_argument("--frames", type=int, default=300, help="frames timed per configuration")
    p.add_argument("--warmup", type=int, default=30, help="frames run first and not counted")
    p.add_argument("--threshold", type=float, default=0.1, help="detector confidence floor")
    p.add_argument("--faces", choices=["yunet", "none"], default="yunet")
    p.add_argument("--identity", choices=["sface", "none"], default="sface")
    p.add_argument("--tracker", choices=sorted(TRACKERS), default="bytetrack")
    p.add_argument("--yunet", type=Path, default=None, help=f"default: models dir/{YUNET_FILE}")
    p.add_argument("--sface", type=Path, default=None, help=f"default: models dir/{SFACE_FILE}")
    p.add_argument("--out", type=Path, default=None,
                   help="results JSON (default: data dir/bench/live/<time>.json)")  # fmt: skip
    return p


def _print(result: dict) -> None:
    head = (f"{result['detector']} {result['precision']} {result['resolution']} "
            f"every={result['every']} face_scale={result['face_scale']}")  # fmt: skip
    print(f"\n== {head}  ({result['frames_timed']} frames, {result['warmup_frames']} warm-up)")
    for stage, s in result["stages_ms"].items():
        print(f"  {stage:<18} p50 {s['p50']:>8.2f}  p95 {s['p95']:>8.2f}  p99 {s['p99']:>8.2f} ms")
    every = result["every"]
    print(f"  end to end (decode excluded): per frame, and averaged over each {every} frame(s):")
    for variant, s in result["end_to_end_ms"].items():
        w = s["window_mean"]
        verdict = "fits 30 fps" if s["fits_30fps_p95"] else "over 33.3 ms"
        print(f"    {variant:<11} frame p50 {s['p50']:>7.2f} max {s['max']:>7.2f} | "
              f"avg p50 {w['p50']:>7.2f} p95 {w['p95']:>7.2f} p99 {w['p99']:>7.2f} ms "
              f"-> {verdict}")  # fmt: skip
    avg = result["per_frame_avg"]
    print(
        f"  {avg['people_blurred']} people blurred per frame, {avg['faces_per_detection']} faces "
        f"per detection; identity calls: {result['identity_calls']}; "
        f"VRAM peak: {result['vram_peak_mb']}"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.clip.is_file():
        print(f"error: no clip at {args.clip}", file=sys.stderr)
        return 2
    unknown = [d for d in args.detectors if d != "stub" and d not in RFDETR_SIZES]
    bad_prec = [p for p in args.precisions if p not in ("fp32", "fp16")]
    bad_blur = [b for b in args.blurs if b not in BLUR_VARIANTS]
    try:
        everys = [int(k) for k in args.every]
        scales = [float(s) for s in args.face_scales]
        heights = [int(h) for h in args.heights]
    except ValueError:
        everys, scales, heights = [], [], []
    numbers_ok = (everys and scales and heights and min(everys) >= 1
                  and all(0 < s <= 1 for s in scales) and min(heights) >= 16)  # fmt: skip
    if unknown or bad_prec or bad_blur or not numbers_ok or args.frames < 1 or args.warmup < 0:
        print(f"error: unknown detector(s) {unknown}, precision(s) {bad_prec} or blur(s) "
              f"{bad_blur}, or a bad number (every >= 1, 0 < face scale <= 1, frames >= 1)",
              file=sys.stderr)  # fmt: skip
        return 2
    args.yunet = args.yunet or config.models_dir() / YUNET_FILE
    args.sface = args.sface or config.models_dir() / SFACE_FILE

    configs = []
    for detector in args.detectors:
        for precision in args.precisions if detector != "stub" else ["n/a"]:
            for height in heights:
                for every in everys:
                    for scale in scales if args.faces != "none" else [1.0]:
                        configs.append((detector, precision, height, every, scale))

    report: dict[str, Any] = {
        "what": "F-50 live feasibility spike: per-stage latency (python -m vibe.benchmark.live)",
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "hardware": doctor.probe().as_dict(),
        "versions": _versions(),
        "clip": {"name": args.clip.name},
        "settings": {k: str(v) for k, v in vars(args).items() if k not in ("clip", "out")},
        "budget_ms": {"30fps": round(BUDGET_MS, 2), "60fps": round(BUDGET_60_MS, 2)},
        "notes": [
            "Each clip frame is scaled to fit the target height and centred on black (untimed).",
            "decode_source is timed at the clip's own size and excluded from end to end.",
            "Blur is worst case: every person and face is blurred.",
            "With every=k, detection runs on 1 frame in k; the frames between reuse the last "
            "boxes (no motion prediction). fits_30fps uses the p95 of k-frame averages, since "
            "the delay buffer lets a heavy frame borrow time from the light ones after it.",
            "YuNet and SFace run on the CPU (opencv-python has no CUDA).",
            "Speed only: no accuracy is measured.",
        ],
        "results": [],
    }
    with av.open(str(args.clip)) as c:
        v = c.streams.video[0]
        report["clip"].update(width=v.codec_context.width, height=v.codec_context.height,
                              fps=round(float(v.average_rate or 0), 2))  # fmt: skip

    for detector, precision, height, every, scale in configs:
        try:
            result = run_config(args, detector, precision, height, every, scale)
        except NotInstalled as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        report["results"].append(result)
        _print(result)

    out = args.out or config.data_dir() / "bench" / "live" / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    gpu = ", ".join(f"{g.name} {g.vram_gb} GB" for g in doctor.probe().gpus) or "no NVIDIA GPU"
    print(f"\nhardware: {gpu}; driver {report['hardware']['driver']}; "
          f"torch {report['versions']['torch']}\nresults: {out}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
