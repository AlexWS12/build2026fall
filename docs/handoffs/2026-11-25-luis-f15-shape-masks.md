---
author: luis
date: 2026-11-25
branch: luis/f15-shapes
features: [F-15]
base: untagged @ 29df1e2
head: see the PR
status: in progress (spike; the team decides the contract question below)
---

> **Shared files changed:**
> - New API routes `GET` and `POST /api/projects/{id}/shapes` (`openapi.json` and the generated UI types are regenerated).
> - A new setting, `VIBE_SEGMENTER`, in `config.py`.
> - A new cache kind, `masks`.
>
> **No contract change and no dependency change:** SAM 2.1 runs through `transformers`, installed by hand like the detectors' packages. Post a team heads-up before merging. #44 changes `Player.tsx` too, so expect a conflict there.

# F-15 spike: a masked person is hidden by their shape, not their padded box, in the export and the preview

## What changed
- **Masks from SAM 2.1** (Apache 2.0, D-015):
  - `modules/vision/segmenters/` has `sam2` (default `facebook/sam2.1-hiera-tiny`) and a `stub` (an ellipse in the box, no model) behind one `Segmenter` interface.
  - Each mask is prompted with the track's own keyframe box. It's computed **only for the tracks the timeline masks**, at their keyframes in the masked spans plus one on each side.
  - Masks are cached as COCO RLE under `cache/<sha256>/masks/` (`modules/vision/masks.py`, `rle.py`).
  - The full write-up, with the Mask R-CNN vs SAM 2.1 comparison and the numbers, is in `docs/research/f15-segmentation-masks.md`.
- **The renderer** (`render/shape.py`, `frame.py`, `export.py`):
  - It moves the neighbouring keyframes' masks onto the interpolated box, grows them by half the padding, and applies the effect only inside.
  - **Fails closed:** it uses the padded box whenever anything is doubtful. That covers: no mask, an `uncertain` or held box, a gap over 1 s, a mask that doesn't fit its box, or a decode error.
- **Exports use the shapes when `VIBE_SEGMENTER` is set** (e.g. `sam2`):
  - If segmenting can't run (bad spec, missing package, GPU out of memory), the export uses boxes and says so in `issues` ("shapes unavailable: …"). Nobody is left unmasked.
  - The segmenter model is built once per server.
- **The preview shows the shapes** (`GET/POST /shapes`, `modules/vision/preview_shapes.py`, `Player.tsx`):
  - Each cached keyframe mask is sent as a small PNG, cropped to the same padded box the preview draws, with the shape as alpha.
  - The browser clips its blur to it with CSS `mask-image`.
  - After a request, undo, redo or opening a project, the UI fetches the cached shapes, then asks the server to make any missing ones, without holding the project.
  - Without a shape, a mask is still its box.
- **Render CLI:** `python -m vibe.render export … --segmenter sam2|stub`.

## How to check it
```bash
cd backend && uv run pytest -q     # 1675 passed, 2 skipped, 1 failed: test_writing_keeps_the_timeline_permissions (POSIX chmod; fails on main on Windows too)
cd frontend && npm test            # 112 passed
npm run lint
```
To try it, install transformers and set `VIBE_SEGMENTER=sam2`:
```bash
uv pip install 'transformers>=4.56'
VIBE_SEGMENTER=sam2 npm run dev
```
Blur someone and export.

**Measured** (one run, RTX 4050 Laptop 6 GB, Windows 11, torch 2.6 cu124, `sam2.1-hiera-tiny`; from the research doc):
- 121 keyframes segmented in 16.1 s, 0.133 s per frame.
- 120 of 120 frames hidden by shape, none fell back to the box.

**Checked by hand** (the same laptop, 2026-11-25, not benchmarks):
- On the pink-jacket clip, the shape follows the person at 3 s and 7 s in the export, and the other two people stay visible.
- On a crowded clip at 4 s, the shape is thicker, spills onto the neighbour's shoulder and leaves a couple of square edges. The person stays hidden, but it isn't clean.
- In the preview, the blur shows the person's outline.

## Decisions made (the team's call)
- **Contract:** D-018 sketches an optional per-keyframe mask reference on `Keyframe`, but the spike keeps the contract as is: masks are looked up by (track id, keyframe time, box) in the cache. Either amend D-018 to say masks stay a cache side table, or add `Keyframe.mask` (a `schema_version` bump and a heads-up). Not decided.
- The preview uses the nearest keyframe's shape (within 0.5 s) without moving it between keyframes, as the export does. It's an approximation, like the rest of the preview.

## Next steps
- Decide the contract question above.
- Tune the margin and the joining between keyframes (the 4 s case above).
- One-keyframe tracker fragments stay boxes (they're `unsure` and masked), which can hide the shapes under them. The ByteTrack fix in #47 removes most of them.

## Gotchas
- Building SAM 2.1 downloads its weights into the Hugging Face cache the first time.
- `POST /shapes` doesn't take the project's lock, so chatting goes on while it runs. Segmenting is serialised by its own lock.
