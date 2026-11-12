---
author: paul
date: 2026-11-11
branch: paul/f31-transcribe
features: [F-31, F-21]
base: v0.1.0 @ 44708a6
head: 1d15d3a
status: ready for review
---

# F-31 / F-21: `main` merged into PR #26, review fixes, and transcription on F-08's `run_analysis`

> **No shared file changes.** No contract, toolbox argument, API route or dependency changes, and the OpenAPI spec is unchanged. One value changes: `Transcript.model` is now F-08's cache key (`faster-whisper-small-int8-<8 hex>` instead of `faster-whisper-small-int8@1`). **Heads-up for the team:** transcripts cached under the old key (`…-int8@1.json`, including ones copied from a teammate) no longer replay under `stub`. Transcribe again with `local`, or copy a new cache. **Nothing is pushed yet**, and no review replies are posted.

## What changed
Follows [2026-11-10-paul-f31-review-fixes.md](2026-11-10-paul-f31-review-fixes.md).
- **Merge commit `8890448`:** `origin/main` (`44708a6`) merged in, not rebased, so the pushed branch needs no force push. Five files conflicted, and each kept both sides:
  - `agent/templates.py`: main's `_KEEP` (F-20) and this branch's `_SPOKEN`.
  - `toolbox/tools.py` and `toolbox/__main__.py`: the imports from both sides, plus main's `--duration` help with this branch's `--transcript` flag.
  - `docs/ARCHITECTURE.md`: main's Toolbox lines, with `find_speech` and the transcript added.
  - `docs/ROADMAP.md`: main's F-20 and F-30 rows, and this branch's F-21 and F-31 rows.
- **Review fixes (`e298a1b`):** a code review of the merged branch found three real problems. Each one has a test that failed before its fix:
  - **Transcribe is disabled during an upload (`App.tsx`).** On Nov 10 the upload was disabled during a transcription, but not the other way round. Clicking Transcribe mid-upload could put the old video's transcript under the new one.
  - **Planning loads no media code (`toolbox/context.py`).** After the merge, `import vibe.agent` pulled in `av`, `numpy` and `vibe.modules` just for the `Transcript` type. It's now imported for type checkers only. A test runs a fresh Python and checks that `vibe.agent` and `vibe.toolbox` load none of them.
  - **More time words (`agent/templates.py`).** `middle`, `third(s)` and `end` were added, so "mute where he says hello in the middle third" or "…until the end" is asked about. Before, the whole phrase was searched for as speech.
  - **Two tests the review asked for:** a word at the very end of the clip goes through `find_speech`, then `mute`, and F-12's past-the-end check doesn't refuse it. And `POST /transcript` on a busy project is a 409, and the no-audio 400 releases the busy lock.
- **Transcription on F-08 (`1d15d3a`, `modules/transcribe.py`):** eduardo's F-08 handoff asked for this.
  - **New class `TranscriptAnalysis(Analysis)`**, registered with module and kind `transcript`. Its only setting is the Whisper size, matching `__init__`, so the model server can rebuild it. Its `compute` runs `extract_audio` and then Whisper, always locally (on this machine, or on the model server for `remote`).
  - **`transcribe_video` goes through `run_analysis`.** That removes the private `_atomic_write` import (Edugre's comment #11) and this module's own stub and cache code.
  - **`remote` works now:** the model server needs faster-whisper installed, and the result is cached here.
  - **`TranscribeError` is now a `ModuleError`,** and `POST /transcript` answers 503 for any `ModuleError`.
  - **A corrupt or wrong-format cached transcript is an error, never replaced.** This undoes the Nov 10 "unreadable cache is a miss" fix, to match F-08. The error names the file and says to delete it. The route's log shows it, and the user sees the usual 503.
  - ARCHITECTURE's "AI modules → Audio" line is updated.

## How to check it
Verified on 2026-11-11 on Windows 11 Home (x86_64), CPU only, Python 3.12.2, uv 0.12.11, Node 24.12.0, ffmpeg 8.1.1:
- **Backend: 1073 passed, 2 skipped, 1 failed.**
  - The failure is `test_toolbox_cli.py::test_writing_keeps_the_timeline_permissions` (chmod on Windows), which fails on `main` too.
  - The two skipped tests need POSIX permissions.
  - Ruff check and format pass, and `python -m vibe.api --check` passes.
  - After the merge, `gen:api` left `openapi.json` and `schema.ts` unchanged.
- **New F-08 tests (`tests/modules/test_transcribe.py`):**
  - `remote` runs against an in-process model server with a fake Whisper, and the cached result then replays with `stub`;
  - the server can rebuild `TranscriptAnalysis` from its settings (`make_analysis`);
  - a corrupt or wrong-format cache file raises an error naming it, and is kept. `test_api_projects.py` checks the same as a 503.
- **Frontend: 17 tests pass**, and oxlint and `tsc -b` are clean. They were run after the review fixes; the F-08 commit changes no frontend files.
- **Not tested:**
  - the app with a real clip, and real Whisper (faster-whisper isn't installed on this machine);
  - a model server on another machine;
  - CI (it runs on the PR), the GPU, and long clips.

## Decisions made
paul made these on 2026-11-11. They're F-31 details, not decisions-log rows.
- **Merge `main` in rather than rebase,** because the branch is already pushed and under review.
- **The F-08 move happens in this PR,** not a follow-up.
- **A corrupt cached transcript is an error naming the file, not a miss,** to match every other F-08 cache. It's never deleted or replaced automatically.

## Next steps
- **Push** with a plain `git push`, no force. The merge and both new commits are local only.
- **Reply to the review on PR #26:** Edugre's 10 inline comments and Codex's P2 comment. Draft the replies, get paul's OK on the text, then post them in each thread. After that, mark the PR ready for review.
- **Minor review items, left for later.** They all fail safe (they ask, refuse or error, never edit the wrong thing):
  - A request with two quoted phrases (`'hola' and 'adios'`) is searched as one phrase.
  - Curly quotes don't count as quoting.
  - A spoken "thank you" is stripped as politeness.
  - A word said after the video ends gives a confusing error.
  - A phrase across two segments spans the silence between them.
  - The "said N times" message lists only start times, and it counts words an earlier cut removed.
  - A bad `VIBE_BACKEND` value is a `ValueError`, which the route reports as "couldn't read the video's audio" (500). A model-download failure is a bare 500.
  - `find_speech`'s description doesn't say it matches exact words, not topics. That matters once the LLM planner (R-1) uses it.

## Gotchas
- **Old transcript caches are orphaned.** Delete `cache/<sha>/transcript/faster-whisper-*-int8@1.json` (the old key format).
  - `transcribe_video` ignores them: `local` transcribes again, and `stub`'s error lists the old key under "Cached under other settings".
  - With both keys in the folder, F-08's "whatever is cached" lookup (`replay`) raises `AmbiguousAnalysis`.
- **Under `remote`, a video with no audio is a 503, not a 400.** The model server has no handler for video errors, so it answers 500, and the client turns that into a `RemoteError`. From reading the code; not run.
- **Whisper weights download to the Hugging Face cache,** not F-08's `models_dir()`. Pass `download_root` if they should live there.
- **On Windows:**
  - `npm install` with npm 11 rewrites `frontend/package-lock.json` (peer-flag metadata only); `git restore` it.
  - `gen:api` can leave `schema.ts` showing as modified when only its line endings changed.
  - `npm --prefix frontend exec tsc -- -b` can't find `tsconfig.json`; use `npm --prefix frontend run typecheck`.
  - **The route's docstring is published in `openapi.json`,** so editing it makes `vibe.api --check` fail. Put route notes in comments, or regenerate both generated files.
