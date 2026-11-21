---
author: nickolas
date: 2026-11-21
branch: nickolas/f33b-render-audio-filters
features: [F-33b]
base: v0.4.0 @ 6491308
head: dd07478
status: ready for review
---

# The renderer applies denoise: exports with a denoise span come out denoised (stub backend)

## What changed
- `audio_filters` spans with `filter: "denoise"` are applied in exports instead of refused. Denoise runs before mutes and cuts, is mixed in at the span's strength with 10 ms fades, and audio outside the spans is untouched.
- Two streaming passes, so an export never holds the whole clip: `learn_noise` keeps one noise profile per 10 s window, then `NoiseGate` gates chunk by chunk (any chunk size gives the same samples).
- The noise profile is cached per video (`noise_profile`), so re-exports skip pass 1. The export route and the render CLI both use the cache.
- After Edugre's review on #40: with `VIBE_BACKEND=local` or `remote`, a denoise export is a 422 that names the filter ("the 'local' denoise backend isn't built yet"), not a 500. Nothing is written.

## How to check it
- `cd backend && .venv/Scripts/python.exe -m pytest tests/render/test_render_export_denoise.py tests/test_api_projects.py -q`
- In the app (with `VIBE_BACKEND=stub`, the default): upload a clip with sound, ask "reduce the noise", export.
- Full suite on Windows, no GPU: 1445 passed, 118 skipped, 1 failed (`test_writing_keeps_the_timeline_permissions`, which also fails on `main` on Windows).
- Noise reduction is checked on synthetic sound only (a tone with pauses plus hiss). Not checked by listening to real recordings.

## Decisions made
- The `local` / `remote` model is not picked (DeepFilterNet is a candidate, not a decision).

## Next steps
- Minor review notes still open on #40: add `PROFILE_MARGIN_DB`, `_NOISE_PERCENTILE` and `_FRAME_SECONDS` to the cache key; check the profile file's shape when loading it; set `_mono` after the channel check; gate only near the spans instead of the whole source.
- Pick the local/remote model; the per-chunk (live) path.

## Gotchas
- A clip that's speech from start to end with no pauses gives a poor noise estimate, so the voice can be thinned.
