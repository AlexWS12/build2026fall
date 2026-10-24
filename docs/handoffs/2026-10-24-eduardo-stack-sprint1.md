---
author: eduardo
date: 2026-10-24
branch: eduardo/stack-sprint1
features: []
base: untagged @ baa46b5
head: 1f1f743
status: ready for review
---

# Sprint 1 tech stack: frontend and uv decided, the rest proposed

> **Heads-up:** this records dependency and tooling decisions (shared files). Post in the team chat before merging.

## What changed
- `docs/ARCHITECTURE.md`, decisions log: added **D-009** (frontend is React + Vite + TypeScript, with frontend types generated from the backend's OpenAPI spec) and **D-010** (Python environments and dependencies use uv, with a pinned Python version, one committed `uv.lock`, and GPU-only packages such as PyTorch `cu128` in an optional dependency group).
- `docs/ARCHITECTURE.md`, open questions: removed the frontend question. The planner-LLM and analysis-cache-storage questions are marked as team decisions, with candidates listed.
- `README.md`, tentative stack table: the Frontend row is marked decided (D-009), and a new "Environment and packaging" row is decided (D-010).
- No code. Builds on alex's foundation entry ([2026-10-23-alex-docs-foundation.md](2026-10-23-alex-docs-foundation.md)).

## How to check it
Read the decisions log in `docs/ARCHITECTURE.md` (D-009, D-010) and the "Tentative tech stack" table in `README.md`. There are no tests yet because there's no code and no test command.

## Decisions made
- **D-009, React + Vite + TypeScript.** The timeline, review queue and drag-to-fix boxes carry a lot of state that 10 people will edit. Typed components and generated types catch contract mismatches at build time. Plain JS (the Knight Hacks approach) was the alternative.
- **D-010, uv.** It gives one fast, reproducible install for everyone, and CPU-only laptops can skip the GPU group.
- **Left to the team, deliberately not decided:**
  - **The planner LLM.** The candidates are Claude Opus 5 (`claude-opus-5`, $5/$25 per million input/output tokens) and Claude Sonnet 5 (`claude-sonnet-5`, $2/$10), ideally compared on the F-09 request set. These are list prices, not measurements.
  - **Storage.** The candidate is one folder per project: a `timeline.jsonl` op log plus `cache/` keyed by video hash, with no database.

## Next steps
- Review and merge this PR after the team heads-up.
- Team discussion of the rest of the proposed Sprint 1 stack. **These are candidates only, not decided:**
  - Python 3.12, FastAPI + Uvicorn, Pydantic v2 for the contract and tool schemas.
  - In-process background tasks, no Celery or Redis.
  - ffmpeg 7.x, PyAV for the per-frame path, opencv-python-headless + NumPy for effects.
  - The Anthropic SDK with structured outputs (`client.messages.parse()` against a pydantic `Plan` model), behind a planner interface with a stub backend.
  - Native `<video>` with a canvas overlay for boxes, Node 22 LTS, npm.
  - PyTorch 2.7+ `cu128` for the RTX 5070, a FastAPI model server over Tailscale for the `remote` backend, `huggingface_hub` for weights.
  - Ruff, Biome or ESLint + Prettier, pytest in stub mode, Vitest, Pyright (optional), GitHub Actions CI.
  - No Docker in Sprint 1.
- Plan Sprint 1 in `docs/ROADMAP.md`. It needs the roster, each person's GPU, the sprint length and dates, and a sprint lead.

## Gotchas
- **License check for the F-50 person detector:** Ultralytics YOLO is AGPL-3.0, which would force the whole repo to stay open source. RT-DETR (the non-Ultralytics build) and YOLOX are Apache-licensed options.
- The RTX 5070 needs CUDA 12.8+ and PyTorch 2.7+ `cu128` wheels. Check that before adopting any model with custom CUDA kernels.
