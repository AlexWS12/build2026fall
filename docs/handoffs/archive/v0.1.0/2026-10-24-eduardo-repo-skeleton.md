---
author: eduardo
date: 2026-10-24
branch: eduardo/repo-skeleton
features: [F-01]
base: untagged @ 269de2f
head: dc7bc9d
status: ready for review
---

# Repo skeleton: backend/ + frontend/ layout, `vibe` package, tests, lint, CI and `npm run dev`

> **Heads-up:** this adds shared files (dependency files, run scripts, CI config, the first API route). Post in the team chat before merging. It also adds decisions D-011 (layout) and D-012 (FastAPI, **proposed**).

## What changed
- **Layout (D-011):** one repo with `backend/` and `frontend/` side by side, each with its own toolchain. See "Code layout" in `docs/ARCHITECTURE.md`. `frontend/` doesn't exist yet: F-07 creates it.
- **`backend/`:** the uv project moved here from the root (`pyproject.toml`, `uv.lock`, `.python-version` = 3.12). It's a src-layout package named `vibe`, with one empty subpackage per layer for its owner to fill: `contract` (F-02), `video` (F-03), `toolbox` (F-06), `modules` (F-08), `render` (F-04), `agent` (F-05), `api`.
- **`vibe/config.py`:** `VIBE_BACKEND` selects `stub` / `local` / `remote` and defaults to `stub`, so nothing needs a GPU. Unknown values raise an error.
- **`vibe/api/app.py`:** a FastAPI app with `GET /api/health`. Every route lives under `/api`, so the Vite dev server can proxy it.
- **Tests and lint:** `backend/tests/test_smoke.py` checks that every layer imports, that health defaults to stub, and that an unknown backend is rejected. Ruff (lint + format) and pytest are configured in `pyproject.toml`. The dev dependencies are pytest, ruff and httpx.
- **Root `package.json`:** `npm run dev` (uses `concurrently`, starts the backend on port 8000), `npm test`, `npm run lint`. F-07 adds the frontend to `dev`.
- **CI:** `.github/workflows/backend.yml` runs only when `backend/` changes. It runs `uv sync --locked`, `ruff check`, `ruff format --check` and `pytest`, using the stub backend on a runner with no GPU.
- **Docs:**
  - README: repo tree and "Run the app"
  - ARCHITECTURE: "Code layout", plus D-011 and D-012
  - HANDOFF.md: the new shared files, listed by path
  - ROADMAP: F-01 claimed, and F-07 gets the generated-API-types extra (also updated on its Trello card)
- Builds on [2026-10-24-eduardo-plan-sprint1.md](2026-10-24-eduardo-plan-sprint1.md).

## How to check it
```bash
npm install
npm run lint     # Ruff: all checks pass
npm test         # 9 passed
npm run dev      # then open http://localhost:8000/api/health -> {"status":"ok","backend":"stub"}
```
Verified on 2026-10-24 (macOS, Apple Silicon, Python 3.12.12, Node 22.16) from a clean copy with no `.venv` or `node_modules`: `npm ci`, `uv sync --locked`, lint and tests all pass, and `npm run dev` serves the health route.

**CI:** the `backend` workflow passed on PR #4 at `dc7bc9d` (ubuntu-24.04, no GPU, stub backend), so F-01's exit check is met. The first run failed only because `setup-uv@v10` doesn't exist (see Gotchas).

## Decisions made
- **D-011 (Accepted):** sibling `backend/` and `frontend/` folders in one repo. The generated frontend types (D-009) need both sides in the same PR, and separate folders keep the toolchains apart. Python stays at 3.12 for now. A resolve check found current wheels for torch, PyAV, OpenCV, onnxruntime and faster-whisper on 3.14 too (Linux, Windows, macOS 14+), so 3.14 gets revisited after the R-3 model shortlist.
- **D-012 (Proposed): FastAPI + Uvicorn.** The skeleton uses it because its OpenAPI output is what D-009 needs. **The team should confirm it**, then flip it to Accepted.
- **Package name `vibe`.** The root `npm run dev` uses `concurrently`, because everyone needs Node for the frontend anyway and it works the same on Windows and Mac. Releases stay versioned by git tags, so `pyproject.toml` says `version = "0.0.0"`.
- **The generated API types go to F-07 as an extra**, not into F-01.

## Next steps
- PR #4 is open with CI green, and F-01 is marked `done` in `docs/ROADMAP.md`. It needs a review and then a merge.
- Post the shared-files heads-up, and get D-012 confirmed.
- F-02, F-03, F-06 and F-08 can now start in their `backend/src/vibe/<layer>/` folders. F-07 creates `frontend/`, adds `dev:frontend` to the root `package.json`, and adds `frontend.yml`.

## Gotchas
- **pytest prints a warning:** Starlette says using `httpx` with its test client is deprecated and suggests a package called `httpx2`. It wasn't installed, because that name came only from the warning and hasn't been checked. The tests pass. Whoever looks into it should confirm the package is legitimate first.
- **Run uv commands from `backend/`**, or use `uv run --directory backend ...` (the root npm scripts already do). Running `uv` at the repo root finds no project.
- **Mac teammates on macOS 13 or older** get older torch and onnxruntime wheels, since the current ones need macOS 14+.
- `astral-sh/setup-uv` publishes exact tags only, so the workflow pins `v10.3.2` (released 2026-10-22). A floating `@v10` fails to resolve; that broke the first CI run.
