---
author: paul
date: 2026-11-10
branch: paul/f31-transcribe
features: [F-31, F-21]
base: v0.1.0 @ 85eef1c
head: 6803957
status: ready for review
---

# F-31 / F-21: fixes for Edugre's review on PR #26

> **No shared file changes.** No contract, toolbox argument, API route or dependency changes. `find_speech`'s arguments are unchanged; only its matching and an error message changed. The branch is **not rebased** on `main` yet (21 commits behind; see Next steps).

## What changed
Follows [2026-11-05-paul-f31-transcribe.md](2026-11-05-paul-f31-transcribe.md). One commit, `6803957`, covering 9 of Edugre's 10 comments on PR #26:
- **UI (`App.tsx`, `UploadPanel.tsx`):**
  - The chat input is disabled while a transcription runs. The server holds the project's busy lock, so a message sent then got a 409.
  - Upload is disabled too, so a new project can't receive the old video's transcript (Codex flagged the same issue). `UploadPanel` gets an optional `disabled` prop, so it doesn't say "Uploading…".
- **Transcript clock (`TranscriptPanel.tsx`):** times are rounded to hundredths before the minutes are split off, so 59.997 shows `1:00.00` instead of `0:60.00`.
- **Templates (`agent/templates.py`):**
  - "where he says X" with a time word in X (first, last, half, whole, entire, minute(s), second(s), or any digit) returns None, so the planner asks or hands it to the LLM. Before, the time was searched for as speech ("goodbye in the last minute"). Quoting the words skips the check: `mute where he says 'last call'`.
  - The bare "the word(s) X" form only works for mute now. "remove the words on the screen" no longer plans `find_speech` → `cut_range`.
- **`find_speech` (`toolbox/tools.py`):**
  - It matches whole Whisper words only, so "don" doesn't match "don't" and "it" doesn't match "it's".
  - The "said N times" error says to edit by time (`mute 0:12 to 0:13`) instead of "give a time range", which the tool can't take.
- **Transcribe (`modules/transcribe.py`, `api/projects.py`):**
  - An unreadable cache file now counts as a miss. It's logged, `local` replaces it, and `stub` gives 503. Before, every retry was a bare 500.
  - A `ValueError` while reading audio (a stream with no frames) is a logged 500 with the usual detail.
  - `WhisperModel` is cached with `lru_cache` (`_whisper`), so it loads once per size.
- **Tests:** added to `test_spoken_templates.py`, `test_find_speech.py`, `test_transcribe.py`, `test_api_projects.py` and `App.test.tsx`.
- **Not changed: the private `_atomic_write` import (comment #11).** `main` now has a public `vibe.modules.cache.write_json` (F-08). The move to `Analysis` / `run_analysis` after the rebase removes the import.

## How to check it
Verified on 2026-11-10 on macOS 27.2 (Apple Silicon, arm64), CPU only, Python 3.12.13, uv 0.5.9, Node 24.19.0:
- Backend: **580 passed, 103 skipped.** The skipped tests need the ffmpeg CLI, which isn't installed here. The new API tests don't need it and ran. Ruff check and format pass.
- Frontend: **16 tests pass**, and oxlint and `tsc -b` are clean.
- **In the app**, with `VIBE_BACKEND=local npm run dev` and one 720p clip of mine: paul reported it working. Which of the checks below were tried wasn't written down.
  1. Upload a clip and click **Transcribe**. The chat box and the upload input stay disabled until it finishes.
  2. `mute where he says <a word in the transcript>` plans `find_speech` → `mute` and replies `not_applied`.
  3. `cut the part where she said <word> in the last minute` gets a question, not a plan.
  4. `remove the words on the screen` plans no cut.
- **Not tested:** CI (it runs on the PR), the GPU, and long clips.

## Decisions made
paul made these on 2026-11-10. They're F-21 details, not decisions-log rows.
- **No `within` time range on `find_speech` for now.** It would change the toolbox's arguments (a shared file). A request with a time in the spoken words is asked about instead.
- **Time words come from a list, not a parse.** `parse_time_phrase` doesn't read "the last minute", so a parse would miss Edugre's own example. The cost: "where he says 3" or "says first" is asked about too, unless quoted. It's marked with a `ponytail:` comment in `templates.py`; add `within` if it turns out too strict.
- **While transcribing, the upload is blocked** rather than dropping late results by project id. It's one prop, and it matches the server's one-request-per-project lock.

## Next steps
- **Push `6803957`** and reply to each of Edugre's comments on PR #26 (nothing is pushed or posted yet).
- **Rebase on `origin/main`** (21 commits behind). Expect conflicts in `agent/templates.py` (F-30's "mute it" change), the toolbox files (`unmute`), and `ARCHITECTURE.md` / `ROADMAP.md`.
- **After the rebase,** move `transcribe_video` under F-08's `run_analysis` as an `Analysis` with kind `transcript` (eduardo's F-08 handoff). Its `write_json` replaces `_atomic_write`.
- Earlier next steps still stand: the executor (jonathan), F-09 speech scoring (jean), and R-1 transcript excerpts for the planner (paul).

## Gotchas
- **While transcribing, the disabled chat box still says "Upload a video first"** (its placeholder only knows about "no project"). This is cosmetic.
- **Running the app on a fresh machine** needs `npm install` at the root (for `concurrently`), `npm ci` in `frontend/`, and `uv pip install faster-whisper` in `backend/`. Without the first, `npm run dev` fails with `concurrently: command not found`.
