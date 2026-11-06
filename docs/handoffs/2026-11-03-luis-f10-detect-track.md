---
author: luis
date: 2026-11-03
branch: luis/f10-detect-track
features: [F-10]
base: v0.1.0 @ b5e3c66
head: d949ab3
status: ready for review
---

# F-10 first cut: detect-and-track pipeline, stub, per-video cache and detector adapters for the model comparison

> **Ready for review: the pipeline, stub, cache and CLI, plus the review fixes and a first run of the real adapters (updated 2026-11-05).** The card isn't done: there are **no measured numbers yet**, only boxes checked by eye on two clips, because F-09 has no labeled clips (Next steps). The model comparison needs them.

> **No shared file changes.** New subpackage `backend/src/vibe/modules/vision/` and tests in `backend/tests/vision/`; a one-line "Built so far" note in ARCHITECTURE "AI modules"; my F-10 row in the roadmap. **No dependency changes:** model packages are installed by hand for now (see Gotchas), so `pyproject.toml` and `uv.lock` are untouched.

## What changed
- **`backend/src/vibe/modules/vision/`** (CLI: `uv run python -m vibe.modules.vision --help`):
  - `detection.py`: `Detection` (label, normalized box, conf) and the `Detector` interface: `detect(rgb_frame) -> list[Detection]`, plus `name` and `settings` for the cache key. One frame in, boxes out, so the same detectors serve live mode later. `normalize_box` clips to the frame and drops empty boxes once for every model (the contract rejects both).
  - `tracking.py`: `TrackerSettings` (min confidence, a lower one for `face`/`license_plate`, the `uncertain` cut-off, IoU to continue a track, max gap in seconds), `IouTracker` (greedy IoU + constant-velocity guess, no packages, deterministic; the baseline and what the tests use) and `ByteTracker` (Roboflow `trackers`, one per label). Tracks get one keyframe per detection; the renderer and F-09 interpolate between them.
  - `analyze.py`: `analyze_video(path, detectors, tracker, every=k)` reads every k-th frame with F-03's `VideoReader`, runs every detector, tracks, and returns the tracks **and the raw detections** with wall time per stage. `retrack()` rebuilds tracks from cached detections, so a tracker or threshold sweep needs no model and no GPU.
  - `cache.py`: writes `cache/<sha256>/detections/<key>.json` and `cache/<sha256>/tracks/<key>.json` (a list of contract `Track`s: the shape `python -m vibe.benchmark report` already scores, one column per key). Keys read like `rfdetr-l_egoblur-face-k1-3f9a1c2e-iou-7b0d4e11` and end in a hash of every setting. `load_tracks` / `project_tracks` give the toolbox its `ToolContext.tracks`; `default` marks which track set a video uses when several are cached.
  - `detectors/`: the candidates for the comparison, by name, with their license: `stub` (a colored block, for tests), `tv-frcnn` / `tv-frcnn-mobile` (torchvision, BSD), `rfdetr-n/s/m/l` (Apache 2.0), `egoblur-face` / `egoblur-plate` (Apache 2.0, gated weights), `yunet` (MIT), and **`yolo` / `yoloe` (Ultralytics, AGPL)**. Picked with a spec: `-d rfdetr-l`, `-d yolo:weights=yolo11m.pt`, `-d "yoloe:prompts=coffee mug|poster"`, `-d egoblur-face:weights=C:/models/face.jit`. Each one imports its package only when built and says how to install it if it's missing.
- **Tests, `backend/tests/vision/`:** 53 tests (33 in the first cut), no GPU, no network, no ffmpeg CLI: clips (moving colored squares) are encoded with PyAV's bundled ffmpeg, so they also run on Windows without ffmpeg.

## How to check it
```bash
cd backend
uv run pytest tests/vision -q                       # 53 passed
uv run ruff check && uv run ruff format --check
uv run python -m vibe.modules.vision detectors      # every detector with its license
uv run python -m vibe.modules.vision analyze CLIP.mp4 -d stub:color=red,label=person
```
Checked on Windows 11 (Python 3.12, no ffmpeg on PATH): on 2026-11-03 `tests/vision` 33 passed and the full suite 379 passed, 76 skipped (ffmpeg); on 2026-11-05, after the fixes below, `tests/vision` 53 passed and the full suite 474 passed, 1 skipped. Both times **1 failed: `tests/toolbox/test_toolbox_cli.py::test_writing_keeps_the_timeline_permissions`, which fails the same way on unchanged `main` on this machine** (not F-10). The tests use the stub and a stand-in for `supervision`/`trackers`: **no automated test covers the real model adapters**, which need the model packages and weights that CI doesn't have. They were run by hand, see "Step 2" below.

## Decisions made
luis made these on 2026-11-03 for F-10; say on the PR if you disagree. None is architecture-level.
- **Raw detections are cached too, not only tracks.** Tuning the tracker, the thresholds or the stride then costs seconds on a CPU instead of a GPU run.
- **Cached track ids carry the key's hash** (`trk_3f9a1c2e_3`). Ops name tracks by id, so if two analyses both had `trk_3`, re-running with another model would silently move a blur onto a different object. Now an edit made on one analysis names ids the other doesn't have: a missing target, not a wrong one.
- **No analysis means `None`, even with hand-drawn boxes**, and several cached analyses with no key chosen is an error (`AmbiguousAnalysis`), never a guess. Both keep "blur every face" from succeeding on the wrong data.
- **Privacy labels fail closed in the tracker:** `face` and `license_plate` are kept down to confidence 0.1 (others 0.3), weak keyframes are `uncertain` instead of dropped, and a detection ByteTrack doesn't confirm still gets its own track. These defaults are starting points to tune with F-09, not measured values.
- **Ultralytics stays out of the project** (D-015): no import unless `yolo`/`yoloe` is asked for, not in `pyproject.toml`, and the CLI warns when one is used. It's here only to measure what the permissive stack costs.

## Review fixes (PR #22, Edugre's 8 comments)
All eight addressed; `tests/vision` is now 53 passed (was 33).
- **`analyze` no longer copies the video** into the data folder: the cache is keyed by `cache.video_sha256(path)` (same digest as `Storage.add_video`), and `analyze`/`retrack`/`list` only read the file. Detectors and tracker are built first, so missing weights fail before any video is read.
- **Track ids can't collide across detector runs:** the tracks key's final hash now covers the whole detections key (it used to hash only the tracker and its settings, which is also the id prefix), and a long key that gets cut to 128 characters still ends in that hash.
- **A default track set:** `python -m vibe.modules.vision default VIDEO --key KEY` writes `cache/<sha>/default/tracks.json`. `load_tracks` with no key reads it; with none marked it still raises `AmbiguousAnalysis` (now pointing at that command), and a marker whose tracks were deleted is a `CacheError`, not `None` ("never analysed"). `list` shows `(default)`. The marker sits in its own folder so the benchmark never reads it as a track set.
- **ByteTrack glue:** a kept detection ByteTrack doesn't return is linked by overlap (same matching as `IouTracker`, now the shared `_Linker`) and that track ends once ByteTrack returns a box over the same object, instead of every unconfirmed frame starting a new track. Each update now carries its timestamp and `lost_track_buffer = max_gap * 30` (in `trackers` 2.7 the buffer is `lost_track_buffer / 30` seconds when timestamps are given), so `max_gap` means seconds at any `--every N`. `iou_match` configures only the fallback; ByteTrack's own thresholds stay at the library's defaults.
- **Detector options are typed by the parameter they set**, not guessed from the text: `yoloe:prompts=1984` stays a string (it used to be parsed as an int and crash), `threshold=1` becomes `1.0`, unknown or unparsable options name the option in the error.
- **Ultralytics boxes** go through `normalize_box` like every other adapter, so a box past the edge is clipped and an empty one dropped.
- **The ByteTracker unit tests** use a stand-in for `supervision`/`trackers`, which checks our glue and nothing about ByteTrack itself. The real library was run separately (Step 2).

## Step 2: the real adapters, run on two real clips (2026-11-05)
**Hardware and versions:** RTX 4050 Laptop (6 GB), driver 555.97, Windows 11, Python 3.12, `torch 2.6.0+cu124`, `rfdetr 1.11.2`, `trackers 2.7.0`, `supervision 0.31.5`, `opencv-python 5.0.0.93`. EgoBlur Gen1 weights (Meta, Apache 2.0) and YuNet `face_detection_yunet_2023mar.onnx` (OpenCV Zoo, MIT; SHA-256 checked) are in `C:\Users\majit\models\`, **outside the repo**.

**Clips** (free-license stock video from Pexels, kept outside the repo; not our F-09 clips): a pedestrian crowd (1080x1920, 60 fps, 12 s, stride 6 = 122 sampled frames) and a street with cars and three readable plates (1080x1920, 30 fps, 23 s, stride 3 = 230 sampled frames).

| Adapter | Ran on | Time per sampled frame, model only | Needed a fix? |
|---|---|---|---|
| `tv-frcnn-mobile` | GPU | 39.0–44.2 ms | no |
| `rfdetr-s` | GPU | 50.8–70.3 ms | yes: import moved to `rfdetr.assets`; device now reported |
| `yunet` | CPU (OpenCV 5 dnn) | 60.2–72.8 ms | no |
| `egoblur-face` / `egoblur-plate` (Gen1) | GPU | 481–552 ms | yes: load on CPU, then `.to(cuda)` |
| `bytetrack` (real `trackers`) | CPU, from cached detections | n/a | yes: timestamps + `lost_track_buffer` (see above) |

These are wall times from one run each, for orientation, **not benchmark numbers**. EgoBlur is about ten times slower than the rest: offline only on this card, as D-015 already assumed (YuNet for live).

**What I checked, by eye only:** boxes drawn over 3 sampled frames per clip. They land on what they should in every adapter (so the RGB/BGR handling and the normalized coordinates on vertical video are right). EgoBlur boxes all three legible plates tightly and the foreground faces, including one with a mask. YuNet and EgoBlur also box many tiny, distant faces, most of them `uncertain`: I can't tell from the image how many are real faces. RF-DETR has fewer duplicate boxes than `tv-frcnn-mobile`, and reaches further into the crowd.

**What this does not show:** which model is better. There are no labels, so no recall, precision or fragmentation. Track counts differ a lot (crowd clip: 478 tracks with `tv-frcnn-mobile`, 336 with `rfdetr-s`, 1482 face tracks with YuNet, 839 with EgoBlur) but count is not quality: it mixes fragmentation, false positives and the real number of people.

**ByteTrack vs the IoU tracker, on the same cached detections** (street clip): `rfdetr-s` 1194 vs 1095 tracks, `tv-frcnn-mobile` 2075 vs 1416. ByteTrack makes more tracks, probably because it only starts a track from a detection of at least 0.7 confidence and after two frames, so an object is first covered by our overlap fallback and then by a new ByteTrack track. That needs stitching (Next steps 8), and it is a reason not to read ByteTrack as better or worse yet.

**Not run, on purpose or for lack of need:** `yolo` / `yoloe` (Ultralytics, AGPL): **never executed**, luis decided not to test them, so D-015 will not have a measured cost for staying permissive. `rfdetr-n/m/l`, full `tv-frcnn` and EgoBlur Gen2 share code with what ran. HiPerGator: an L4 (23 GB) Console session started and the branch cloned there, but nothing was run on it yet.

## Next steps (the rest of the F-10 plan)
1. **Review and merge this PR**, and post the YOLO proposal in the team chat: benchmarking `yolo`/`yoloe` in isolation, with any license change a team decision (D-015). (Update 2026-11-05: I won't run YOLO; say so if you think the decision needs it.)
2. **Coordinate:** Eduardo (F-08): fit `vision` under the stub/local/remote interface when it lands; nothing in `modules/` exists on `main` and there's no `eduardo/compute-setup` branch on GitHub yet. Jean (F-09): `clips.json` is still empty and there are no labels; offer to film and label 1–2 clips (faces incl. small ones, a plate, a crowd, handheld, low light). Whoever writes the plan executor (F-06 / F-64): build `ToolContext(tracks=project_tracks(storage, project, key))`.
3. ~~Set up the GPU~~ **Done** (Step 2). The driver update wasn't needed: an Ada card (4050) runs the `cu124` wheels on driver 555.97. The `cu128` wheels and a 570+ driver are for Blackwell (the 5070). `rfdetr` pulls in `supervision` and `trackers`; YuNet needs `opencv-python`.
4. ~~Smoke-test each adapter~~ **Done** for torchvision, RF-DETR, EgoBlur Gen1, YuNet and ByteTrack; the Ultralytics path is still unrun. Left to check: whether Gen2 weights change EgoBlur's output.
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
- **The CLI's cache key doesn't cover adapter code or library versions.** Re-running after changing an adapter (or upgrading `rfdetr`/`trackers`) writes the same key and overwrites the old result silently. Delete the old key, or put library versions in the key before the benchmark.
- **`uv run` can wipe PyTorch.** A plain `uv sync` or `uv run` removes anything not in the lock file (torch, rfdetr...). Use `uv run --no-sync` and `uv sync --inexact` after installing model packages.
- **The repo sits inside OneDrive on this machine**, and `.venv` came back empty once and `uv sync` hit "Access is denied" writing an `.exe`; rebuilding it fixed it. Not a project problem, but keep the venv out of OneDrive if you can.
- **OpenCV Zoo weights are Git LFS files.** The GitHub API and `raw.githubusercontent.com` give a 131-byte text pointer, not the model. Download from `github.com/opencv/opencv_zoo/raw/main/...` and check the size and SHA-256 (YuNet 2023mar: 232,589 bytes).
- **EgoBlur's TorchScript needs torchvision imported before loading and must be loaded on the CPU first** (`map_location=cpu`, then `.to(cuda)`), or the first frame fails with "tensors on cuda:0 and cpu".
- **Labels must match exactly:** `face`, `license_plate`, and COCO names as the model gives them (`cell phone`, with a space). F-09 and `find_objects` compare labels exactly.
- **Times printed by the CLI are wall time for orientation, not benchmark numbers.** Quote only the F-09 / F-50 scripts.
