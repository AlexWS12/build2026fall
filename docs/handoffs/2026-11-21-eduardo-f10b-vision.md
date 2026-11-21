---
author: eduardo
date: 2026-11-21
branch: eduardo/f10b-vision
features: [F-10b, F-09b]
base: v0.4.0 + #40 @ 97dbb80
head: df928f4
status: in progress
---

# The Analyse button finds faces for real (YuNet on CPU), plus stitching, head boxes, a label helper and a CPU bake-off

## What changed
- **Vision is an F-08 analysis** (`modules/vision/analysis.py`, `VisionAnalysis`, registered as `vision`). `POST /api/projects/{id}/analysis` now runs the detectors in `VIBE_VISION_DETECTORS` (default `yunet:weights=@yunet`, faces only) through `run_analysis`.
  - `stub` still only replays the cache (503 if nothing is cached).
  - `local` runs the detectors here, and `remote` runs them on the model server (tested in-process only).
  - The result becomes the video's default track set.
  - A missing package or missing weights is a 503 that says what to install. A bad spec is a 503 that points to the server log.
- **After a model comparison** left several track sets and no default, Analyse runs (or replays) the app's own set and marks it as the default, instead of a 503.
- **Weights by name:** a detector option `weights=@yunet` resolves to the `models.json` entry's file. Every machine and the model server build the same key, and the key covers the pinned sha256. YuNet's sha256 is now pinned (the file is 232,589 bytes).
- **New optional extra** `vision = ["opencv-python-headless>=4.9"]` (shared file: `pyproject.toml` / `uv.lock`). Install it with `uv sync --extra vision`.
- **Hardening** (`modules/vision/stitch.py`):
  - `stitch` joins same-label fragments when the next one starts ≤ 1.5 s later where the last was heading.
  - `head_fallback` adds an `uncertain` face box on a tracked person's head wherever no face box covers it (fail closed).
  - Both enter the tracks key only when on (`--stitch` / `--heads` on `analyze`, `retrack` and `benchmark offline`).
  - The app stitches by default, and adds head boxes when a person detector is in the set.
- **Label helper (F-09b):**
  - `python -m vibe.benchmark label-frames CLIP_ID [--label face]` writes a self-contained `label.html` under `data/labels/<clip>/`: one frame a second plus the last, with the label's existing boxes to correct.
  - `import-labels CLIP_ID FILE` turns the saved JSON into `user` tracks and checks them against the manifest.
- **Bake-off runner:** `python -m vibe.benchmark offline --set SPECS [--every N] [--stitch] [--heads] [--out DIR]`.
  - It caches under the CLI keys, so `report` scores the runs.
  - It records wall time per stage, s/frame, peak memory and tracks per labelled target, with the hardware.
  - A detector that can't load is a `skipped` row with the reason.
- API docstring change → `openapi.json` and `frontend/src/api/generated/schema.ts` regenerated. No route arguments or responses changed. **No contract change.**

## How to check it
- Tests (no GPU): `npm test` → backend 1580+ passed, frontend 108 passed; `npm run lint` clean.
- Real faces in the app (tier 0 is enough):
  1. In `backend/`, run `uv sync --extra vision` and `uv run python -m vibe.modules download yunet`.
  2. Start the backend with `VIBE_BACKEND=local`.
  3. Open a project, click Analyse, then "blur all faces for the first half", then Export.
  - Checked on `pair-backpacks-behind` (the rehearsal clip): 118 face tracks, the request was applied, and the MP4 was blurred.
- Bake-off outputs (under `data/bench/`, gitignored), all from the commands above, on **Apple M2, 8 GB, CPU only. These are not GPU numbers**:
  - `2026-11-21-yunet-m2/offline.md`: YuNet at threshold 0.3 vs 0.6, every 1 vs 3, on all 9 clips. YuNet took 0.017–0.044 s/frame, with a peak of 383 MB. Track counts drop about 3–4× from 0.3 to 0.6, e.g. crowd-dark-coats every 1: 2551 → 700.
  - `2026-11-21-yunet-m2/stitch.md`: the same detections re-tracked with `--stitch` drop by about 30–70% (crowd-dark-coats 0.3/every 1: 2551 → 708; pair-backpacks-behind 118 → 59).
  - `2026-11-21-frcnn-yunet-m2/offline.md`: `tv-frcnn-mobile` (people) + YuNet 0.6, every 3, `--stitch --heads`. 0.27–0.37 s/frame, peak 1132 MB, all 9 clips ran. `benchmark report` (`2026-11-21-report-m2/report.md`, IoU ≥ 0.5, covered ≥ 90% after 0.15 padding) scores **people** on the 5 labelled clips: recall 80.7–100%, exposed 0–21.2% of labelled person boxes (pair-backpacks-behind 14/66 = 21.2%, trio-sidewalk-totes 3/27 = 11.1%, hikers-backpacks 5/57 = 8.8%), precision 21–97%. A person exposed means "blur people" would leave someone visible at that moment, so person masking is **not privacy-safe yet** on these settings. COCO also fires on many other labels (cars, handbags, traffic lights...); those don't affect face or person masks.
- **Faces, first labelled clip** (trio-street-outfits: 3 people, 39 face boxes drawn with `label-frames`, `benchmark report`, `data/bench/report-faces/`):
  - YuNet as it was: **39/39 exposed (100%)** at every threshold, stride and with or without stitching. It finds the faces (confidence about 0.92), but its boxes cover only eyes to mouth, so a mask leaves forehead, cheeks and chin visible.
  - New `grow` option on YuNet (scales each box around its centre): 1.5 → 25/39 exposed, 1.8 → 13/39, 2.2 → 2/39, **2.6 → 0/39** (threshold 0.3 or 0.6, every 1 or 3, stitched). Recall at IoU 0.5 drops to 5.1% at 2.6 because the boxes are much bigger than the label; for masking, exposure is what counts.
  - tv-frcnn-mobile + YuNet 0.6 (no grow) + head boxes, every 3: 1/39 exposed.
- **Faces, second labelled clip** (trio-sidewalk-totes: 3 people, 21 face boxes; two people have hair across the face):
  - YuNet grow 2.6 alone: **7/23 exposed** (every 1). Person 1 is exposed 5 of 8 times: YuNet doesn't detect their face for most of 0–4 s (0–7 of 12–23 frames around each label), so no box size helps.
  - tv-frcnn-mobile + YuNet grow 2.6 + head boxes as first written (centred 60% of the top 25%, skipped when any face covers half of it): still 7/23. The person box was about 0.37 of the frame wide (bag or neighbour), so the head guess missed, and a neighbour's face counted as "covered".
  - Head boxes now default to the **full width of the top 30%**, skipped only when a face box covers **90%** of it: **0/23** (every 1 and 3) here, and still **0/39** on trio-street-outfits (`vision retrack --stitch --heads` on the cached detections, then `benchmark report`).
  - So: YuNet alone can't protect faces it doesn't see (hair, turned heads). Only the person detector's head boxes cover those, at about 0.3 s/frame on the M2. Two clips and 60 face boxes only; the cost (how much more gets blurred) isn't measured.
- **Faces, third labelled clip** (crowd-orange, 4 s crowd: 16 people, 42 face boxes; I split one track the page had joined across two people, the original export is unchanged): **0/42 exposed** with YuNet grow 2.6 alone and with tv-frcnn-mobile + YuNet grow 2.6 + head boxes, every 1 and every 3.
- **All three labelled clips, current defaults** (`benchmark report`): YuNet grow 2.6 alone exposes 0/39, 7/23 and 0/42; with the person detector and head boxes, 0/39, 0/23 and 0/42. The only faces YuNet alone left visible were hidden by hair.

## Decisions made
- **D-022 (Proposed, in use), the app's vision default:** `tv-frcnn-mobile` + YuNet threshold 0.6 `grow=2.2`, every 3rd frame, stitched, with head boxes (`config.py`: `VIBE_VISION_DETECTORS`, new `VIBE_VISION_EVERY`). Eduardo chose it for now from the three-clip face scores, to revisit when the team weighs in or a GPU bake-off (RF-DETR + EgoBlur, D-015) has numbers. It needs PyTorch: without it, Analyse is a 503 that says to install it, or to set YuNet only.
- **"Blur people" stays in the demo with a stated limit:** up to 21% of labelled person boxes exposed (pair-backpacks-behind 14/66). Mention it as a known blocker when showing it.
- Head-box shape and YuNet threshold 0.6 / `grow=2.2` are defaults in code (`HeadSettings`, `DEFAULT_VISION_DETECTORS`), covered by D-022.

## Next steps
- **Over-masking, measured** (new `masked` / `masked outside` columns in `benchmark report`: share of the frame inside a padded mask at the scored moments, on a 200x200 grid). With the first D-022 default (YuNet threshold 0.3, grow 2.6), crowd-orange had 57.5% of the frame masked, and the export blurred nearly all of it. Settings that keep 0 faces exposed on all three clips (people + head boxes, every 3, stitched):

  | YuNet | trio-street-outfits | trio-sidewalk-totes | crowd-orange |
  |---|---|---|---|
  | threshold 0.3, grow 2.6 | 20.2% | 41.0% | 57.5% |
  | threshold 0.45, grow 2.6 | 19.4% | 34.0% | 50.4% |
  | threshold 0.6, grow 2.6 | 18.9% | 28.8% | 46.5% |
  | **threshold 0.6, grow 2.2 (new default)** | **17.4%** | **27.6%** | **42.4%** |
  | threshold 0.6, grow 1.8 | 16.4% | 26.1% | 39.5%, but 1/42 face exposed |

  On crowd-orange, YuNet's faces and the head boxes each mask about a third of the frame on their own. Smaller head boxes (width 0.8 or height 0.25) leak 1–4 of the 21 trio-sidewalk-totes faces, so they stay full width, top 30%. In the app, the new default's export blurs the heads and leaves bodies and the street visible; before, nearly the whole frame was blurred.
- **Masked share undercounts the export:** the renderer holds a lost track's last box over the whole range, which the metric doesn't see. A metric from the render's mask audit would be more honest.
- Team review of D-022 (Proposed): is about 0.3 s/frame acceptable on laptops, or should tier 0 default to YuNet only?
- People: exposure up to 21% with tv-frcnn-mobile at every 3. Try every 1 and a lower `min_conf` with `retrack` and `benchmark offline` before calling "blur people" safe.
- GPU bake-off of D-015's RF-DETR + EgoBlur with `benchmark offline`, on the same labelled clips.
- Plates: no clip has any; add licensed clips with plates before claiming anything for them.
- A background job for Analyse on long clips (today the request waits and the project is busy, 409).
- The renderer holds a lost track's box over the whole requested range (fail closed). With many fragments that piles up into large masked blocks. Stitching helps; the F-50 handoff suggests an OpenCV blur for speed.

## Gotchas
- **OpenCV and PyAV both bundle `libavdevice` on macOS.** Importing both prints `objc: Class AVFFrameReceiver is implemented in both ...`. Exports and analysis worked here, but it's a known source of rare crashes. Watch for it.
- **`uv sync` without `--extra vision` removes OpenCV,** and the Analyse button then answers 503 with the install hint.
- **A bake-off adds track sets to the shared cache.** Analyse handles that now, but `GET .../tracks` and exports answer 503 ("several cached") on that clip until Analyse is pressed or `python -m vibe.modules.vision default` is set.
- **An app-run analysis keeps only tracks, not raw detections,** so `retrack` works only on CLI or `benchmark offline` runs.
- **`npm run dev` uses `--kill-others-on-fail`:** when the backend's reloader died during the branch switch, Vite went down with it.
