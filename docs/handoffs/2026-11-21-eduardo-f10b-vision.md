---
author: eduardo
date: 2026-11-21
branch: eduardo/f10b-vision
features: [F-10b, F-09b, F-09]
base: v0.4.0 + #40 @ 97dbb80
head: ccb999d
status: ready for review
---

# The Analyse button detects faces and people for real (CPU is enough), with face labels, a bake-off and a decision (D-022)

> **Shared files, heads-up to post in the team chat:**
> - **API:** the `/analysis` route's behaviour changed (same response).
> - **Settings and dependencies:** `config.py` (`VIBE_VISION_DETECTORS`, `VIBE_VISION_EVERY`), the new optional `vision` extra in `pyproject.toml` / `uv.lock`, and the YuNet hash in `models.json`.
> - **Generated files:** `openapi.json` and `frontend/src/api/generated/schema.ts` are regenerated.
> - **Benchmark (Jean's F-09):** two masked-area columns in `score.py` / `report.py`.
> - **No contract or toolbox change.**
> - **After merging, everyone runs** `uv sync --extra cpu --extra vision` (or `cu128`) **and** `uv run python -m vibe.modules download yunet`. PR #44.

## What changed
- **Analyse runs detection** (`modules/vision/analysis.py`, `VisionAnalysis`, registered as an F-08 analysis named `vision`). `POST /api/projects/{id}/analysis` runs `VIBE_VISION_DETECTORS` on every `VIBE_VISION_EVERY`-th frame through `run_analysis`.
  - `stub` only replays the cache. `local` runs the detectors here, and `remote` runs them on the model server (tested in-process only).
  - The result becomes the video's default track set. If a comparison left several sets and none is the default, Analyse runs (or replays) the app's own set.
  - A missing package or missing weights is a 503 that says what to install. A bad setting is a 503 that points to the server log.
- **Default (D-022, Proposed, in use):** `tv-frcnn-mobile` (people) + `yunet:weights=@yunet,threshold=0.6,grow=2.2` (faces), every 3rd frame, IoU tracker, fragments stitched, plus head boxes.
- **YuNet `grow` option:** YuNet's own boxes cover only eyes to mouth, so `grow` scales them around their centre. It's only in the cache key when used.
- **Weights by name:** `weights=@yunet` resolves through `models.json`. YuNet's sha256 is pinned (the file is 232,589 bytes).
- **Hardening** (`modules/vision/stitch.py`):
  - `stitch` joins same-label fragments that start ≤ 1.5 s later, where the last one was heading.
  - `head_fallback` adds an `uncertain` face box over the full width of the top 30% of a person box, unless a face box covers 90% of it.
  - Both are in the tracks key only when on (`--stitch` / `--heads` on `analyze`, `retrack` and `benchmark offline`).
- **Benchmark tools:**
  - `benchmark label-frames CLIP` / `import-labels CLIP FILE`: a self-contained page for drawing ground-truth boxes (F-09b).
  - `benchmark offline --set SPECS [--every N] [--stitch] [--heads]`: timed detector runs, cached so `report` scores them, with the hardware stated. A detector that can't load is a `skipped` row with the reason.
  - `benchmark report`: new `masked` / `masked outside` columns, the share of the frame inside padded masks at the scored moments (200×200 grid).
- **Face labels** for trio-street-outfits (3 people, 39 boxes), trio-sidewalk-totes (3, 21) and crowd-orange (16, 42), drawn by Eduardo. In crowd-orange, one track the page had joined across two people is split into tracks 6 and 16.

## How to check it
- `npm test`: backend 1630 passed, frontend 108 passed. `npm run lint` and `python -m vibe.api --check` are clean. Run on macOS, 2026-11-21.
- In the app:
  1. In `backend/`, run `uv sync --extra cpu --extra vision` and `uv run python -m vibe.modules download yunet`.
  2. Start the backend with `VIBE_BACKEND=local`.
  3. Open a project, click Analyse, type "blur all faces", then Export.
  - torchvision downloads its weights on the first run.
  - Checked on crowd-orange: Analyse took about 14 s for 4 s of video (wall time, not a benchmark), and 52 faces were blurred. The export blurs heads and leaves bodies and the street visible.
- Numbers come from `benchmark offline` and `benchmark report` on **Apple M2, 8 GB, CPU only (not GPU numbers)**. The outputs are under `data/bench/` (gitignored).

## Results
- **Faces exposed** (IoU ≥ 0.5, ≥ 90% covered after 0.15 padding):

  | setting | trio-street-outfits | trio-sidewalk-totes | crowd-orange |
  |---|---|---|---|
  | YuNet as it was (no grow) | 39/39 | 21/23 | – |
  | YuNet grow 2.6 alone, every 1 / every 3 | 0/39 | 7/23 / 8/23 | 0/42 |
  | **default (people + YuNet t0.6 g2.2 + head boxes, every 3)** | **0/39** | **0/23** | **0/42** |

  YuNet doesn't detect one person's face on trio-sidewalk-totes for most of 0–4 s (hair), so only the head boxes cover it. Narrower or shorter head boxes leak 1–4 of those 21 faces.
- **Masked share** (0 faces exposed on all three unless noted):

  | YuNet, with people + head boxes, every 3 | trio-street-outfits | trio-sidewalk-totes | crowd-orange |
  |---|---|---|---|
  | threshold 0.3, grow 2.6 (first default) | 20.2% | 41.0% | 57.5% |
  | threshold 0.6, grow 2.6 | 18.9% | 28.8% | 46.5% |
  | **threshold 0.6, grow 2.2 (default)** | **17.4%** | **27.6%** | **42.4%** |
  | threshold 0.6, grow 1.8 | 16.4% | 26.1% | 39.5%, 1/42 exposed |
- **People** (`tv-frcnn-mobile` + YuNet 0.6, every 3, stitched; 5 person-labelled clips): recall 80.7–100%, **exposed 0–21.2%**. The worst is pair-backpacks-behind at 14/66. "Blur people" isn't privacy-safe yet.
- **Speed:** YuNet alone runs at 0.017–0.044 s/frame (peak 383 MB). The default runs at about 0.3 s/frame (peak about 1.1 GB).
- **Stitching** cuts YuNet track counts by about 30–70% (`2026-11-21-yunet-m2/stitch.md`).

## Decisions made
- **D-022 (Proposed, in use)** in `docs/ARCHITECTURE.md`: the default above. Eduardo chose it for now from these scores. Revisit when the team weighs in, or when a GPU bake-off of D-015's RF-DETR + EgoBlur has numbers.
- **"Blur people" stays in the demo** with its limit stated (up to 21% of labelled people exposed). Mention it as a known blocker when showing it.

## Next steps
- **Team review of D-022.** Is about 0.3 s/frame acceptable on laptops? Or should tier 0 default to faces only (`VIBE_VISION_DETECTORS="yunet:weights=@yunet,grow=2.6"`), which misses hidden faces?
- **People:** try every 1 and a lower `min_conf` (`retrack`, `benchmark offline`) before calling "blur people" safe.
- **Masked share undercounts the export.** The renderer holds a lost track's last box over the whole range, so crowds still show large blurred blocks. A metric from the render's mask audit would show it.
- **GPU bake-off** of RF-DETR + EgoBlur with `benchmark offline`, on the same labelled clips.
- **Plates:** no clip has any. Add licensed clips with plates before claiming anything for them.
- **Background job for Analyse** on long clips. Today the request waits and the project is busy (409).

## Gotchas
- **OpenCV and PyAV both bundle `libavdevice` on macOS.** Importing both prints `objc: Class AVFFrameReceiver is implemented in both ...`. It worked here, but it's a known source of rare crashes.
- **A plain `uv sync` removes PyTorch and OpenCV.** Analyse then answers 503 with the install hint. Use `uv sync --extra cpu --extra vision`.
- **`benchmark offline` and the CLI add track sets to the shared cache.** Analyse handles that, but `GET .../tracks` and exports answer 503 ("several cached") on that clip until Analyse is pressed or `python -m vibe.modules.vision default` is set.
- **Changing the default doesn't re-analyse a video that already has a default set.** Delete `cache/<sha256>/default/tracks.json`, then Analyse. Undo any blur made on the old tracks first: masks name track ids, and missing ids fail closed (whole frame).
- **An app-run analysis keeps only tracks, not raw detections,** so `retrack` works only on CLI or `benchmark offline` runs.
- **Tests must not build `tv-frcnn-mobile`:** where PyTorch is installed, that downloads weights. The API tests pin YuNet only.
- **`npm run dev` uses `--kill-others-on-fail`:** when the backend's reloader died during a branch switch, Vite went down with it.
