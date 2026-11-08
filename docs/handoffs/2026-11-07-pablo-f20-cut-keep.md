---
author: pablo
date: 2026-11-07
branch: pablo/f20-cut-range
features: [F-20]
base: v0.1.0 @ 6dd4cc8
head: f2ef1a3
status: ready for review
---

# F-20: `keep_range` toolbox tool, a `keep` planner template, and cut/keep export tests

> **Shared files changed:** `backend/src/vibe/toolbox/registry.py` and `tools.py` gain a new tool, `keep_range`. No existing tool's name or arguments changed, and neither did the contract (`schema_version` stays the same). `backend/tests/agent/requests_draft.json` (shared by jean and paul) gains 8 entries. Post a heads-up in the team chat before merging. paul: please add `keep_range` to the tool list in `docs/research/r1-planner-llm.md`.

## What changed
- **Tool** `keep_range(time_range)` (`toolbox/tools.py`): keeps one range and cuts everything around it, as `Cut` ops on source time: `[0, start)` first, then `[end, end of video)`. No contract change.
  - It needs the video duration (`--duration` on the CLI, `ToolContext.duration`). It refuses a missing or non-positive duration, non-finite times, a range that starts at or past the end, a range that ends past the end (no clamping), and a range that is the whole video.
  - The second cut is written after the first. If it fails, the tool raises a `ToolError` naming the cut that already landed and saying to undo the request. A test on a real `Project` shows that undo empties the cuts.
- **Registry and CLI:** a `keep_range` `ToolSpec` (edit, `time_range` is a ref arg), a CLI example, and a "needs --duration" help line.
- **Planner templates** (`agent/templates.py`): "keep only 0:10 to 0:20", "only keep 0:05-0:08", "keep the first half" plan a `keep_range` call. "keep everything", a bare "keep" with no time, and two keeps in one request are not planned (the last asks, since only the overlap of two keeps would stay).
- **Planner questions** (`agent/planner.py`): "keep everything except ..." and "don't keep ..." point to `cut`. Other unreadable keeps ask for one time range.
- **Plan validation** (`agent/validate.py`): a plan with two or more `keep_range` calls is refused, which also covers plans from an LLM.
- **Tests:**
  - `tests/render/test_render_export_cuts.py` (new, 5 tests): a cut to the end, a cut from 0, a cut that starts between frames, and two request-level exit checks ("keep only 0:01 to 0:02" and "keep the first half" go through `plan_request`, `call_tool` and `export` on a generated clip).
  - New cases in `test_tools.py`, `test_registry.py`, `test_toolbox_cli.py`, `test_templates.py`, `test_planner.py` and `test_validate.py`.
  - 8 new requests in `requests_draft.json` (5 clarify, 3 plan).
- **Docs:** `docs/ARCHITECTURE.md` (toolbox "Built so far") and the F-20 row in `docs/ROADMAP.md` (`in progress`).

## How to check it
```
cd backend && VIBE_REQUIRE_FFMPEG=1 uv run pytest -q
uv run ruff check && uv run ruff format --check
uv run python -m vibe.toolbox keep_range --help
uv run python -m vibe.toolbox keep_range --log timeline.jsonl --duration 60 --args '{"time_range": {"start": 10, "end": 20}}'
```
Result on 2026-11-07 (macOS, no GPU, ffmpeg installed): **783 passed**. Ruff check and format are clean.

**Not tested:**
- CI has not run. Frontend tests were not run, because no frontend files changed.
- Export of a variable-frame-rate video.
- A kept range that contains no source frame. The tool accepts it and export fails.
- The frame-content checks in the export tests only catch a kept range that is off by roughly half a second or more. Frame-level accuracy rests on the frame counts and stream start times.

## Decisions made
None in the decisions log. These are for F-20 only; say on the PR if you disagree.
- **Refuse instead of clamp.** A range past the end is an error, not trimmed to the end.
- **Head cut first.** If the second write fails, what stays is a cut of the head only, which can be undone as one request.
- **Two keeps are never planned.** Each keeps only its own range, so only their overlap would be left.
- **Source-time semantics,** the same as `cut_range`.

## Next steps
- **Plan executor:** the tests run plans with a small loop in the test file. The real executor (filling `$ref` steps and running calls) is a separate card.
- **Cuts across calls:** the whole-video check is per call. Two calls can still cut everything. Export raises `RenderError`, but the tool reports success. A check on the folded state would close this.
- **Same fix for `cut_range`:** it has NaN/inf/duration holes that `keep_range` closes for itself.
- **alex (R-2):** `Project.append` can leave a partial line in the timeline after a crash, and `Project.log()` drops partial lines. `keep_range` writes two ops, so it is exposed to this like every tool.
- **VFR export test:** dropped from F-20; add it when there is a VFR fixture.

## Gotchas
- **Source time after an earlier cut:** "keep 0:10 to 0:20" read off the shortened preview, after an earlier cut, keeps the wrong span. The tool does not refuse it. Earlier cuts stay cut.
- **Strict end check:** a real duration like 59.97 s refuses "keep 0:10 to 1:00", because `end` must be at most the duration.
- **17 ms lead-in:** a cut that starts between frames leaves the picture starting up to one frame after the sound. The renderer does this by design (`render/export.py:125`), and a test limits it to one frame.
- **A mute or blur on a part that `keep_range` cuts away** silently does nothing. That is harmless, and a test pins it. `UnsupportedEdit` still applies to audio filters inside a cut.
