---
author: pablo
date: 2026-11-10
branch: pablo/f20-cut-range
features: [F-20]
base: v0.1.0 @ 751159b
head: abf02a5
status: ready for review
---

# F-20 review fixes: `keep_range` writes its cuts as one batch, a narrow end tolerance, and keep parsing fixes

> **Heads-up (shared files):** `contract/log.py` (`OpLog`), `storage/store.py` (`Project`) and the toolbox's `OpSink` protocol (`toolbox/context.py`) gain `append_many(ops)`, and every edit tool now writes through it. Anything passed as `ToolContext.log` must have it. The schema doesn't change (`schema_version` stays the same). **Not posted yet:** pablo needs to post this in the team chat. alex: please review the `store.py` part.

Follows [2026-11-07-pablo-f20-cut-keep.md](2026-11-07-pablo-f20-cut-keep.md) and answers Edugre's 9 review comments on PR #27. Each review thread has a reply naming its fix commit.

## What changed
- **Rebased** onto `main` at 751159b. That brings in F-08, F-30 (`unmute`, `OpSink.state()`) and F-33. Conflicts were in `agent/templates.py` (verb list), `tests/toolbox/test_toolbox_cli.py` and `docs/ARCHITECTURE.md`; all were resolved by keeping both sides.
- **One batch per request** (01ccba0):
  - `OpLog.append_many`, `Project.append_many` and the CLI's `_TimelineFile.append_many` check the whole batch against the contract, then write it in one write and one fsync. `append` delegates to them.
  - `_write` in `toolbox/tools.py` takes a list of ops, so `keep_range`'s two cuts land together or not at all. Before this, the head cut could land without the tail cut.
- **One error mapping** (`_timeline_errors`) for both the state read and the write. A bug such as a `TypeError` now propagates as itself instead of becoming a `ToolError`.
- **Empty keeps refused:** `keep_range` reads the folded timeline and refuses a keep that earlier cuts would leave empty, writing nothing. For example, "cut 0:00 to 0:30", then "keep only 0:10 to 0:20".
- **End tolerance:** `END_TOLERANCE_S` = 0.1 s.
  - An end up to 0.1 s *past* the video's end means "to the end", so "keep 0:50 to 1:00" works on a 59.97 s clip.
  - An end *before* the end is always kept exactly.
  - Further past the end is refused, and the message gives the length and "keep from X to the end".
- **Parser** (7467cc3):
  - "just keep" parses, and "only keep" / "just keep" start a new clause after "and" or ", then".
  - "but" before an edit verb splits a clause ("keep 0:10 to 0:20 but blur faces"). "but keep" never splits.
  - The "say cut instead" hint fires only for a negation before "keep", or within five words after it, stopping at a time or comma.
  - The "more than one `keep_range`" rule is one helper, `too_many_keeps` in `agent/plan.py`, used by both the templates and `validate_plan`.
- **Docs:** `docs/ARCHITECTURE.md` (toolbox "Built so far" and "One entry point"; the planner's tools are listed instead of a stale count).
- **Tests** (891 after the rebase, 941 now):
  - Batch tests in `test_contract.py` and `test_storage.py`: a bad op in a batch writes nothing, a batch is one write, and it survives reopening.
  - `test_tools.py`: one batch, no wrapped bugs, contiguity refusal on a real project, empty keeps, the end tolerance including short clips, and chat request → `plan_request` → `call_tool` → timeline for the new phrasings.
  - `test_templates.py` and `test_planner.py`: clause splits and negation hints.
  - `test_toolbox_cli.py`: the incomplete-line refusal for a batch.
- **Cleanup:** deleted the stray `pyproject.toml`, `.python-version` (3.14) and `src/build2026fall/` from an accidental `uv init` at the repo root. They were never committed.

## How to check it
```
cd backend && VIBE_REQUIRE_FFMPEG=1 uv run pytest -q
uv run ruff check && uv run ruff format --check
uv run python -m vibe.toolbox keep_range --log timeline.jsonl --duration 59.97 --args '{"time_range": {"start": 50, "end": 60}}'
```
Results on 2026-11-10 at abf02a5:
- Local (macOS, Python 3.12, no GPU, ffmpeg installed): **941 passed**, 1 warning. Ruff check and format are clean.
- CI (`backend` workflow, run 42918275813): **941 passed**, ruff clean. GitGuardian passed.

**Not tested:**
- A crash *during* the batch write. See Gotchas.
- Frontend checks; no frontend files changed.

## Decisions made
F-20 only, nothing for the decisions log. Each one is explained in its review thread.
- **One batch instead of head cut first.** `append_many` is also what the plan executor will need, to write a whole request's ops as one unit.
- **End tolerance only past the end.** This is narrower than the review suggested. A tolerance on both sides dropped a real tail on very short clips (keep 0.01 to 0.05 on a 0.08 s clip cut nothing at the end). An end before the video's end is never moved.
- **"but keep" is asked about, not split.** "cut 0:10 to 0:20 but keep 0:15 ..." or "blur faces but keep 0:10 to 0:20" may be an exception to the first edit, not a second edit.

## Next steps
- **pablo:** post the heads-up above in the team chat, then ask Edugre to re-review PR #27.
- **alex (R-2):** review `storage/store.py`. The partial-line bug is still open (see Gotchas). It's yours to decide how to fix: truncate the partial tail before appending, or refuse like the CLI does.
- **Plan executor (unclaimed):** build it on `append_many`. The test files still run plans with a small loop.
- **`cut_range`:** it still checks per call, so two cuts can remove everything, and export then raises `RenderError`. It also has the NaN/inf/duration holes that `keep_range` closes. It could reuse `keep_range`'s state check.

## Gotchas
- **Partial last line, crash only.** A crash mid-write can leave half a line in a project's `timeline.jsonl`. `Project.log()` drops it when reading, but the next `append` / `append_many` writes right after it. That glues the two into one invalid line, and the project's timeline becomes unreadable. The CLI's `--log` mode refuses such a file; `--project` doesn't. A review pass confirmed it; it predates this branch. Error paths (contract refusal, read-only file, unreadable log) write nothing, and tests cover them.
- **Every `OpSink` needs `append_many`.** A test fake or new sink with only `append` fails the first time an edit tool writes to it.
- **"keep everything, except 0:10 to 0:20"** with a comma before "except" doesn't get the "say cut" hint, because the comma stops the search. It gets the general "which part should I keep?" instead.
