---
author: nickolas
date: 2026-10-29
branch: nickolas/data-policy
features: [R-4]
base: v0.1.0 @ 21ee75c
head: ea4a0a1
status: ready for review
---

# R-4 data policy and target shape: local-first footage, boxes now and masks later

> **Heads-up:** this adds two rows to the decisions log in `docs/ARCHITECTURE.md` (a shared file), proposing **D-017** (data policy) and **D-018** (target shape). Post in the team chat before merging. **paul (R-1)**, **eduardo (F-08)** and **jean (F-09)** are directly affected; see "Next steps".

## What changed
- **`docs/research/r4-data-policy-and-target-shape.md`:** the R-4 write-up. It lists every path by which footage could leave a machine, the reasons for and against sending it to the cloud, the proposed policy (data classes × destinations), the case for boxes first, the mask design for F-15, caveats and sources.
- **`docs/ARCHITECTURE.md`:**
  - **D-017 (Proposed), data policy:** text and metadata may go anywhere, including the cloud LLM. Media (frames, crops, clips, audio) may go to team-controlled machines, and to a third-party service only with a per-project opt-in (off by default) for clips whose consent covers it. Biometric data (face embeddings) never leaves its machine and isn't written to the shared cache. The planner never sees pixels. Live mode: no network. A missing opt-in fails closed.
  - **D-018 (Proposed), target shape:** boxes are the v1 target. F-15 later adds an optional per-keyframe mask reference into the analysis cache, and the box stays as the fallback.
  - Closed the two R-4 open questions, replaced the "whether the planner also sees a few frames" item in "Agent / planner" with D-017, and noted in "Compute" that a `remote` server on a non-team machine counts as a third-party service.
- **`README.md`:** the "Hybrid inference" constraint now points at D-017 instead of calling it an open question.
- **`docs/ROADMAP.md`:** claimed the Sprint 2 R-4 row (owner, branch, `in progress`).

## How to check it
- Read the TL;DR and the two "The policy / The design" sections of `docs/research/r4-data-policy-and-target-shape.md`, and the D-017 / D-018 rows in `docs/ARCHITECTURE.md`.
- No code changed. Backend: `cd backend && uv run pytest -q` gave 278 passed, and `uv run ruff check .` passed, on Linux x86_64, Python 3.12.3, uv 0.11.32, pydantic 2.14.1, no GPU. Frontend tests weren't run; CI covers them.

## Decisions made
- **D-017 and D-018 (Proposed)**, above. The policy stance (local-first, with an opt-in rather than a total ban or provider-terms-based permission) was chosen by nickolas on 2026-10-29 from three options; the write-up says why.
- **Transcript excerpts count as text**, so F-21 (cut by transcript) can go through the planner. This is the one grey area in the policy; say so on the PR if you disagree.

## Next steps
- Post the heads-up, get a review and merge. Agree on D-017 and D-018 as a team; R-4 is `done` at sprint close once they're agreed.
- **R-1 (paul):** compare planner candidates on text-only input. A model that needs to see frames to plan well doesn't fit D-017.
- **F-08 (eduardo):** the `remote` backend should take an allowlist of team-controlled hosts; any other host needs the project opt-in.
- **F-09 (jean):** have the clip manifest record whether each clip's consent covers cloud processing.
- **F-64 (unclaimed):** a per-project `allow_cloud_media` setting, default `false`.
- **F-15 / F-52 (unclaimed):** F-15 follows D-018's mask design and decides the `schema_version` bump with the contract owner. F-52 follows D-017's biometric rules (enrollment consent, local template, transient bystander embeddings).
- Ask the course staff whether any rules apply to recording people for the demo.

## Gotchas
- **The legal points come from secondary sources.** The cloud session blocked the primary statute and provider pages, so the BIPA / GDPR summaries and the Anthropic retention numbers are from search results, and those disagree on retention (30 vs 7 days). The policy doesn't depend on them, but don't quote them as verified.
- **"Boxes vs masks" was mostly decided already** by D-013 and D-015. D-018 adds the part nobody had written down: how masks fit later and what the renderer does when a mask is missing.
- **Adding a field to `Keyframe` isn't free:** contract models use `extra="forbid"`, so old code rejects tracks that carry masks, even though the op log format doesn't change.
- Nothing here was measured. Whether a VLM on the 5070 is good enough for F-13 / F-22 is for F-50 / F-09.
