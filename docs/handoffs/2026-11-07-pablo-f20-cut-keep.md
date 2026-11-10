---
author: pablo
date: 2026-11-07
branch: pablo/f20-cut-range
features: [F-20]
base: v0.1.0 @ 751159b (rebased 2026-11-10)
head: 7467cc3
status: ready for review
---

# F-20: `keep_range` toolbox tool, a `keep` planner template, and cut/keep export tests

> **Shared files changed:** `backend/src/vibe/toolbox/registry.py` and `tools.py` gain a new tool, `keep_range`. No existing tool's name or arguments changed. **Review follow-up (2026-11-10):** `OpLog`, `storage.Project` and the `OpSink` protocol gain `append_many(ops)`, and every edit tool now writes through it. Anything passed as `ToolContext.log` must have it. The schema doesn't change (`schema_version` stays the same). alex: please review the `storage/store.py` part. `backend/tests/agent/requests_draft.json` (shared by jean and paul) gains 8 entries. Post a heads-up in the team chat before merging. paul: please add `keep_range` to the tool list in `docs/research/r1-planner-llm.md`.

## What changed
- **Tool** `keep_range(time_range)` (`toolbox/tools.py`): keeps one range and cuts everything around it, as `Cut` ops on source time: `[0, start)` first, then `[end, end of video)`. No contract change.
  - It needs the video duration (`--duration` on the CLI, `ToolContext.duration`). It refuses a missing or non-positive duration, non-finite times, a range that starts at or past the end, a range that ends past the end (no clamping), and a range that is the whole video.
  - Both cuts are written as one batch (`append_many`): the whole batch is checked against the contract, then written in one write and one fsync. If anything fails, nothing is written and the error is the same `ToolError` every edit tool gives. A bug (say a `TypeError`) is not wrapped.
  - An end up to `END_TOLERANCE_S` (0.1 s) *past* the video's end means "to the end", so "keep 0:50 to 1:00" works on a 59.97 s clip. An end before the video's end is always kept exactly, however close, so no real tail is dropped (this matters for very short clips). Further past the end is still refused, and the message names the length and says "keep from X to the end".
  - It refuses a keep that earlier cuts would leave empty (e.g. "cut 0:00 to 0:30", then "keep 0:10 to 0:20"), reading the folded timeline first.
- **Registry and CLI:** a `keep_range` `ToolSpec` (edit, `time_range` is a ref arg), a CLI example, and a "needs --duration" help line.
- **Planner templates** (`agent/templates.py`): "keep only 0:10 to 0:20", "only keep 0:05-0:08", "just keep ...", "keep the first half" plan a `keep_range` call. "only keep" and "just keep" also start a new clause after "and", and "but" followed by a verb splits a clause ("keep 0:10 to 0:20 but blur faces"). "but keep" never splits ("cut 0:10 to 0:20 but keep 0:15" may be an exception to the cut), so it's asked about. "keep everything", a bare "keep" with no time, and two keeps in one request are not planned (the last asks, since only the overlap of two keeps would stay).
- **Planner questions** (`agent/planner.py`): "keep everything except ...", "keep all but ...", "don't keep ..." and "do not keep ..." point to `cut`. Only a negation before "keep", or within five words after it and before any time or comma, counts ("keep the whole video except ..." does; "keep 0:10 to 0:20 but not the faces" doesn't). Other unreadable keeps ask for one time range.
- **Plan validation** (`agent/validate.py`): a plan with two or more `keep_range` calls is refused, which also covers plans from an LLM. The rule is one helper in `agent/plan.py` (`too_many_keeps`), shared with the templates.
- **Tests:**
  - `tests/render/test_render_export_cuts.py` (new, 5 tests): a cut to the end, a cut from 0, a cut that starts between frames, and two request-level exit checks ("keep only 0:01 to 0:02" and "keep the first half" go through `plan_request`, `call_tool` and `export` on a generated clip).
  - New cases in `test_tools.py`, `test_registry.py`, `test_toolbox_cli.py`, `test_templates.py`, `test_planner.py` and `test_validate.py`. The review follow-up adds batch tests in `test_contract.py` and `test_storage.py` (a bad op in a batch writes nothing; a batch is one write), and chat-request → planner → toolbox → timeline tests for "just keep", "... and only keep ...", "keep ... but mute ...", a 59.97 s tail, and a keep an earlier cut empties.
  - 8 new requests in `requests_draft.json` (5 clarify, 3 plan).
- **Docs:** `docs/ARCHITECTURE.md` (toolbox "Built so far") and the F-20 row in `docs/ROADMAP.md` (`in progress`).

## How to check it
```
cd backend && VIBE_REQUIRE_FFMPEG=1 uv run pytest -q
uv run ruff check && uv run ruff format --check
uv run python -m vibe.toolbox keep_range --help
uv run python -m vibe.toolbox keep_range --log timeline.jsonl --duration 60 --args '{"time_range": {"start": 10, "end": 20}}'
```
Result on 2026-11-10 after the rebase and review fixes (macOS, Python 3.12, no GPU, ffmpeg installed, `VIBE_REQUIRE_FFMPEG=1`): **941 passed**. Ruff check and format are clean.

**Not tested:**
- CI has not run. Frontend tests were not run, because no frontend files changed.
- Export of a variable-frame-rate video.
- A kept range that contains no source frame. The tool accepts it and export fails.
- A crash in the middle of the one batch write. It can still leave a partial last line, which `Project.log()` drops, so the first cut could survive alone. Error paths (contract refusal, read-only file, unreadable log) write nothing, and tests cover them.
- The frame-content checks in the export tests only catch a kept range that is off by roughly half a second or more. Frame-level accuracy rests on the frame counts and stream start times.

## Decisions made
None in the decisions log. These are for F-20 only; say on the PR if you disagree.
- **Refuse instead of clamp.** A range past the end is an error, not trimmed to the end.
- **"but keep" is asked, not split.** It may be an exception to the first edit.
- **One batch.** Both cuts land together or not at all (`append_many`), instead of the head cut first.
- **End tolerance 0.1 s, past the end only.** A deliberate, narrow exception to refuse-instead-of-clamp: an end up to about a frame past the video's end means "to the end". An end before the end is never moved.
- **Two keeps are never planned.** Each keeps only its own range, so only their overlap would be left.
- **Source-time semantics,** the same as `cut_range`.

## Next steps
- **Plan executor:** the tests run plans with a small loop in the test file. The real executor (filling `$ref` steps and running calls) is a separate card. `append_many` is there for it, so it can write a whole request's ops as one unit.
- **Cuts across calls for `cut_range`:** `keep_range` now checks the folded state, but `cut_range` doesn't. Two cuts can still cut everything; export raises `RenderError`.
- **Same fix for `cut_range`:** it has NaN/inf/duration holes that `keep_range` closes for itself.
- **alex (R-2):** `Project.append` / `append_many` glue a new op onto a partial last line left by a crash, which makes the timeline unreadable (the CLI's `--log` mode refuses such a file; `--project` doesn't). Not fixed here, since it's your call how.
- **VFR export test:** dropped from F-20; add it when there is a VFR fixture.

## Gotchas
- **Source time after an earlier cut:** "keep 0:10 to 0:20" read off the shortened preview, after an earlier cut, keeps the wrong span. The tool refuses it only when nothing would be left. Otherwise it keeps that source span, and earlier cuts stay cut.
- **17 ms lead-in:** a cut that starts between frames leaves the picture starting up to one frame after the sound. The renderer does this by design (`render/export.py:125`), and a test limits it to one frame.
- **A mute or blur on a part that `keep_range` cuts away** silently does nothing. That is harmless, and a test pins it. `UnsupportedEdit` still applies to audio filters inside a cut.
