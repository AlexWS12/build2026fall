---
author: jonathan
date: 2026-11-03
branch: jonathan/f64-api-routes
features: [F-64]
base: v0.1.0 @ 21ee75c, merged with main @ b5e3c66
head: ec390c1
status: ready for review
---

# F-64 API routes: upload, chat request (planned, not applied yet) and timeline state, one request at a time per project

> **Heads-up:** this changes two shared files: the **API routes** (three new routes, the OpenAPI spec and the generated UI types) and a **dependency file** (`python-multipart` added to `backend/pyproject.toml` and `uv.lock`). Since the first review, `ChatReply` also has a new **`status`** field. Post in the team chat before merging. The contract, storage and toolbox aren't touched.

## What changed
- **`backend/src/vibe/api/projects.py`** has the three routes, registered in `app.py`:
  - **`POST /api/projects`:**
    - Takes a multipart `file` and returns `ProjectInfo` with 201.
    - The client's file name is never used as a path. The upload is saved as `upload<ext>`, checked with `probe()` (PyAV, no ffmpeg CLI needed), then passed to `Storage.create_project`.
    - The cleaned file name (path parts and unprintable characters removed, capped at 100 characters) becomes the project's `name`. Storage records `original_name` as `upload<ext>`.
    - `<ext>` is kept only if storage accepts it (lowercase, 1 to 10 letters or digits). Any other extension is dropped, so a readable video with an odd extension is still accepted.
    - A file that isn't a readable video gets 400 and creates nothing. An upload over `MAX_UPLOAD_BYTES` (2 GiB) gets 413.
  - **`POST /api/projects/{id}/requests`:**
    - Takes `{text}` (non-empty, not only spaces) and returns `ChatReply {request_id, status, reply}`. Request ids look like `r_…`.
    - **Nothing is applied yet: the plan executor is a separate card** (Next steps). The reply comes from F-05's `plan_request`, and **no request writes to the timeline**:
      - A `Plan` replies `status: "not_applied"`. The reply describes the plan and says nothing was changed.
      - A `Clarify` replies `status: "clarify"` with its question.
      - `status: "applied"` is in the type for the executor, so it won't need another API change. It's never returned yet.
    - This fails closed: "blur faces" can never look done while nothing is masked.
  - **`GET /api/projects/{id}/state`:** returns the `TimelineState` folded from the project's op log.
  - **Errors:**
    - An unknown or invalid project id gets 404. A body that fails validation gets 422.
    - A busy project gets 409. That's the only meaning of 409.
    - A planner bug (`PlanError`, `ValueError`) gets 500, and `LlmError` gets 503. Both say "Nothing was changed".
    - Error details are short fixed messages. The full error, which can include server paths, is logged on the server and never sent to the client.
  - **One request at a time per project:** a set of busy project ids, guarded by one lock. If a request is already in flight for that project, the next one gets 409 instead of queueing. An id leaves the set when its request finishes, so the set doesn't grow.
- **Review fixes (PR review, `ec390c1`):** the changes above that replaced the first version. That version had these problems:
  - It appended a stub mute (0:00 to 0:01) to the real timeline for every planned request, and gave privacy requests a 200 with no mask. Removed, and replaced by `status`.
  - Unsafe upload names (`..`, very long names, NUL bytes) gave a 500.
  - Planner errors weren't caught.
  - Error details exposed server paths.
  - Readable videos with odd extensions were rejected.
  - There was no upload size limit.
  - One 409 covered both "busy" and "refused".
  - The per-project lock dictionary grew forever.
- **`backend/tests/test_api_projects.py`** has 30 tests in stub mode, covering:
  - upload: project on disk, cleaned names (`..`, paths, a 300-character name, `CON.mp4`, non-ASCII, a bare `.mp4`), a NUL byte (sent in a hand-built multipart body, because the test client percent-encodes it), an odd extension, 413, a non-video gets 400, a missing file gets 422;
  - no server paths in any error response;
  - an empty timeline for a new project, and 404s;
  - planned requests reply `not_applied` and write nothing ("blur faces", a mute, a cut);
  - a clarify replies `clarify` and writes nothing;
  - unique request ids;
  - planner failures (500/503) write nothing and free the project;
  - 422 bodies;
  - 409 while the project is busy, and the busy set is empty afterwards.

  The tests check that a reply exists, not its wording. The test clip is encoded with PyAV, so the tests run without the ffmpeg CLI.
- **Spec and UI types:**
  - `openapi.json` is regenerated (`uv run python -m vibe.api`), and so is `frontend/src/api/generated/schema.ts` (`npm --prefix frontend run gen:api`).
  - In `frontend/src/api/types.ts`, `ProjectInfo`, `VideoRef` and `ChatReply` come from the generated schema. `TimelineState` is still hand-written; see Gotchas.
  - The UI change for `status` is minimal: the mock API (`frontend/src/api/mock.ts`) sends `status: "not_applied"`. The chat still shows only `reply`.
- **`backend/pyproject.toml` / `uv.lock`:** added `python-multipart>=0.0.9`, which FastAPI needs for file uploads.
- **`docs/ROADMAP.md`:** claimed F-64 (jonathan, `jonathan/f64-api-routes`) in Sprint 2, and set the backlog row to `in progress`.
- **Merged `origin/main` into this branch** (`4947d28`, no rebase), which brought in F-06 (below). The only conflict was in `docs/ROADMAP.md`; it kept both sides.

## How to check it
```powershell
# Tests (from the repo root)
npm test                      # see the results below
uv run --directory backend python -m vibe.api --check   # spec up to date (exit 0)

# The UI against the real backend: two terminals in the repo root
uv run --directory backend uvicorn vibe.api.app:app --reload --port 8000
$env:VITE_API_MODE = 'http'; npm --prefix frontend run dev    # then open http://localhost:5173
```
Upload a short video, then send these requests:
- **"blur faces"** gets a "Planned: … Not applied: the plan executor isn't connected yet, so nothing was changed." reply, and the timeline stays empty.
- **"cut it"** gets the planner's question, and the timeline doesn't change.

The status badge should read `ok`, not `mock`. Route docs are at http://localhost:8000/docs.

Verified at `ec390c1` on Windows 11 (no GPU used, no ffmpeg CLI on PATH):
- Backend `pytest`: **376 passed, 1 failed, 76 skipped.**
  - The failure is `tests/toolbox/test_toolbox_cli.py::test_writing_keeps_the_timeline_permissions`, from F-06 on main: Windows `chmod` can't keep a `0o644` mode, so it reads back as `0o666`. This branch doesn't touch the toolbox. It should pass on Linux CI; not yet seen.
  - The 76 skipped tests need the ffmpeg CLI, which isn't installed on this machine. CI installs it.
- Route tests: 30 passed. Frontend `vitest`: 12 passed.
- `ruff check`, `ruff format --check`, the spec check, `tsc -b` and `oxlint` all pass.
- jonathan re-ran the UI flow above by hand with `VITE_API_MODE=http` after the review fixes: the upload works, "blur faces" replies that it wasn't applied, and the timeline stays empty.
- **Not checked:** that the new tests fail on the old code. They don't load against the old module, which doesn't expose `PlanError` and `LlmError`. The reviewer reproduced the upload-name 500 and the path leak on the old code.
- CI: see the PR.

## Decisions made
jonathan made these on 2026-11-03. They're F-64 details, not decisions-log material; say on the PR if you disagree.
- **The plan executor isn't part of F-64.** jonathan will offer to build it as a separate card. Until it exists, the requests route plans but doesn't apply.
- **`status` field with HTTP 200** for chat outcomes (`applied`, `clarify`, `not_applied`), not an HTTP error code for "not applied". HTTP errors stay for "the request itself failed" (404, 409, 413, 422, 500, 503).
- **Nothing fake is ever written.** The first version's stub mute is gone, so a reply never claims an edit that didn't happen.
- **Storage isn't changed.** Uploads are stored as `upload<ext>`, and the cleaned original name is kept only as the project name.
- **A busy project gets 409, not a queue.** The UI sends one request at a time, and a waiting HTTP request would just hang.
- **Clarify writes nothing**, so a question never changes the timeline.

## Next steps
- Post the heads-up (API routes, the new `status` field, `python-multipart`), then get the PR re-reviewed. After merging, the Sprint 2 F-64 card is done.
- **F-06 has merged** (PR #16). `plan_request` already validates against `vibe.toolbox.TOOLS`, and `projects.py` needed no change for that. The executor can now be built on `call_tool`.
- **Follow-up cards** (to be created):
  1. **Plan executor:**
     - Run the plan's calls through `vibe.toolbox.call_tool`, filling each `{"$ref": "sN"}` with that step's result.
     - Dry-run on an in-memory `OpLog` copy first, so a failing step never leaves half a request in the timeline, then return `status: "applied"`.
     - Give `ToolContext` the duration (from `probe()` on the stored video) and the tracks (from F-08's cache). With `tracks=None`, masking requests fail closed.
     - Coordinate with pablo: it's on the F-05/F-06 next steps.
  2. **Fewer upload writes (storage, alex):**
     - The upload is written to a temp file, read again to hash it, and copied into storage.
     - Storage could move the file instead, or take a pre-computed hash.
     - The same card could add an optional `original_name`, so the real file name is kept.
  3. **Batch append in storage (alex):** write all of a request's ops at once, so a crash can't leave half a request in the timeline. The executor needs this to be crash-safe.
  4. **Frontend error detail and status:**
     - Show the server's `detail` instead of "failed: 409" (`frontend/src/api/client.ts`), including a 422's list.
     - Style `not_applied` and `clarify` replies differently in the chat.
- Tell pablo the toolbox permissions test fails on Windows.
- **Later routes** (undo/redo, hand edits, export) aren't in this card.

## Gotchas
- **The busy check only works in one process.** Running uvicorn with several workers would let two requests for one project interleave. That would need a lock on disk, or a single worker.
- **The upload size limit protects storage, not the network.** Starlette has already received the whole body when the route checks the size. A proxy or server limit would stop big uploads in transit.
- **`TimelineState` is still hand-written in `frontend/src/api/types.ts`.** The generated schema marks fields that have defaults (`masks`, `cuts`, `mutes`, `end`, …) as optional, even though the server always sends them. Using it would mean `?? {}` guards throughout the timeline view. The real fix is in the contract's JSON-schema settings, which is a shared file, so it's left for the contract owner.
- **Storage bug inherited from storage (found by pablo, F-06 handoff):** if a crash leaves a half-written last line in `timeline.jsonl`, the next `Project.append` glues onto it and the log becomes unreadable. F-64 doesn't append any more, but the executor will.
- **Projects persist under `data/` at the repo root** (gitignored). Set `VIBE_DATA_DIR` to put them somewhere else.
- **On Windows, if a repo-root `.venv` is active,** `uv` prints a `VIRTUAL_ENV … does not match` warning. It's harmless: the backend uses `backend/.venv`. Don't pass `--active` to `uv run`: it rebuilds the root `.venv`.
- The pytest `httpx2` deprecation warning is the existing one from F-01.
