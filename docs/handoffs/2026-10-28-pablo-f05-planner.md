---
author: pablo
date: 2026-10-28
branch: pablo/f05-planner-v2
features: [F-05]
base: v0.1.0 @ 0977fb7
head: c9661f7
status: ready for review
---

# F-05 planner, part 1: template parser, plan validation and clarify reply (no LLM yet)

> **Heads-up:** no code in a shared file changes, but it proposes **D-016** (plan format) in `docs/ARCHITECTURE.md` for the team to confirm on the PR. The tool names it targets are a **draft** in `backend/src/vibe/agent/tools_draft.py` because F-06 (toolbox) isn't claimed yet, so whoever builds F-06 should read it first. `backend/tests/agent/requests_draft.json` is a **draft request set** for F-09 (jean) and R-1 (paul) to adopt or edit.

## What changed
- **`backend/src/vibe/agent/`:**
  - `planner.py`: `plan_request(text, registry=DRAFT_REGISTRY, llm=None)` returns a `Plan` or a `Clarify`. It tries the template parser first. With no match and no LLM, it asks a question that depends on the verb, e.g. "Which part should I cut? Give a time range like 0:10 to 0:20." `LlmPlanner` is the slot for R-1's model:
    - an invalid plan is retried once, with the validation errors passed back;
    - after a second invalid plan, it returns `Clarify` with both errors in `reason`;
    - if the provider fails, the adapter raises `LlmError` and the planner returns `Clarify` and logs it. Any other exception is a bug and is raised, not hidden.
  - `templates.py`: the template parser, which handles:
    - mask requests ("blur / pixelate / black box / hide" + "faces / people / everyone / license plates"), with an optional time;
    - "mute / silence", with an optional time;
    - "cut / remove / delete", which needs a time;
    - clauses joined by "and" / "then".
  - `timeparse.py`: understands "0:10 to 0:20", "0:05-0:08", "between X and Y", "the first 5 seconds", "after 1:30", "before 0:30" and "for the whole video". "First half" / "second half" / "last 10 seconds" become a `resolve_time` step, because they need the video's duration.
  - `validate.py`: `validate_plan(plan, registry)` returns readable errors for unknown tools, bad or extra arguments, duplicate step ids, `$ref`s to a missing, later or wrong-type step, and a plan with no edit call. `add_mask.targets` must be a `$ref`, so a planner can't make up track ids.
  - `plan.py`: `ToolCall(step_id, tool, args)`, `Plan(calls)` and `Clarify(question, reason)`. An argument `{"$ref": "s1"}` means "the result of step s1". The LLM will write this same JSON shape.
  - `tools_draft.py`: the **draft** registry, with `find_objects`, `resolve_time`, `add_mask`, `cut_range` and `mute`. It reuses `TimeRange` / `Effect` from the contract.
- **`backend/tests/agent/`:** 150 tests.
  - `test_planner.py` holds the **exit check**: it runs every request in `requests_draft.json` (34 requests: 18 plans, 16 clarify) with the socket blocked, and checks the exact tool calls or a clarify.
  - The same file covers the LLM slot with fake LLMs: retry once, then clarify, plus provider errors.
  - `test_templates.py`, `test_timeparse.py` and `test_validate.py` cover each part.
- `docs/ROADMAP.md`: claimed F-05 (branch in the Sprint 2 row; backlog status `in progress`).
- **Review fixes (PR #15):** bare numbers ask "seconds or minutes?"; "and" is a range word only after "between"; a non-draft registry rejecting a template plan falls back instead of crashing; the mixed-time clarify has its own reason (`only part of the request has a time`); `LlmError` narrows what counts as a provider failure; singular nouns and polite words are handled.
- `docs/ARCHITECTURE.md`: added **D-016** (plan format, Proposed) to the decisions log and a pointer to it in "Agent / planner".

## How to check it
```bash
cd backend
uv run pytest -q                       # 278 passed (150 in tests/agent)
uv run ruff check . && uv run ruff format --check .
uv run python -c "from vibe.agent import plan_request; print(plan_request('blur faces for the first half')); print(plan_request('cut it'))"
```
Verified on 2026-10-28 on macOS 26.6.2 (Apple Silicon, no GPU used), Python 3.12.16, uv 0.13.6, pydantic 2.14.1. CI hasn't run yet; it runs on the PR. No LLM is wired in, so nothing calls the network.

## Decisions made
pablo made these on 2026-10-28. The plan format is proposed as **D-016** in `docs/ARCHITECTURE.md` (builds on D-006), for the team to confirm on the PR; the rest are F-05 details, so say on the PR if you disagree.
- **Plan format:** a list of `{step_id, tool, args}`, where an argument may be `{"$ref": "<earlier step_id>"}` to use that step's result. The template parser and the future LLM produce the same shape, and both go through `validate_plan`.
- **The planner asks, it doesn't guess.**
  - **Cuts:** a cut needs an explicit time, so "cut it" and "cut the whole video" get a question.
  - **Units:** a bare number anywhere ("cut 10 to 20", "the first 5", "after 90", "10 to 20 minutes") gets "seconds or minutes?", because it could be either. "X and Y" is a range only after "between", since "mute 0:10 and 0:20" may mean two moments. Times like "1:5" (one digit after the colon) or "1:30 minutes" (a clock time with a unit) are refused.
  - **Singular nouns mask every match:** "blur the face" blurs all faces (fail closed). Polite words around a request ("can you please …", "…, please") are ignored.
  - **"trim"** isn't a template, because it can mean either cut or keep.
  - **Mixed times:** if one part of a request has a time and another doesn't ("blur faces and mute 0:10 to 0:20"), it asks whether the time covers everything. "for the whole video" answers that.
- **Templates only take what they fully understand.** Leftover words ("faces except mine", "the guy in red", "him") mean no match, which leads to a clarify, or to the LLM once it exists.

## Next steps
- Open the PR and get a review. After merging, the Sprint 2 F-05 card is done. The backlog row stays `in progress` until the LLM plan lands.
- **F-06 owner:** replace `tools_draft.py` with the real registry. The planner only needs `name`, `kind` (query/edit), an args model, `returns` and `ref_args` / `ref_required`. Rename tools in `requests_draft.json` if the names change.
- **R-1 (paul):** once a model is picked, implement `LlmPlanner.plan(request, tools, error) -> dict` (one call returns the whole plan as JSON) and pass it to `plan_request(..., llm=...)`. Compare models on `requests_draft.json`.
- **F-09 (jean):** adopt or extend `requests_draft.json` as the shared request set.
- **F-64 (API routes):** call `plan_request` for `POST /api/projects/{id}/requests`. It doesn't run tools yet; executing a plan (resolving `$ref`s) is the next F-05/F-06 step.
- **Open:** when an LLM writes a cut with a literal time range the user never gave, flag it for confirmation instead of applying it.

## Gotchas
- **Not plugged in yet.** `plan_request` only returns a plan. Nothing executes it, so no ops reach the timeline until F-06 exists.
- **Clause splitting only happens on "and" / "then" / commas followed by a known verb**, so "between 0:10 and 0:20" stays one range. A new verb has to be added to `_VERBS` in `templates.py` too.
- **A template plan the registry rejects** raises `PlanError` only with `DRAFT_REGISTRY` (a parser bug). With any other registry (e.g. F-06's, if it renames a tool) it is logged and falls back to the LLM, or a clarify.
- The pytest `httpx2` deprecation warning is the existing one from F-01, not new.
