---
author: paul
date: 2026-11-05
branch: paul/f31-transcribe
features: [F-31, F-21]
base: v0.1.0 @ 85eef1c
head: 6d253e7
status: ready for review
---

# F-31 transcription with word timestamps, in the app; F-21 "mute / cut where he says X" (planned, not applied yet)

> **Heads-up: shared files.**
> - **Toolbox:** a new `find_speech` tool, and `ToolContext.transcript`.
> - **API routes:** `POST /api/projects/{id}/transcript`, with the OpenAPI spec and the UI types regenerated.
> - **`frontend/src/api`:** `ApiClient.transcribe`.
>
> It also changes `agent/templates.py` (pablo's F-05 code). **No dependency files change:** faster-whisper is installed by hand, like F-10's model packages.

## What changed
- **`backend/src/vibe/modules/transcribe.py`** (F-31):
  - **API:**
    - `transcribe(samples, rate)` takes mono 16 kHz samples, so a per-chunk caller can reuse it later.
    - `transcribe_video(storage, video)` and `cached_transcript(storage, video)` work on stored videos.
  - **Output:** a `Transcript` holds `Segment`s, each with `Word`s. Times are source seconds (D-013), so a word's `start`/`end` goes straight into a `TimeRange`.
  - **Cache:** results go to `cache/<sha256>/transcript/faster-whisper-<model>-int8@1.json`. The `@1` is our format version; bump it when the format changes.
  - **Backends:**
    - `local` runs faster-whisper (MIT) on the **CPU**, in int8.
    - `stub` only replays a cached transcript, and raises without one. It never returns an empty "nobody spoke" transcript.
    - `remote` isn't built and raises.
  - **CLI:** `uv run python -m vibe.modules.transcribe CLIP [--model small]`. It prints UTF-8, so piping it on Windows doesn't crash on other languages.
- **`find_speech` query tool** (F-21, in `toolbox/tools.py` and `registry.py`):
  - **Result:** when a word or phrase is said, as a `time_range` padded by `SPEECH_PAD = 0.15` s on each side, so `mute` and `cut_range` can `$ref` it.
  - **Matching:** ignores case, accents and punctuation ("que" finds "¿Qué"), and can span two segments.
  - **Errors (`ToolError`, nothing written):**
    - Said more than once: it lists the times and asks for a range instead of guessing.
    - Not said at all.
    - No transcript yet.
  - **CLI:** `--transcript FILE`.
- **Planner templates** (`agent/templates.py`): "mute where he says X", "mute the word X" and "cut the part where she said X" now plan `find_speech` → `mute` / `cut_range`. English only. Topics ("where she talks about the weather") still go to the LLM or a clarify.
- **API** (`api/projects.py`): `POST /api/projects/{id}/transcript` returns the transcript.
  - **Busy:** the project counts as busy (409) while Whisper runs.
  - **Errors** (fixed messages, as in F-64):
    - 503 when the server can't transcribe (`stub` with no cache, or faster-whisper missing).
    - 400 for a video with no audio.
    - 500 for unreadable audio.
- **UI:** a **Transcript** panel with a Transcribe button lists the segments with their times (`0:07.08  Hola`). The mock client returns a canned `(mock)` transcript.
- **Docs:**
  - ARCHITECTURE: `find_speech` under Toolbox, and transcription under AI modules → Audio.
  - ROADMAP: F-31 and F-21 marked `in progress`.
- **Tests:**
  - `tests/modules/test_transcribe.py`: 8 tests, with a fake Whisper.
  - `tests/toolbox/test_find_speech.py`.
  - `tests/agent/test_spoken_templates.py`.
  - 4 transcript-route tests at the end of `tests/test_api_projects.py`.
  - `find_speech` added to the shared tool-name guard in `test_registry.py`.
  - 2 frontend tests in `App.test.tsx`.

## How to check it
```bash
cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python -m vibe.api --check
cd .. && npm --prefix frontend test && npm run lint:frontend
```
Real transcription (needs `uv pip install faster-whisper` in `backend/`). Use Git Bash syntax, or `$env:VAR = '...'` in PowerShell:
```bash
VIBE_BACKEND=local VITE_API_MODE=http npm run dev   # badge should read "Backend: ok (local)"
```
Upload a clip with speech and press **Transcribe**. Then try "mute where he says <a word from it>" in the chat. It should reply `Planned: find_speech … ; mute {"$ref": "s1"}. Not applied`.

Verified on 2026-11-05 on Windows 11, CPU only (the RTX 4060 Ti wasn't used), Python 3.12.10, uv 0.11.9, Node 24.15.0, after rebasing on `85eef1c`:
- Backend: **567 passed, 104 skipped, 1 failed.**
  - The failure is `tests/toolbox/test_toolbox_cli.py::test_writing_keeps_the_timeline_permissions`, F-06's Windows `chmod` failure, which also fails on unchanged `main` here.
  - The skipped tests need the ffmpeg CLI, which isn't installed here.
- Ruff and the spec check pass.
- Frontend: 14 tests pass, and oxlint, `tsc -b` and `vite build` pass. Running `gen:api` again leaves the types unchanged.
- **The real backend ran on one clip:** an 11.4 s Spanish phone video, with the `small` model.
  - The transcript gave 3 segments in Spanish, with a time on every word.
  - A `stub` replay matched it.
  - Through the API and the UI: upload 201, transcript 200. "mute where he says hola" and "cut the part where he says el guardo" planned `find_speech` → edit and replied `not_applied`.
- **Not measured:** accuracy, speed, and whether 0.15 s of padding is enough. F-09 doesn't score speech yet.
- **Not tried:** long clips, the GPU, and CI (it runs on the PR).

## Decisions made
paul made these on 2026-11-05. They're F-31/F-21 details, not decisions-log rows; say on the PR if you disagree.
- **faster-whisper is the first `local` backend, and it's tentative.** There's no decision row until F-09 can score speech. It runs on the CPU in int8 so every teammate can run it (tier 0). A GPU option comes when someone needs it.
- **It isn't in `pyproject.toml`.** It's installed by hand, like F-10's packages, so CI and CPU laptops never need it. Adding it as an optional group is a shared-file change for later.
- **`stub` only replays the cache.** An empty transcript would make "mute where he says X" fail open.
- **Several matches ask, never guess.** Muting every match needs `mute` to take a list of ranges, which is a shared-file change.
- **The plan executor is left to jonathan,** who offered it as the F-64 follow-up. Speech edits plan correctly but don't apply until it exists.
- **The transcript is ordered with a button,** not on upload (that would make uploads wait for Whisper) and not from the chat (that would need the executor).

## Next steps
- **Executor (jonathan):** build `ToolContext(..., transcript=cached_transcript(storage, project.info.video))` alongside the duration and tracks, so `find_speech` → `mute` / `cut_range` apply. Until then F-21 stays plan-only.
- **F-09 (jean):** speech scoring against `SpeechSegment` (the same `start` / `end` / `text` fields). That's how to tune `SPEECH_PAD` and choose a model size.
- **R-1 (paul):** give the LLM planner transcript excerpts, which D-017 allows because they count as text. That covers topics, other languages and "every time he says X".
- **F-32:** add a `speaker` to `Segment`. `Mute.speaker` already exists in the contract.
- **Later:**
  - Run long transcriptions in the background with progress. Today the request just waits.
  - Show the server's error `detail` in the chat instead of "failed: 409" (F-64 follow-up #4).
  - Click a word to seek the player.

## Gotchas
- **`uv sync` removes faster-whisper.** Reinstall it with `uv pip install faster-whisper`, or use `uv sync --inexact`. `uv run` keeps it.
- **The first transcription downloads the model** from Hugging Face: about 480 MB for `small`, about 75 MB for `tiny`. That's network on first use only, and never on the per-frame path.
- **Mock mode:** if the panel shows `(mock) Hello there.`, the UI is on the mock client. Set `VITE_API_MODE=http`; `frontend/.env.local` works and is gitignored. A 503 means the backend isn't in `VIBE_BACKEND=local` mode, which has no file option: set it in the shell (Git Bash: `VIBE_BACKEND=local npm run dev`).
- **Don't set `VIBE_BACKEND=local` permanently.** `test_smoke.py` expects the default `stub`.
- **A chat request sent while a transcription runs gets 409**, because they share the project's busy lock.
- **The status badge checks `/api/health` only once, at page load.** If the page loads before uvicorn is up, it says "unreachable" until you refresh. That's F-07 behavior.
- **`transcribe.py` imports storage's private `_atomic_write`** (marked `shortcut:`). Switch when storage exports a public one; jean's F-09 review nit #9 asks for the same.
