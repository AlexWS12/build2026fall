---
author: jonathan
date: 2026-11-03
branch: jonathan/f64-api-routes
features: [F-64]
base: v0.1.0 @ 21ee75c
head: 4fba60e
status: ready for review
---

# F-64 API routes: upload, chat request (stub apply) and timeline state, one request at a time per project

> **Heads-up:** this changes two shared files: the **API routes** (three new routes, the OpenAPI spec and the generated UI types) and a **dependency file** (`python-multipart` added to `backend/pyproject.toml` and `uv.lock`). Post in the team chat before merging. The contract, storage and toolbox aren't touched.

## What changed
- **`backend/src/vibe/api/projects.py`** has the three routes, registered in `app.py`:
  - **`POST /api/projects`:**
    - Takes a multipart `file` and returns `ProjectInfo` with 201.
    - Saves the upload under its original file name, checks it with `probe()` (PyAV, no ffmpeg CLI needed), then calls `Storage.create_project`.
    - A file that isn't a readable video gets 400 and creates nothing.
  - **`POST /api/projects/{id}/requests`:**
    - Takes `{text}` (non-empty, not only spaces) and returns `ChatReply {request_id, reply}`. Request ids look like `r_…`.
    - The reply comes from F-05's `plan_request`:
      - A `Plan` is described in the reply, which says plainly that **nothing was applied yet**.
      - A `Clarify` sends its question as the reply and writes no op.
  - **`GET /api/projects/{id}/state`:** returns the `TimelineState` folded from the project's op log.
  - **Errors:** an unknown or invalid project id gets 404. A body that fails validation gets 422. A busy project, or an op the contract refuses, gets 409.
  - **One request at a time per project:** each project has a `threading.Lock`, taken without waiting. If a request is already in flight for that project, the next one gets 409 instead of queueing.
  - **`apply_plan(project, request_id, plan)` is a STUB**, marked `TODO(F-06 toolbox + plan executor)`:
    - It ignores the plan and appends one example op, a mute from 0:00 to 0:01 with `source: "agent"`, through `Project.append`. That way `/state` returns a real timeline.
    - The reply says it's a stub.
    - Plans are **not** turned into ops.
- **`backend/tests/test_api_projects.py`** has 14 tests in stub mode, covering:
  - upload: project on disk, non-video gets 400, missing file gets 422;
  - an empty timeline for a new project;
  - 404s;
  - a planned request writes the stub op with its `request_id`;
  - a clarify writes nothing;
  - unique request ids;
  - 422 bodies;
  - 409 while the project's lock is held.
  
  The tests check that a reply exists, not its wording. The test clip is encoded with PyAV, so the tests run without the ffmpeg CLI.
- **Spec and UI types:**
  - `openapi.json` is regenerated (`uv run python -m vibe.api`), and so is `frontend/src/api/generated/schema.ts` (`npm --prefix frontend run gen:api`).
  - In `frontend/src/api/types.ts`, `ProjectInfo`, `VideoRef` and `ChatReply` now come from the generated schema. `TimelineState` is still hand-written; see Gotchas.
- **`backend/pyproject.toml` / `uv.lock`:** added `python-multipart>=0.0.9`, which FastAPI needs for file uploads.
- **`docs/ROADMAP.md`:** claimed F-64 (jonathan, `jonathan/f64-api-routes`) in Sprint 2, and set the backlog row to `in progress`.

## How to check it
```powershell
# Tests (from the repo root)
npm test                      # backend: 243 passed, 49 skipped; frontend: 12 passed
uv run --directory backend python -m vibe.api --check   # spec up to date (exit 0)

# The UI against the real backend: two terminals in the repo root
uv run --directory backend uvicorn vibe.api.app:app --reload --port 8000
$env:VITE_API_MODE = 'http'; npm --prefix frontend run dev    # then open http://localhost:5173
```
Upload a short video, then send these requests:
- **"blur faces"** gets a "Planned: … Nothing was applied yet" reply, and the timeline shows a 0:00–0:01 mute.
- **"cut it"** gets the planner's question, and the timeline doesn't change.

The status badge should read `ok`, not `mock`. Route docs are at http://localhost:8000/docs.

Verified on 2026-11-03 on Windows 11 (no GPU used), Python 3.12.9:
- `npm test`, `ruff check`, `ruff format --check`, the spec check, `tsc -b` and `oxlint` all pass.
- The 49 skipped backend tests are the existing video tests that need the ffmpeg CLI, which isn't installed on this machine. CI installs it.
- jonathan ran the UI flow above by hand with `VITE_API_MODE=http`: upload, request and timeline all worked.
- CI hasn't run yet; it runs on the PR.

## Decisions made
jonathan made these on 2026-11-03. They're F-64 details, not decisions-log material; say on the PR if you disagree.
- **The plan executor isn't part of F-64.** jonathan will raise ownership with the team. Until it exists, the requests route plans but doesn't apply.
- **A busy project gets 409, not a queue.** The UI sends one request at a time, and a waiting HTTP request would just hang.
- **The stub writes a real but harmless op** (a 1 s mute), and the reply says so. That way the end-to-end path (UI → API → storage → fold → UI) works for real, and a fake edit is never silent.
- **Clarify writes nothing**, so a question never changes the timeline.

## Next steps
- Post the heads-up, open the PR and get a review. After merging, the Sprint 2 F-64 card is done.
- **Plan executor (owner to be agreed by the team), once F-06 (PR #16) merges:**
  - Replace `apply_plan` in `projects.py` with the real executor: run the calls through `vibe.toolbox.call_tool` and fill each `$ref` with an earlier step's result.
  - Dry-run on an in-memory `OpLog` first, so a failing step never leaves half a request in the timeline.
  - Give `ToolContext` the duration (from `probe()` on the stored video) and the tracks (from F-08's cache).
  - Delete the stub op and its wording, and update `test_planned_request_replies_and_writes_the_stub_op`.
- **When F-06 merges:** `plan_request`'s default registry moves from `tools_draft` to `vibe.toolbox.TOOLS`. Nothing in `projects.py` needs to change for that.
- **Later routes** (undo/redo, hand edits, export) aren't in this card.

## Gotchas
- **The lock only works in one process.** Running uvicorn with several workers would let two requests for one project interleave. That would need a lock on disk, or a single worker.
- **`TimelineState` is still hand-written in `frontend/src/api/types.ts`.** The generated schema marks fields that have defaults (`masks`, `cuts`, `mutes`, `end`, …) as optional, even though the server always sends them. Using it would mean `?? {}` guards throughout the timeline view. The real fix is in the contract's JSON-schema settings, which is a shared file, so it's left for the contract owner.
- **Storage bug inherited from storage (found by pablo, F-06 handoff):** if a crash leaves a half-written last line in `timeline.jsonl`, the next `Project.append` glues onto it and the log becomes unreadable. The requests route appends through `Project.append`, so it inherits this. It's not fixed here because storage is a shared file.
- **Projects persist under `data/` at the repo root** (gitignored). Set `VIBE_DATA_DIR` to put them somewhere else.
- **On Windows, if a repo-root `.venv` is active,** `uv` prints a `VIRTUAL_ENV … does not match` warning. It's harmless: the backend uses `backend/.venv`. Don't pass `--active` to `uv run`: it rebuilds the root `.venv`.
- The pytest `httpx2` deprecation warning is the existing one from F-01.
