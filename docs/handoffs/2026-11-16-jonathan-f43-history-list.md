---
author: jonathan
date: 2026-11-16
branch: jonathan/f43-history-list
features: [F-43]
base: v0.1.0 @ 73394e3
head: 00b1c65
status: ready for review
---

# The editor lists the requests that changed the video (F-43 history list)

## What changed
Continues alex's F-43 work ([his entry](2026-11-14-alex-demo-wiring-redesign.md)), which left undo, redo and the history route done and "no history list in the UI yet". This is that list. Frontend only.

- **History button in the header**, between Redo and Export (`frontend/src/components/HistoryMenu.tsx`). It opens a popover under itself.
- **The list** shows every request from `GET /{id}/history`, newest first:
  - the chat message, cut off on one line, with the full text on hover;
  - the edit kinds on a second line ("mute", "add mask"), left out when the label already names them;
  - undone requests struck through and marked "Undone", or "Undone for good" (fainter, reason on hover) when a newer request means Redo can't bring them back;
  - the request Undo would take back marked "Undo next", and the one Redo would bring back marked "Redo next" when that's certain (see Gotchas). Neither shows while the matching toolbar button is disabled.
- **Long sessions:** the popover grows to `min(70vh, 560px)`, then the list scrolls inside it. The page behind doesn't move. The list is focusable, so the keyboard can scroll it.
- **Closing:** Escape from inside the menu (focus goes back to the History button), a press outside, focus moving outside (tabbing away), or the button again. Undo and Redo keep it open, so the list can be watched while it changes. Export and everything else close it.
- **Empty project:** "No edits yet. Requests that change the video are listed here."
- **`undoTarget` / `redoTarget` / `undoneForGood`** in `frontend/src/state/session.ts`: the latest request still applied (null when the server says there is nothing to undo), what Redo would bring back when the statuses make it certain, and the undone requests Redo can no longer reach.
- **`requestLabel` / `kindsLabel`** in `frontend/src/components/history.ts`: a request with no chat text is named by what it wrote ("Hand edit: add mask, cut"), or by its id when nothing is known (the mock API's demo data).
- `Toolbar` passes `HistoryMenu` the history, its `canUndo` / `canRedo`, its Undo and Redo buttons as `keepOpen`, and its `.icon` class as `buttonClassName`.

No contract, toolbox, API route or `frontend/src/api/` change. No shared file touched.

## Review fixes
2026-11-17, from the PR review. "What changed" above already describes the result; this lists what changed and why. `origin/main` (with F-41, 3b595dc) is merged in, not rebased. Still frontend only, no shared file.
- **What keeps the list open is passed in.** `Toolbar` gives `HistoryMenu` its Undo and Redo buttons as `keepOpen` refs, instead of the menu looking for a `role="toolbar"` parent. Export and the download link now close the list.
- **"Undo next" follows the Undo button.** `HistoryList` takes `canUndo` / `canRedo` from the toolbar (they also need the project idle), so nothing is marked "next" while a request is planning, rendering or exporting.
- **Edit kinds on a second line**, under the label, on every row, so "Undo next" and undone rows keep them. Left out when the label already is the kinds ("Hand edit: cut").
- **"Redo next" and "Undone for good."** `redoTarget` and `undoneForGood` in `frontend/src/state/session.ts` work these out from the statuses, only where it's certain (see Gotchas). Undone requests Redo can no longer bring back say "Undone for good", are fainter, and explain why on hover.
- **Keyboard:** Escape closes the list only from inside the menu (not from the chat box) and puts focus back on the History button. Focus moving outside the menu and its `keepOpen` elements (tabbing away) closes it. The list is focusable, so the keyboard can scroll a long session.
- **Icon style:** the History button takes the toolbar's `.icon` class through `buttonClassName`, and the clock svg sets its own fill and stroke, so it draws as a line outside the toolbar too.
- Not done here: comment 7 (the F-43 roadmap row is alex's), asked on the PR.

## How to check it
```bash
npm run dev                    # repo root; open http://localhost:5173
npm --prefix frontend test
```
In the app: drop a short video, send "mute the first 5 seconds" and "cut 0:10 to 0:15", click the clock button in the header. Click Undo with the list open: the top row becomes "Redo next" and "Undo next" moves down one. Then send a new request: the undone row becomes "Undone for good".

Review fixes, verified on 2026-11-17 at 00b1c65 (after merging `origin/main`), macOS 27.2 arm64, Node 24.10:
- Frontend `vitest`: **108 passed** (88 on the merged branch before the fixes, so 20 new; 4 existing ones updated). `oxlint` and `tsc -b` exit 0.
- Frontend `npm --prefix frontend run build`: **passes**, 48 modules.
- The new focus and keep-open tests were checked to fail with the code broken (no `focusin` listener; `keepOpen` ignored).
- Backend `pytest`: not run. This branch still changes no backend file.
- Driven in the browser by jonathan against the real backend (`VITE_API_MODE=http npm run dev`, `VIBE_BACKEND=stub`; which browser wasn't noted): kinds on the second line and the outlined clock; Undo with the list open ("Redo next", "Undo next" moves down); two undos in a row leave "Redo next" off; "Undone for good" after Redo plus a new request; both markers gone while a request runs; Escape from the chat box leaves the list open, Escape from the list closes it and focuses the History button, tabbing to Export closes it; Export and a press outside close it. All passed.

First version, verified on 2026-11-16 at 58da8b1, Windows 11, `VIBE_BACKEND=stub`:
- Frontend `vitest`: **69 passed** (16 new). `oxlint` and `tsc -b` are clean.
- Frontend `npm --prefix frontend run build` (`tsc -b && vite build`): **passes**, 46 modules.
- Backend `pytest`: **1199 passed, 118 skipped, 1 failed.** The failure is `tests/toolbox/test_toolbox_cli.py::test_writing_keeps_the_timeline_permissions` (expects mode `0o644`, Windows reports `0o666`). This branch changes no backend file, so it is the same on `main` on this machine.
- Driven in the browser against the real backend on a generated 20 s clip, with requests sent through the API: 5 requests with one undone, then 40 requests. The list showed all 40 and scrolled, the page didn't, and Undo with the list open updated it in place. No console errors.

**Not tested:**
- Hand edits in the list in a real project: the UI can't make them yet (F-42). Unit test only.
- Narrow windows and touch. The popover is `min(340px, 86vw)` wide and anchored to the button's right edge.
- Safari and Firefox.

## Decisions made
jonathan, 2026-11-16. UI choices, none architecture-level.
- **A popover from the header, not a tab beside the chat.** It keeps chat, player and timeline visible and takes no permanent room. The list is its own component (`HistoryList`), so moving it into a tab later is small.
- **Read-only.** Undo is linear, so the list shows what Undo would act on instead of offering a request to jump back to.
- **Scroll only for long sessions**, no "show all" control.

jonathan, 2026-11-17, from the review:
- **"Redo next" is worked out in the UI, only when certain**, rather than adding fields to the history route now. That keeps this PR off the shared API files. The backend fields are a follow-up (Next steps).

## Next steps
- jonathan: push the review fixes, reply on the PR, and ask alex there about the F-43 roadmap row (comment 7).
- Follow-up, needs a team heads-up (API route, shared file): add `undo_next`, `redo_next` and `redoable` to `HistoryReply`, filled from `History.can_undo`, `History.can_redo` and `History.redoable` in `vibe/contract/fold.py`. Then regenerate the OpenAPI spec and UI types, and have `undoTarget`, `redoTarget` and `undoneForGood` read them. "Redo next" would then show after several undos in a row too.
- "Jump back to this request" (several undos in one click) is not on the card. It would need a decision on what happens to the requests jumped past.
- When F-42 lands, check that hand edits read well in the list.

## Gotchas
- **`undoTarget` is worked out in the UI** from `can_undo` and the statuses, because `HistoryReply` only says whether undo is possible, not which request it would take. It matches the backend's rule (`History.can_undo` is the last active request). If undo ever stops being linear, return the target from the route instead.
- **"Redo next" is sometimes left blank, on purpose.** The backend keeps redo as a stack (the most recently undone request comes back first), and `HistoryReply` doesn't name it. From the statuses in log order, "A, undo A, B, undo B" and "A, B, undo B, undo A" look the same (`[A undone, B undone]`) but redo different requests. So the UI marks only what is certain: undone requests before the latest applied one are "Undone for good", and a single undone request after it is "Redo next". Two or more undone after it stay plain "Undone". The backend follow-up in Next steps removes this.
- **An undone request can sit in the middle of the list**: undo one, then send a new request, and the undone one stays behind for good (redo is cleared). The list keeps log order and doesn't group them, but the row now says "Undone for good", with the reason on hover.
- **The backend permissions test fails on Windows** (above). Not from this branch.
- **Ports 5173 and 8000 may be taken by another project on your machine.** `vite.config.ts` pins the proxy to 8000, so running on other ports needs a temporary config.
