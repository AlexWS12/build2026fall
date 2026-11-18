---
author: alex
date: 2026-11-14
branch: alex/demo-wiring-redesign
features: [F-40, F-43, plan executor, export route]
base: v0.1.0 @ 44708a6, merged with main @ f14dbf1
head: 7f3331c
status: ready for review
---

# The app edits for real from chat, with undo, export, a live preview and a new look

> **Heads-up (shared files).** This branch changes:
> - **API routes** (`api/projects.py`, `openapi.json`, generated UI types): eleven new routes, and `POST .../requests` now applies the plan. `ChatReply` gains `steps`.
> - **Toolbox** (`registry.py`, `tools.py`, `context.py`, `__main__.py`): new `undo` and `redo` tools. No existing tool name or argument changed. `OpSink` gains `history()`, so every sink (test fakes too) needs it.
> - **Storage** (`store.py`): a new `chat.jsonl` per project. Additive, no `storage_version` bump.
> - **UI side of the API** (`frontend/src/api/`): the `ApiClient` has ten more methods, and `VITE_API_MODE` now defaults to automatic.
>
> No contract change, so `schema_version` stays 1. **Not posted yet:** alex needs to post this in the team chat.

## What changed
- **Plan executor** (`agent/execute.py`):
  - `execute_plan` runs a plan through `call_tool`, fills each `$ref`, and dry-runs on a copy of the op log. Only if every step succeeds are the ops written, in one `append_many` batch.
  - `execute_request` adds what's known about the stored video: duration, cached tracks plus hand-drawn ones, and the cached transcript.
  - A step the toolbox refuses is a normal outcome (`not_applied`, with the reason). Nothing is written.
- **Plain-words replies** (`agent/describe.py`): "Muted 0:00–0:05.", "Blurred 2 faces (0:00–0:10)." Each reply also carries its steps (tool, resolved arguments, result).
- **`undo` / `redo` tools**, and "undo" / "redo" as whole chat requests. "undo the blur" gets a question, because undo is linear.
- **API routes** under `/api/projects`:
  - `GET ""` (list), `GET /{id}` (info, video size and duration, `analysed`), `GET /{id}/video` (Range works);
  - `GET /{id}/history`, `POST /{id}/undo`, `POST /{id}/redo` (400 when there's nothing to undo or redo);
  - `GET /{id}/tracks`, `POST /{id}/analysis`;
  - `POST /{id}/exports`, `GET /{id}/exports/{name}`;
  - `GET /{id}/messages` (the stored conversation).
- **UI state** (`frontend/src/state/session.ts`): the session is one explicit state machine. A project does one thing at a time (requesting, undoing, redoing, analysing, transcribing, exporting), like the server's 409. This replaces the separate `uploading` / `thinking` / `transcribing` flags. A new slow action is a new `Activity` kind there.
- **UI features:**
  - replies show what was done, with the steps behind a disclosure;
  - undo, redo and export in the header; a finished export is marked stale once the timeline changes;
  - the player previews the edits as it plays (masks drawn, cuts skipped, mutes silent), with a Preview / Original switch;
  - the timeline has a playhead, seeks on click, names mask lanes by what they are ("face 1"), and links a reply to its spans on hover;
  - a reload reopens the project and its conversation (`#/p/<id>` in the address).
- **Look:** a landing page with the drop zone and recent projects, the Lanes logo and favicon, and a warmer dark theme for the editor. The arrangement is the F-07 shell's: player and timeline left, chat right. The upload panel is gone; the landing page and header replace it. Design options are on a private canvas; ask alex for the link.
- **Merged `origin/main` at f14dbf1** (F-31 transcription, F-11 mask effects), no rebase, so no force-push.
  - `find_speech` plans now run: "mute where he says hola" is applied when a transcript is cached.
  - `execute_request` imports media code inside the function, so `import vibe.agent` still loads no `av` or `numpy` (paul's test checks this).
  - paul's four App tests for the transcript panel were adapted to the new layout. Two changed meaning: "chat and upload wait" now checks chat and leaving the project, and "transcribe waits while a new upload runs" became "while a chat request runs", because a new upload now starts from the landing page.

## How to check it
```bash
npm run dev          # repo root: backend + UI; open http://localhost:5173
npm test             # both sides
```
In the app: drop a short video, then send "mute the first 5 seconds", "cut 0:10 to 0:15", "undo", click Redo, click Export, download the file. Reload: the project and conversation come back.

Verified on 2026-11-14 at 7f3331c, Linux, no GPU, `VIBE_BACKEND=stub`:
- Backend `pytest`: **1316 passed, 2 skipped.** `ruff check`, `ruff format --check` and the spec check are clean.
- Frontend `vitest`: **53 passed.** `oxlint`, `tsc -b` and `vite build` are clean.
- Driven in headless Chromium against the real backend on a generated 20 s clip: upload, mute, cut, undo by chat, redo, export (the MP4 downloads, 0:15), reload, back to the landing page. No console errors other than the expected 503 below.
- Blur: checked with tracks cached by the `stub` detector (`python -m vibe.modules.vision analyze <video> -d stub:color=red,label=face`). Reply, lanes, preview masks and export all worked.

**Not tested:**
- Any real detector, GPU, or the `local` / `remote` backends.
- Real transcription (in stub mode with nothing cached the Transcribe button gives the 503, as designed).
- Windows, Safari, Firefox. The player's sizing uses container query units.
- More than one server worker.

## Decisions made
alex, 2026-11-14. None is in the decisions log yet; say on the PR if one should be.
- **All-or-nothing requests.** A plan that fails at any step writes nothing.
- **`/analysis` only replays the cache.** No default detector is decided (D-015 is still Proposed), and wiring one would lock that in. It returns 503 when nothing is cached.
- **`VITE_API_MODE` defaults to automatic:** the real backend when `/api/health` answers, demo data otherwise. The badge says which. `http` and `mock` still force one.
- **The browser preview is approximate, and says so.** The export is the exact render.
- **Dark only for now.** The light theme was dropped with the redesign.
- **Fonts come from Google Fonts**, with system fallbacks, so the UI still works offline.

## Next steps
- alex: post the heads-up, get a review.
- **Analysis from the UI** needs a default detector (F-10b), then `POST /analysis` can run it. Until then, analyse with the vision CLI before a demo.
- **Denoise:** a request replies `applied`, but export refuses audio filters until F-33b (`nickolas/f33b-render-audio-filters`) merges. Keep it out of demos until then.
- **F-43:** the history route exists; the UI has no history list yet.
- **Clarify options:** the planner's questions carry no suggested answers, so the UI can't offer one-click replies.
- **`docs/ARCHITECTURE.md`** ("Toolbox", "Agent") still describes the API as plan-only. Not edited here.
- **Known gaps, found while mapping state, not fixed:**
  - a planner LLM failure reaches the user as a clarify question, so the API's 503 for `LlmError` is unreachable;
  - `ToolError` has no reason code, so the UI can't tell "not analysed" from "past the end";
  - the torn-last-line bug in `timeline.jsonl` (pablo's F-20 handoff) is still open, and `chat.jsonl` appends the same way.

## Gotchas
- **Every `OpSink` needs `history()` now**, as well as `append_many`.
- **`chat.jsonl` isn't written for requests that end in a 5xx.**
- **`/tracks` returns hand-drawn tracks even when `analysed` is false.**
- **An unusable analysis cache** (several track sets and no default) gives 503 on `/tracks`, `/analysis` and `/exports`, and a blur request gets `not_applied`.
- **The stub detector's boxes can cover the whole frame**, so a blur preview on a test clip looks like a full-frame blur. That's the test data, not a bug.
