---
author: pablo
date: 2026-10-28
branch: pablo/f06-toolbox
features: [F-06]
base: v0.1.0 @ 21ee75c
head: 68d3abf
status: ready for review
---

# F-06 toolbox: typed tools, registry, stub tools and a CLI (the planner's five tools)

> **Heads-up:** this adds the toolbox's **tool names and arguments**, which are a shared file (`HANDOFF.md`, "Shared files"). Post in the team chat before merging. The names and arguments are the same ones the planner already used (F-05), so nothing that calls them changes. The contract and storage aren't touched.

## What changed
- **`backend/src/vibe/toolbox/`:**
  - `registry.py`: `TOOLS` (read-only, by name) and `call_tool(name, args, ctx)`. `call_tool` validates the arguments against the tool's model and runs the tool. An unknown tool, bad arguments, or an edit the timeline refuses all raise `ToolError` and write nothing.
  - `tools.py`: the five tools, each with an argument model and a result model:
    - `find_objects(label)` → `{track_ids, count}`. **Stub:** it filters the tracks in the context. Real detection comes from F-10 via F-08.
    - `resolve_time(phrase)` → `{time_range}`. **Real:** it computes "first half", "second half", "first N" and "last N" (N needs a unit or clock time) from the video duration. It rejects a missing or non-finite duration, empty ranges, and a span as long as the video or longer (so "cut the last 2 minutes" of a 1-minute clip asks instead of cutting everything).
    - `add_mask`, `cut_range`, `mute` → `{op_ids}`. Each builds the contract op (`AddMask` / `Cut` / `Mute`) with the context's `request_id` and `source`, then appends it. `add_mask` drops repeated targets.
  - `context.py`: `ToolContext(request_id, source, log, duration, tracks)`. `log` is anything with `append(op)` (`OpSink`): a `contract.OpLog` in memory, or a `storage.Project` on disk.
  - `spec.py`: `ToolSpec` (name, kind, description, args/result models, `returns`, `ref_args` / `ref_required` for the planner's `$ref`s, and `run`) and `ToolError`.
  - `timecodes.py`: `parse_timestamp`, moved here from the agent so the toolbox never imports the agent.
  - `__main__.py`: the CLI, described under "How to check it". Edit tools rewrite the timeline file atomically (temp file + rename) and keep its permissions. Errors print `error: …`, exit 1, and leave the file untouched.
- **Planner (F-05):** `agent/tools_draft.py` is deleted. The planner validates against `vibe.toolbox.TOOLS`, and `agent/timeparse.py` imports timestamp parsing from `toolbox/timecodes.py`. Tool names didn't change, so the request set's expected plans are the same.
- **Tests:** `backend/tests/toolbox/` (68 tests):
  - `test_tools.py`: every tool, every error path, and tools working on both an `OpLog` and a storage `Project`;
  - `test_registry.py`: guards the shared names; argument models forbid unknown fields;
  - `test_toolbox_cli.py`: the **exit check** and the CLI's error handling (bad UTF-8, an unwritable folder, permissions).
- **Docs:** a code pointer, the tool list and the CLI in "Toolbox" in `docs/ARCHITECTURE.md`; the toolbox file paths in "Shared files" in `HANDOFF.md`; the CLI command in `README.md`; F-06 claimed in `docs/ROADMAP.md`.

## How to check it
```bash
cd backend
uv run pytest -q                                   # 346 passed (68 in tests/toolbox)
uv run ruff check . && uv run ruff format --check .
uv run python -m vibe.toolbox --help               # lists the 5 tools
uv run python -m vibe.toolbox add_mask --help      # its arguments and an example
cp src/vibe/contract/examples/blur_first_half.jsonl /tmp/t.jsonl
uv run python -m vibe.toolbox mute --log /tmp/t.jsonl --args '{"time_range": {"start": 10, "end": 20}}'
uv run python -m vibe.toolbox resolve_time --duration 90 --args '{"phrase": "last 0:10"}'
```
Verified on 2026-10-28 on macOS 26.6.2 (Apple Silicon, no GPU used), Python 3.12.16, uv 0.13.6. CI hasn't run yet; it runs on the PR.

## Decisions made
pablo made these on 2026-10-28 (F-06 details, not decisions-log material; say on the PR if you disagree):
- **Scope: only the planner's five tools.** `remove_mask`, `unmute`, `uncut`, `undo` and the rest come when the UI or a feature needs them. Each one is a few lines in `tools.py` plus a `ToolSpec` in `registry.py`.
- **No plan executor yet.** Running a whole plan (filling `$ref`s with earlier results) is the next step, not this PR.
- **One entry point, `call_tool`,** for the planner, the API and the UI's hand edits. The CLI sets `source="user"`.
- **Tools don't know where the timeline lives.** They append to whatever `log` they're given (`OpLog` or `Project`), so F-64 can pass a storage project directly.
- **CLI arguments are one JSON object (`--args`)**, because `effect` and `time_range` are nested. `--help` lists the fields and an example.

## Next steps
- Post the heads-up, open the PR and get a review. After merging, the Sprint 2 F-06 card is done.
- **F-64 (API routes):** for `POST /api/projects/{id}/requests`, call `plan_request`, then run each call with `call_tool(name, args, ToolContext(request_id, "agent", log=project, duration=..., tracks=...))`. Apply requests one at a time per project, because a request's ops must be contiguous.
- **Plan executor (F-05/F-06 follow-up):** run the calls in order, replace each `{"$ref": "sN"}` with that step's result (`track_ids` or `time_range`), and dry-run on an in-memory `OpLog` copy first, so a failing step never leaves half a request in the timeline.
- **F-08 / F-10:** replace the `find_objects` stub. Fill `ToolContext.tracks` from the analysis cache (`Storage.cache_path`), or give the tool a module backend.
- **R-1 (paul):** the LLM sees `TOOLS` (each spec has a `description` and an args model, from which a JSON schema can be generated).

## Gotchas
- **`find_objects` only knows the tracks you hand it.** With an empty `tracks`, it finds nothing, and a following `add_mask` fails with "add_mask: bad arguments: targets: List should have at least 1 item". That's correct (fail loudly), but the user message for "no faces found" belongs to the executor.
- **`add_mask` accepts track ids that don't exist.** The contract doesn't check them against tracks. The planner always passes ids from `find_objects`, but hand edits from the UI should send real ids.
- **The CLI rewrites the whole timeline file** (via `OpLog.to_jsonl()`), so its exact formatting may change, though not its content. The server should use `storage.Project.append`, which appends one line.
- `tests/toolbox/test_toolbox_cli.py` isn't named `test_cli.py`, because `tests/video/test_cli.py` already exists and the test folders have no `__init__.py`.
- The pytest `httpx2` deprecation warning is the existing one from F-01.
