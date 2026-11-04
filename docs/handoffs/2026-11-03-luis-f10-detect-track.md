---
author: luis
date: 2026-11-03
branch: luis/f10-detect-track
features: [F-10]
base: v0.1.0 @ b5e3c66
head: 7de88a9
status: ready for review
---

# F-10 first cut: detect-and-track pipeline, stub, per-video cache and detector adapters for the model comparison

> **Ready for review: the pipeline, stub, cache and CLI.** The card isn't done: the real model adapters haven't been run yet, and the model comparison needs F-09's clips (Next steps).

> **No shared file changes.** New subpackage `backend/src/vibe/modules/vision/` and tests in `backend/tests/vision/`; a one-line "Built so far" note in ARCHITECTURE "AI modules"; my F-10 row in the roadmap. **No dependency changes:** model packages are installed by hand for now (see Gotchas), so `pyproject.toml` and `uv.lock` are untouched.

## What changed
- **`backend/src/vibe/modules/vision/`** (CLI: `uv run python -m vibe.modules.vision --help`):
  - `detection.py`: `Detection` (label, normalized box, conf) and the `Detector` interface: `detect(rgb_frame) -> list[Detection]`, plus `name` and `settings` for the cache key. One frame in, boxes out, so the same detectors serve live mode later. `normalize_box` clips to the frame and drops empty boxes once for every model (the contract rejects both).
  - `tracking.py`: `TrackerSettings` (min confidence, a lower one for `face`/`license_plate`, the `uncertain` cut-off, IoU to continue a track, max gap in seconds), `IouTracker` (greedy IoU + constant-velocity guess, no packages, deterministic; the baseline and what the tests use) and `ByteTracker` (Roboflow `trackers`, one per label). Tracks get one keyframe per detection; the renderer and F-09 interpolate between them.
  - `analyze.py`: `analyze_video(path, detectors, tracker, every=k)` reads every k-th frame with F-03's `VideoReader`, runs every detector, tracks, and returns the tracks **and the raw detections** with wall time per stage. `retrack()` rebuilds tracks from cached detections, so a tracker or threshold sweep needs no model and no GPU.
  - `cache.py`: writes `cache/<sha256>/detections/<key>.json` and `cache/<sha256>/tracks/<key>.json` (a list of contract `Track`s: the shape `python -m vibe.benchmark report` already scores, one column per key). Keys read like `rfdetr-l_egoblur-face-k1-3f9a1c2e-iou-7b0d4e11` and end in a hash of every setting. `load_tracks` / `project_tracks` give the toolbox its `ToolContext.tracks`.
  - `detectors/`: the candidates for the comparison, by name, with their license: `stub` (a colored block, for tests), `tv-frcnn` / `tv-frcnn-mobile` (torchvision, BSD), `rfdetr-n/s/m/l` (Apache 2.0), `egoblur-face` / `egoblur-plate` (Apache 2.0, gated weights), `yunet` (MIT), and **`yolo` / `yoloe` (Ultralytics, AGPL)**. Picked with a spec: `-d rfdetr-l`, `-d yolo:weights=yolo11m.pt`, `-d "yoloe:prompts=coffee mug|poster"`, `-d egoblur-face:weights=C:/models/face.jit`. Each one imports its package only when built and says how to install it if it's missing.
- **Tests, `backend/tests/vision/`:** 33 tests, no GPU, no network, no ffmpeg CLI: clips (moving colored squares) are encoded with PyAV's bundled ffmpeg, so they also run on Windows without ffmpeg.

## How to check it
```bash
cd backend
uv run pytest tests/vision -q                       # 33 passed
uv run ruff check && uv run ruff format --check
uv run python -m vibe.modules.vision detectors      # every detector with its license
uv run python -m vibe.modules.vision analyze CLIP.mp4 -d stub:color=red,label=person
```
Checked on 2026-11-03 on Windows 11 (Python 3.12, no ffmpeg on PATH, CPU only): `tests/vision` 33 passed; full suite 379 passed, 76 skipped (ffmpeg), **1 failed: `tests/toolbox/test_toolbox_cli.py::test_writing_keeps_the_timeline_permissions`, which fails the same way on unchanged `main` on this machine** (not F-10). The CLI ran end to end on a generated clip with the stub. **Not run anywhere yet: every real model adapter (torchvision, RF-DETR, EgoBlur, YuNet, Ultralytics) and `ByteTracker`.** No torch or model package is installed here. They're written against each library's documented API, and each file says what to check first.

## Decisions made
luis made these on 2026-11-03 for F-10; say on the PR if you disagree. None is architecture-level.
- **Raw detections are cached too, not only tracks.** Tuning the tracker, the thresholds or the stride then costs seconds on a CPU instead of a GPU run.
- **Cached track ids carry the key's hash** (`trk_3f9a1c2e_3`). Ops name tracks by id, so if two analyses both had `trk_3`, re-running with another model would silently move a blur onto a different object. Now an edit made on one analysis names ids the other doesn't have: a missing target, not a wrong one.
- **No analysis means `None`, even with hand-drawn boxes**, and several cached analyses with no key chosen is an error (`AmbiguousAnalysis`), never a guess. Both keep "blur every face" from succeeding on the wrong data.
- **Privacy labels fail closed in the tracker:** `face` and `license_plate` are kept down to confidence 0.1 (others 0.3), weak keyframes are `uncertain` instead of dropped, and a detection ByteTrack doesn't confirm still gets its own track. These defaults are starting points to tune with F-09, not measured values.
- **Ultralytics stays out of the project** (D-015): no import unless `yolo`/`yoloe` is asked for, not in `pyproject.toml`, and the CLI warns when one is used. It's here only to measure what the permissive stack costs.

## Next steps (the rest of the F-10 plan)
1. **Review and merge this PR**, and post the YOLO proposal in the team chat: benchmarking `yolo`/`yoloe` in isolation, with any license change a team decision (D-015).
2. **Coordinate:** Eduardo (F-08): fit `vision` under the stub/local/remote interface when it lands; nothing in `modules/` exists on `main` and there's no `eduardo/compute-setup` branch on GitHub yet. Jean (F-09): `clips.json` is still empty and there are no labels; offer to film and label 1–2 clips (faces incl. small ones, a plate, a crowd, handheld, low light). Whoever writes the plan executor (F-06 / F-64): build `ToolContext(tracks=project_tracks(storage, project, key))`.
3. **Set up the GPU (RTX 4050 Laptop, 6 GB):** update the NVIDIA driver (555.97 only supports up to CUDA 12.5; the `cu128` wheels need 570+), then `uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128`, and per candidate `rfdetr`, `opencv-python`, `trackers supervision`, `ultralytics`. Download EgoBlur (Meta, gated) and YuNet (OpenCV Zoo) weights outside the repo.
4. **Smoke-test each adapter** on a few real frames and fix what the real APIs do differently: RF-DETR class ids → names, EgoBlur Gen2 channel order and output, YOLOE `set_classes`, `trackers` ByteTrack's API.
5. **Agree the decision rule with the team before running**, e.g. "for faces and plates, the fastest stack with exposure ≥ X% on the team clips; ties go to fewer fragments, then VRAM".
6. **Bake-off, one factor at a time, same tracker for every detector:** detectors (tv-frcnn, rfdetr-n/s/l, yolo, yolox if added) × faces/plates (egoblur, yunet) × trackers (`retrack`: iou, bytetrack) × stride (1, 2, 5). Score with `python -m vibe.benchmark report`; per-slice results; numbers only from scripts, hardware stated (mine: RTX 4050 Laptop; the 5070 for reference numbers).
7. **Decide:** a decisions-log row (confirm D-015 with numbers, or bring a YOLO gap to the team).
8. **Sprint 4, harden the winner:** track stitching, face ↔ person linking, a head-region fallback for person tracks without a face, choose the stride per label, add the model packages to `pyproject.toml` as an optional group (shared file: heads-up first), fit into F-08's backends.
9. **Named objects:** COCO synonyms first ("phone" → `cell phone`), then OWLv2 on keyframes, or YOLOE if the team accepts AGPL. Cache under a key that includes the query.

## Gotchas
- **If `git` fails on Windows with a missing `libpcre2-8-0.dll`**, the Git for Windows install is damaged: `winget upgrade --id Git.Git` fixed it here.
- **Color order:** our frames are RGB. Ultralytics reads a NumPy array as BGR, OpenCV (YuNet) wants BGR, and EgoBlur's own script feeds BGR. The adapters pass PIL images or flip explicitly; check it on a real image before trusting any number.
- **Fast motion plus a big stride fragments tracks:** with `every=3` on the test clip (a 12-px square moving 12 px between samples), boxes stop overlapping and every sample started a new track. The IoU tracker needs the first two sightings to overlap before velocity helps. Watch "fragments per object" when choosing the stride.
- **Model packages aren't in `pyproject.toml` yet.** `uv run` keeps them, but `uv sync` removes anything not in the lock file; reinstall after a sync, or use `uv sync --inexact`.
- **Labels must match exactly:** `face`, `license_plate`, and COCO names as the model gives them (`cell phone`, with a space). F-09 and `find_objects` compare labels exactly.
- **Times printed by the CLI are wall time for orientation, not benchmark numbers.** Quote only the F-09 / F-50 scripts.
