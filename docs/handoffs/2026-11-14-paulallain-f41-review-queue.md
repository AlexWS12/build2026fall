---
author: paulallain
date: 2026-11-14
branch: paulallain/f41-review-queue
features: [F-41]
base: v0.1.0 @ 1642a7d
head: de869f0
status: ready for review
---

# Review queue: a list of mask spots to check, with Confirm and Cut (F-41)

**Heads-up before merging (post in the team chat).** This changes shared files: the contract (`ops.py`, `fold.py`, `state.py`, the schemas and the examples), the toolbox (`registry.py`, `tools.py`), the API routes (`projects.py`, `openapi.json`) and `frontend/src/api`. It also touches `backend/src/vibe/agent/describe.py`, which is pablo's / alex's code (one sentence for `confirm_mask`). The new decisions row (D-021) needs the team's agreement. No `schema_version` bump: it's a new op kind and an optional field.

## What changed
- **Contract:** new op `confirm_mask` (targets + time range; changes nothing in the render), folded into optional `TimelineState.reviewed`. `add_mask`, `remove_mask` and `set_effect` erase a confirmation over their range. Schemas and example states regenerated.
- **Review queue** (`backend/src/vibe/render/review.py`): built from the mask audit, nothing stored. Items are `lost`, `no_track`, `uncertain` and `gap`; `held` stretches are only counted (`held_seconds`). Contiguous stretches are merged.
- **Toolbox:** `HAND_TOOLS` registry (first tool: `confirm_mask`) and `UI_TOOLS` (= `TOOLS` + `HAND_TOOLS`). The planner only gets `TOOLS`. `describe.py` words a confirmation in plain language.
- **API:** `GET /api/projects/{id}/review` and `POST /api/projects/{id}/review/{item_id}` (`confirm` or `cut`, hand edits with source `user`; 404 when the spot has moved on). The frame index is cached for these routes. `openapi.json` and the generated TypeScript types are regenerated.
- **Frontend:** the API client, the session state and a Review panel in the editor (go to a spot, Confirm, Cut). The buttons are named by kind, target and range, and are disabled while the project is busy.
- **Cut shows the stretch length** ("Cut 4.6 s"), so one click doesn't remove several seconds unnoticed (PR #36 review, AlexWS12).

## How to check it
```
uv run --directory backend pytest -q
uv run --directory backend ruff check .
uv run --directory backend ruff format --check .
uv run --directory backend python -m vibe.api --check
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm --prefix frontend run gen:api && git diff --exit-code frontend/src/api/generated
```
Manual UI check, done on 2026-11-15 at `f8a02d4` against the real backend (stub mode, a separate data folder). Steps:
- Uploaded an 8 s generated clip through the API.
- Seeded the cache with one face track with keyframes at 0, 1, 2, 5, 6 and 7 s (like the tests' `analysed()` helper), then Analyse.
- Asked "blur faces" in the chat.

What happened:
- The Review panel listed two "Target lost, face 1" items: 0:02–0:05 and 0:07–0:08.
- Clicking a row moved the player to 0:02.
- Confirm removed that item.
- Undo brought it back, with the chat note "Undid: a review confirmation".
- Cut on the second item wrote a cut over 7.5–8.0 s and removed the item.
- There were no console errors.

The panel shows ranges in whole seconds (`formatTime`), so a stretch that starts at 7.5 s reads "0:07".

**Results** (Windows 11, run on 2026-11-14 at `866e899`):
- Backend: 1377 passed, 2 skipped, 1 failed. The failure is `tests/toolbox/test_toolbox_cli.py::test_writing_keeps_the_timeline_permissions`, a Windows-only failure (file permissions) that was already there.
- Frontend: 67 tests passed (5 files).
- `ruff check`, `ruff format --check`, `python -m vibe.api --check`, lint, typecheck and build all passed. The generated types are fresh.

## Decisions made
- **`confirm_mask` is an op** (D-021, Proposed), so a confirmation is undoable and the queue is derived from the log.
- **Confirm and Cut only.** Fixing a box by hand is F-42.
- **Held stretches are only counted**, not listed: they're short and expected.
- **Hand-only tools in a separate `HAND_TOOLS` registry**, so the planner can't invent track ids.
- `set_effect` also takes a confirmation back (a changed effect is a new thing to check).
- Contiguous stretches are merged into one item.
- The frame index is cached for the review routes. I didn't measure a speed number.

## Next steps
- **F-42:** its box correction must erase `reviewed` over the range it corrects (known limitation, also in `docs/ARCHITECTURE.md`). It can reuse `HAND_TOOLS`.
- Export's "N spots to check" could link to this queue.
- Review findings I deferred: none that I'm tracking beyond the known limitation above.

## Gotchas
- A standalone `gap` item is rare: a long gap shows as one `lost` item that absorbs it.
- Item ids change when a stretch changes (for example after another edit). The POST then answers 404, and the UI refreshes the queue and adds a chat note.
- A video that hasn't been analysed lists its masks as `no_track`; analyse it first.
- Undo is linear, so each Confirm is its own request: undo them one at a time, newest first.
