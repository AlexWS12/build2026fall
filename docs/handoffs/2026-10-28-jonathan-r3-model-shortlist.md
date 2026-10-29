---
author: jonathan
date: 2026-10-28
branch: jonathan/r3-model-shortlist
features: [R-3]
base: v0.1.0 @ 16acd80
head: 3de18f0
status: ready for review
---

# R-3 model shortlist: permissive detection, tracking, identity and mask models, with licenses

> **Heads-up:** this adds a row to the decisions log in `docs/ARCHITECTURE.md` (a shared file), proposing **D-015**, and adds a new folder, `docs/research/`, for the full write-up. Post in the team chat before merging.

## What changed
- **`docs/research/r3-model-shortlist.md`:** the full R-3 write-up. It covers detection, face/plate detection, tracking, masks and identity, with a license (code and weights), size, sm_120 support and reported speed for each model, plus two proposed pipelines (live and offline) and what F-50 should measure.
- **Decision (D-015, Proposed):** keep only Apache 2.0 / MIT models (code and weights) on the default path.
  - Detect: RF-DETR N/S for live, L for offline. D-FINE / DEIMv2 are the alternates.
  - Faces and plates: EgoBlur Gen2 offline, YuNet live.
  - Track: Roboflow `trackers` (ByteTrack by default, OC-SORT / BoT-SORT as alternates).
  - Identity: SFace first, AdaFace as the accuracy upgrade.
  - Masks: SAM 2.1, with EdgeTAM for live.
  - Excluded unless the team agrees: YOLOE and anything loaded through Ultralytics (AGPL), BoxMOT (AGPL), YOLO-World (GPL), InsightFace weights (non-commercial), re-ID weights (trained on research-only datasets), SAM 3.x (custom license).
- `docs/ROADMAP.md`: added this branch to my Sprint 2 R-3 card.

## How to check it
- Read the "Shortlist Table" and "Recommendations" sections of `docs/research/r3-model-shortlist.md`, and the D-015 row in `docs/ARCHITECTURE.md`.
- No code changed. I ran `npm test`. The backend passed 79 tests and skipped 49, because ffmpeg isn't installed on my machine. The frontend tests didn't run, because its dependencies aren't installed locally (`vitest` not found). CI covers both.

## Decisions made
- D-015 (Proposed), above. It can't move to Accepted until F-50 measures the shortlisted models on the RTX 5070.

## Next steps
- Post the heads-up, get a review and merge. Agree on D-015 as a team.
- **Team call needed:** say explicitly in the decisions log whether we want a shippable license posture, or whether non-commercial weights are fine because this is coursework. That decides whether InsightFace and SAM 3.x can come back in.
- **F-50 (live spike):** use the "What F-50 should measure" section of the write-up. It lists a model × runtime × resolution matrix, per-stage timings, a 33.3 ms p95 budget for 30 fps, peak VRAM with all models loaded at once, and FP16 accuracy checks for D-FINE.
- **F-10 / F-52:** build on the live and offline pipelines in the write-up.
- R-3 is marked `done` at sprint close, once D-015 is agreed.
- D-011 says to revisit Python 3.14 after this shortlist. I didn't check every model's Python support, so that's still open.

## Gotchas
- **None of the speed numbers were measured by us.** They're vendor or paper figures on T4, V100, A100 and H100 GPUs, and on CPU for YuNet. They aren't comparable to each other or to the 5070. Use them only to pick what F-50 tests.
- **The license depends on the wrapper, not only on the model.** RT-DETR is Apache 2.0 from its own repo but AGPL through `ultralytics`. The ByteTrack and OC-SORT reference repos are MIT, but BoxMOT is AGPL.
- **Code licenses and weight licenses differ.** InsightFace, SCRFD and the re-ID models have permissive code with non-commercial or research-only weights. AdaFace is MIT but trained on WebFace4M, so dataset terms are a residual risk.
- Several items in the write-up still need checking against the primary source. They're listed under "Caveats": EgoBlur's gated-download terms, Grounding DINO's weights license, the DeepSORT and Norfair licenses, ONNX Runtime GPU support for sm_120, and some secondary-source version and release facts.
- **Privacy:** the identity step runs face recognition on bystanders, only to exclude the enrolled user from masking. Whoever builds F-52 should write down enrollment consent, keep embeddings stored locally, and match non-enrolled faces only against the user's template. Unsure matches stay masked (fail closed).
