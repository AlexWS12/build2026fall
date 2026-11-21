---
author: pablo
date: 2026-11-21
branch: pablo/f44-preview-before-apply
features: [F-44]
base: v0.4.0 @ 97dbb80
head: 199e35d
status: ready for review
---

> **Shared files changed:** API routes (`/preview`, `/preview/accept`, `/preview/reject`), `openapi.json` and the generated UI types, `ChatReply.status` and the chat record status (new value `preview`), and the storage layer (`pending.json`, `Project.pending()` / `set_pending()` / `clear_pending()`). The edit-timeline contract is **not** changed (no `schema_version` bump). Post a team heads-up before merging.

# F-44: a cut or keep is previewed first, and only applied when a person accepts it

## What changed
- **Which plans wait:** a plan with `cut_range` or `keep_range` (`backend/src/vibe/agent/heavy.py`). Every other edit, including mute, still applies at once. Inpainting is not built yet; add its tool to `HEAVY_TOOLS` when it exists.
- **Dry run:** `execute_plan` / `execute_request` take `commit=False` and return the ops that would be written without writing them.
- **Pending proposal:** saved as `pending.json` in the project folder (`PendingPreview`, atomic write, one per project). It is not an op, so undo and the contract are untouched.
- **Routes** (`backend/src/vibe/api/projects.py`, shapes in `api/previews.py`): `POST /requests` replies `status: "preview"` with `preview.{duration_before, duration_after, removed}`; `GET /preview` gives the state it would make (recomputed from the timeline each time) or `problem` when it can't run any more; `POST /preview/accept` runs the plan for real; `POST /preview/reject` discards it.
- **Settled requests:** chat keeps one line per request (the latest record wins in `/messages` and `/history`).
- **While one waits:** `POST /requests`, `/undo`, `/redo` and `/review/{item}` answer 409. The UI disables the same, plus analyse.
- **UI:** `PreviewBar` (Apply / Discard) in the chat panel; the player and timeline show the proposed state (`frontend/src/state/session.ts`, `useSession.ts`, `App.tsx`, `ChatPanel.tsx`).
- **Review fixes (independent code review, same branch):** accept is idempotent if cleanup fails after the ops are written (a preview whose request id is already in the log counts as applied); a damaged or outdated saved plan shows as a `problem` and can be discarded instead of making the project unopenable; a cut already in place is not proposed; a cut that grows an existing one reports only the new footage as removed.
- Docs: "Preview before apply as built (F-44)" in `docs/ARCHITECTURE.md`.

## How to check it
```bash
npm install
npm test          # backend 1599 passed, frontend 122 passed (run 2026-11-21 after rebasing on main at 97dbb80, macOS, no GPU)
npm run lint
npm run dev       # UI :5173, API :8000; use the real backend, not the mock
```
In the UI: upload a clip, type `cut 0:02 to 0:04`. The reply says nothing is changed yet, the player skips the range, and Apply / Discard appear. Apply writes the cut (Undo works); Discard changes nothing; the request box is disabled until you choose.

Over HTTP (verified by hand on a 10 s clip against a throwaway backend, 2026-11-21): ask → `status: preview`, state still empty; a second request → 409; `GET /preview` → candidate cut; accept → `applied`; export → 8.0 s; undo → cuts empty.

Tests: `backend/tests/test_api_preview.py` (the real request → plan → pending file → accept/reject → timeline path, plus failed writes, damaged plans, restart), `tests/agent/test_execute.py` (`commit=False`), `tests/test_storage.py` (pending file), `frontend/src/state/session.test.ts` and `App.test.tsx`.

## Decisions made
- **Staged outside the contract.** The proposal is a saved plan, not a "pending" op state, so no `schema_version` bump and the planner, toolbox and undo are unchanged. If the team later wants previews of several steps or of non-cut edits, moving to staged ops in the contract is the alternative; I did not add a decisions-log row, so say if it should have one.
- One preview per project at a time; a new request waits (409) rather than replacing it.
- Existing API tests use a helper (`ask`) that accepts the preview, so their assertions are unchanged; the two-step flow is tested on its own.

## Next steps
- Mute / unmute preview ("preview the result" in `docs/ARCHITECTURE.md`) is not gated; it needs the renderer's audio filters (nickolas's `f33b-render-audio-filters`).
- Inpainting preview, once object removal exists: add its tool to `HEAVY_TOOLS` and show the inpainted frames, not just the state.
- Claim the F-44 row in `docs/ROADMAP.md` once Sprint 5 is planned (I did not edit it).

## Gotchas
- **Not checked in a browser.** The UI is covered by component tests and an HTTP run, not by looking at it (the Chrome extension didn't respond when I tried). Someone should click through Apply / Discard once.
- The toolbox CLI with `--project` can still write while a preview waits; only the HTTP routes refuse.
- `GET /preview` takes no lock, so one that overlaps an Apply in progress can briefly show a clean-looking preview. The accept path is safe (checked above).
- If Apply succeeds but the UI's refresh afterwards fails, it says "failed" until the next click, though the edit is applied.
- Teammates on older branches skip chat lines with status `preview` as unreadable.
- The untracked `.python-version`, `pyproject.toml`, `src/`, `uv.lock` at the repo root are not part of this work (they look like an accidental `uv init`).
