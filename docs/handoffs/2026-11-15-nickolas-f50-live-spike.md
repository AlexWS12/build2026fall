---
author: nickolas
date: 2026-11-15
branch: nickolas/f50-live-spike
features: [F-50]
base: untagged @ 1642a7d, rebased on main @ 73394e3
head: 78859d6
status: ready for review
---

# F-50: live mode fits 30 fps at 720p on the RTX 5070 if detection runs every 2–3 frames

## What changed
- **`backend/src/vibe/benchmark/live.py`** (new), run as `uv run python -m vibe.benchmark.live CLIP`:
  - Loops a clip and times each per-frame stage: decode, object detection (RF-DETR N/S/M, FP32/FP16), face detection (YuNet), tracking (ByteTrack or IoU), identity (SFace), and four blur variants: the renderer's numpy blur, numpy at ½ and ¼ size, and OpenCV.
  - Reports p50/p95/p99/max per stage and end to end against the 30 fps budget (33.3 ms), plus VRAM peak, people and faces per frame, hardware (`doctor.probe()`) and library versions. Writes JSON to `data/bench/live/<UTC>.json`, which is not committed.
  - `--every K` detects on 1 frame in K and re-blurs the held boxes in between. Its verdict uses the mean over each K-frame window, because the delay buffer absorbs single spikes.
  - `--face-scales` runs YuNet on a downscaled frame. The resize is timed, and boxes are mapped back to full size.
  - SFace embeddings stay in memory and are never written (D-017). Nothing on the per-frame path calls the network.
- **`backend/tests/benchmark/test_bench_live.py`** (new): five tests on the stub detector with a PyAV-made clip. They need no GPU, models or OpenCV, and check plumbing and report shape, not speed.

## Results
**Hardware:** RTX 5070, 11.9 GB, driver 621.01, Windows 11, AMD CPU (family 25 model 97).
**Software:** torch 2.11.0+cu128, OpenCV 5.0.0, trackers 2.7.0.
**Clip:** one person, 576x1024 portrait, upscaled and centred on a 16:9 canvas. Not committed.
**Run:** 300 timed frames plus 30 warm-up per setting. All numbers come from the script.

**Run 1** (`20261115T172924Z.json`), detecting every frame. No setting fits 33.3 ms:

| Stage | 720p | 1080p |
|---|---|---|
| Renderer numpy blur, p50 | 155–253 ms | 546–638 ms |
| OpenCV blur, p50 | 6–9 ms | 22–26 ms |
| YuNet (CPU), p50 | 16–25 ms | 50–54 ms |
| RF-DETR, p50 | 14–23 ms | 22–26 ms |

- FP16 was not faster than FP32, and RF-DETR S was barely slower than N.
- Tracking took about 1 ms. SFace took 5–6 ms per call. VRAM stayed under 0.4 GB.

**Run 2** (`20261115T174920Z.json`): RF-DETR N, FP32. Each cell is the p95 of the K-frame window mean for the OpenCV / numpy@¼ blur:

| Setting | YuNet full size | YuNet ½ size |
|---|---|---|
| 720p, every 1 | 49.8 / 48.8 ❌ | 35.7 / 33.7 ❌ |
| 720p, every 2 | 27.4 / 26.3 ✅ | 21.3 / 20.0 ✅ |
| 720p, every 3 | 21.9 / 20.3 ✅ | 17.0 / 15.8 ✅ |
| 1080p, every 1 | 99.9 / 105.8 ❌ | 52.2 / 57.4 ❌ |
| 1080p, every 2 | 66.0 / 70.3 ❌ | 33.75 / 38.6 ❌ |
| 1080p, every 3 | 49.4 / 54.0 ❌ | 27.8 / 32.1 ✅ |

- At ½ size, YuNet takes 4.5 ms at 720p and about 9 ms at 1080p.
- SFace takes about 3.5 ms per call. Peak VRAM was 556 MB reserved.
- The slowest single frame was 232 ms, in the 1080p every-2 full-size setting. That is well inside a 0.5–1 s delay buffer.

**Reading:** live at 720p and 30 fps looks feasible on the 5070. 1080p only just fits, and only with every=3 and ½-size faces. The team makes the go/no-go call at Sprint 4 close.

## How to check it
```
uv sync --directory backend --extra cu128   # a plain `uv sync` removes the model packages
uv pip install --directory backend rfdetr opencv-python trackers   # not in pyproject (see Gotchas)
uv run --directory backend pytest tests/benchmark/test_bench_live.py
uv run --directory backend python -m vibe.benchmark.live CLIP.mp4 --detectors rfdetr-n --precisions fp32 --every 1,2,3 --face-scales 1,0.5 --blurs opencv,numpy@0.25
```
YuNet and SFace weights are expected in the models directory, with names and URLs as in `models.json`. Without a GPU, use `--detectors stub --faces none --identity none --tracker iou`.

## Decisions made
- None at the architecture level. D-015 stays Proposed. These numbers support it, with RF-DETR N rather than S.

## Next steps
- **Accuracy is not measured, only speed.** The full-size YuNet reported 1.66 faces per frame at 1080p, against 1.03 at ½ size. Either the full-size run has false positives, or the ½-size run misses small faces. For privacy, a miss is a leak. Re-run on Eduardo's F-09b clips (now on main), which include small groups and labels.
- Boxes between detections are held, not predicted. Add motion prediction (Kalman / ByteTrack predict) before live uses every-k.
- **Renderer blur (F-04, Paul A.):** the numpy blur is far too slow for live. OpenCV or blur at ¼ size gives 6–9 ms at 720p. This needs a heads-up first.
- **`doctor` (F-08, Eduardo):** a 12 GB RTX 5070 reports 11.9 GB, so `TIER2_MIN_VRAM_GB = 12.0` places it in tier 1.
- Optional: TensorRT for RF-DETR, a re-run plugged in with other apps closed, and a multi-person clip.

## Gotchas
- CPU stages (YuNet, blur) varied by up to about 1.5× between configurations in the same run (other load on the machine). Treat single-digit-ms differences as noise.
- `rfdetr`, `opencv-python` and `trackers` are installed with `uv pip install` and are not in `pyproject.toml`, which is a shared file. A plain `uv sync` removes them.
- YuNet and SFace sha256 hashes are not pinned in `models.json` yet, because it's a shared file. yunet `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`, sface `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`.
- Run Python with `-u` when piping output, or progress stays buffered.
