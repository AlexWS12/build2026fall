# F-15 spike: segmentation masks instead of boxes

**Bottom line:** a masked target can now be hidden by its **shape** instead of its padded box,
when a segmentation mask fits. Masks come from **SAM 2.1** (Apache 2.0), prompted with each
track's own box, computed **only for the tracks an edit masks, only at export time**, and cached
under a new `masks` kind. Anything doubtful falls back to today's padded box. No contract change:
masks are looked up by (track id, keyframe time, keyframe box), so D-018's per-keyframe reference
isn't needed yet (see "Contract" below; the team decides). Prototype on `luis/f15-masks`, CLI
only (`python -m vibe.render export ... --segmenter sam2`); not wired into the app yet.

## Two mask sources

| | torchvision Mask R-CNN (`maskrcnn_resnet50_fpn_v2`) | SAM 2.1 (`facebook/sam2.1-hiera-*`) via transformers |
|---|---|---|
| License | Code BSD-3; torchvision's COCO weights, same terms as the `tv-frcnn` detectors we use | Code and weights Apache 2.0; already the mask engine in D-015 |
| Mask for *our* track | Runs a whole detector, then its masks are matched to the track box by IoU; no match = no mask | The track's box is the prompt: one mask per box, nothing to match |
| Edges | 28×28 per instance, upsampled: blobby, loses arms and hair | Full-resolution decoder |
| Fails on | Crowds and overlaps (wrong instance matched); frames it misses the person | A box holding two people can give the wrong one; a drifted box gives background |
| Cost | One detector pass per keyframe whatever the number of targets | One image-encoder pass per frame, shared by all boxes in it, plus a small decoder per box |
| Packages | torchvision (installed) | transformers ≥ 4.56 (installed); neither in `pyproject.toml` |
| 6 GB RTX 4050 | Fits | tiny / small fit; base+ should |
| Needs a bigger GPU (L4) | No | `large`; SAM 2 *video* propagation on long clips (its memory grows with frames) |

Sizes and parameter counts are the publishers'. **Chosen: SAM 2.1, image mode, box-prompted,
`hiera-tiny` on laptops.** Mask R-CNN can be added behind the same `Segmenter` interface as a
CPU comparison; its coarse masks undercut the point of F-15.

## How it works

- `vision/segmenters/`: `Segmenter` protocol (`segment(image, boxes) -> masks`), `stub` (the
  ellipse inside each box, no model) and `sam2` (`model=facebook/sam2.1-hiera-tiny` by default).
  Picked by spec like detectors: `sam2:model=facebook/sam2.1-hiera-small`.
- `vision/masks.py`: `ensure_masks(storage, sha256, video, state, tracks, segmenter)` reads
  `state.masks`, takes each targeted track's keyframes inside its spans plus one on each side
  (skipping `uncertain` ones), segments only those not cached, writes
  `cache/<sha256>/masks/<key>.json` (one file per track and segmenter; the key hashes the
  segmenter's settings and the track's keyframes) and returns a `MaskSet` for the renderer.
- `vision/rle.py`: COCO compressed RLE in numpy. Byte-identical to `pycocotools.mask.encode` on
  204 test masks (one-off script); no new dependency.
- `render/shape.py` + `FrameRenderer(state, tracks, masks)` / `export(..., masks=...)`: between
  two keyframes, both masks are moved and scaled from their keyframe box onto the interpolated
  box and joined; grown by `padding / 2 ×` the box's shorter side (the same margin per side as
  the box); the effect is worked out over the padded box (same blur size as before) and applied
  only inside the shape.
- **Fails closed:** the padded box is used whenever the box isn't straight from keyframes (held,
  lost, a gap over 1 s, next to an `uncertain` keyframe), a keyframe has no mask, a mask covers
  under 15% of its box or its outline overlaps the box under 0.5 IoU, decoding raises, or
  segmenting fails (the CLI says why and renders boxes). `RenderResult.shape_use` counts, per
  track, frames hidden by shape vs by box.

## Contract

D-018 sketches an optional mask reference on `Keyframe`. That breaks old readers
(`extra="forbid"`) and likely bumps `schema_version`; it also doesn't fit "compute at export
time", because the cached tracks file would have to be rewritten after the fact. The spike keeps
the contract as is. **For the team:** (a) keep masks as a side table in the cache and amend D-018
to say so, or (b) add `Keyframe.mask` as sketched (heads-up first, schema bump).

## Measured

One run, 2026-11-25, **RTX 4050 Laptop GPU (6 GB), driver 555.97, Windows 11**, torch 2.6.0
cu124, transformers 5.22.1, fp32. Clip: Pexels 8126705 (1920×1080, 30 fps, 11.3 s, three
people walking). Tracks: `tv-frcnn-mobile` + `iou`; one person masked (`blur`, padding 0.15)
from 0 to 4 s. Numbers are what `python -m vibe.render export ... --segmenter sam2` printed:

| | |
|---|---|
| Keyframes segmented (masked span + one each side) | 121 (one track; other tracks: none) |
| Time in SAM 2.1 tiny (`segment_seconds`) | 16.1 s, **0.133 s per frame** |
| Whole mask step (decode frames + segment + cache write) | 16.9 s |
| Frames hidden by shape / by box | 120 / 0 (no fallback on this clip) |
| Second export | 0 computed, 121 reused from the cache |

One clip and one person: not a quality benchmark. The first frame includes CUDA warm-up. The
checkpoint loads with a transformers warning (`sam2_video` config into `Sam2Model`); all 309
weights load and the masks look right on this clip, but it's worth a check when the model is
pinned. Before/after frame: kept outside the repo (outputs aren't committed).

To reproduce: analyse a clip (`python -m vibe.modules.vision`), write a timeline with an
`add_mask`, then run the export twice, with and without `--segmenter sam2`.

## Not done / next

- Not wired into `POST /exports` or `config.py` (a `VIBE_SEGMENTER` setting); both are shared or
  have F-13 work in flight. With the API, segmenting a long clip should run as a job, not inside
  the request.
- No preview path: the per-frame preview would need masks for the frame shown.
- SAM 2 video propagation (temporal consistency), `small` / `large` on the L4, and a quality
  number (mask IoU against hand labels) are not done.
- Masks are media (D-017): they stay in the local cache like tracks.
