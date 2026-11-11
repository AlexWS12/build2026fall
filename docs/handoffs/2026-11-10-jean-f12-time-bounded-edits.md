---
author: jean
date: 2026-11-10
branch: jean/f12-time-bounded-edits
features: [F-12]
base: v0.1.0 @ 751159b (origin/main merged in 3f17d44)
head: 3324eef
status: ready for review
---

# F-12 time-bounded edits: `resolve_range`, thirds in the planner, round-trip tests

> **Heads-up, shared tool behaviour (no name or argument change).**
> - `resolve_time` keeps its name, arguments, result and exports, but now runs on `resolve_range`. It accepts more phrases (thirds, "after 0:30", "before 1:00", "between 0:10 and 0:40"), and its errors are `TimeRangeError` (a `ToolError` subclass). Its `phrase` description lists the new forms, which the LLM planner will see.
> - **Since the PR #30 review, every edit tool refuses a range past the end of the video when `ctx.duration` is known.** That covers `add_mask`, `cut_range`, `mute`, `unmute`, `denoise`, and any later tool that writes through `_write` (e.g. F-20's `keep_range`).
> - The time-phrase regexes moved into `toolbox/timecodes.py`, shared by the planner and the resolver.
> - **No contract change**, so `schema_version` is unchanged.
>
> Please tell pablo (F-05/F-06/F-20), paul (R-1) and eduardo (F-30, `unmute`) in the team chat.

## What changed
- **`backend/src/vibe/toolbox/time_range.py` (new):**
  - `resolve_range(phrase, duration) -> TimeRange` is a pure function that the planner, the API and the UI can all call. It covers:
    - first and second half;
    - first, middle and last third ("second third" means the middle third);
    - "first N" and "last N", where N needs a unit or a clock time;
    - "after/from/since/starting at M:SS" (optionally followed by "on"/"onwards");
    - "before/until/till/up to M:SS";
    - "between M:SS and M:SS" and "M:SS to M:SS".
  - The planner's leading words ("for", "during", …), a leading "the", letter case and extra spaces are ignored.
  - It returns the **contract's** `TimeRange`, which is frozen, has `extra="forbid"` and requires `0 <= start < end`. Ranges that run to the end of the video have `end=None`, as in the contract and the old `resolve_time`.
  - `check_range(r, duration)` adds what the contract can't know: `start < duration` and `end <= duration`. An end exactly at the duration is allowed.
  - `TimeRangeError(ToolError)` is raised for:
    - a duration that is ≤ 0 or not finite;
    - a phrase it can't read;
    - a bare number ("the first 5": seconds or minutes?);
    - a range that is empty or backwards;
    - a range that starts at or after the end, or ends after it;
    - "first N" or "last N" as long as the video or longer (kept from the old `resolve_time`).

    Nothing is clamped or guessed. Pydantic's `ValidationError` is never caught here. The CLI and API keep presenting errors as they do now.
- **`backend/src/vibe/toolbox/tools.py`:** the body of `resolve_time` is now a check that the duration is set, followed by `resolve_range`. Errors are re-raised as `TimeRangeError("resolve_time: ...")`, so the message still names the tool. All existing `resolve_time` tests pass unchanged. The imports it no longer uses are gone.
- **`backend/src/vibe/agent/timeparse.py` (F-05):** it imports the shared grammar from `toolbox/timecodes.py` (see Review fixes), and `_HALF` also matches the thirds, so "blur faces for the last third" plans as `resolve_time("last third")` + `add_mask`. The planner has no request model with fields: time ranges already travel as each tool call's `time_range` argument, either a literal `TimeRange` or a `$ref` to a `resolve_time` step. So the "optional range field" this card was sketched with isn't needed, and no other planner code changed.
- **Renderer (F-04): unchanged.** It reads the folded `TimelineState`, so masks, cuts and mutes over any range already work. No adapter was needed.
- **Tests:**
  - `backend/tests/toolbox/test_time_range.py` has 60 tests: one per grammar phrase, the edge cases, the `resolve_time` tool, the planner's thirds, plan→toolbox→timeline, and two round-trip tests on a 90 s clip.
  - The edge cases are: zero or invalid duration, a 1-frame clip, an end exactly at the duration, an end one frame past it, a start at or after the end, a clip shorter than the request, negative numbers, swapped bounds, zero-length ranges, bare numbers, unknown phrases, non-integer minutes, and overlapping ranges merging in the timeline.
  - The two round-trip tests are "first half" of the probed clip = (0, 45) within one frame, and "cut the second half" → export → output is 45 s within one frame.
  - `backend/tests/toolbox/conftest.py` is new. It makes the 90 s clip with the ffmpeg CLI (testsrc2 at 64x48 and 10 fps, plus an AAC tone), using the same skip/`VIBE_REQUIRE_FFMPEG` pattern as the benchmark conftest. The F-09 files are untouched.
- `docs/ROADMAP.md`: the F-12 backlog row is now `in progress` with my branch.

### Review fixes (PR #30, Eduardo)
I merged `origin/main` (`751159b`, including F-08 and F-30) into the branch in `3f17d44` instead of rebasing, so the re-push isn't a force-push. The merge had no conflicts.
- **The planner's ranges skipped `check_range`. Fixed in the edit tools.**
  - `parse_time_phrase` still builds literal ranges for after/before/between/span. It can't know the duration, so it still catches backwards ranges and bare numbers, but not ranges past the end.
  - The check now runs in `tools._write`, the one place every edit tool appends an op. It raises `TimeRangeError("<tool>: …end of the video…")` and writes nothing. Without a duration it can't check, so it writes, like `cut_range`'s whole-video check.
  - I chose this over routing after/before/between through `resolve_time`. That option would have changed the plans in the shared request set (R-1, F-09), and wouldn't have caught LLM plans or UI hand edits.
  - New tests:
    - `test_planner_ranges_past_the_end_are_refused_when_the_plan_runs`: 6 requests, including "blur people after 2:00" and "mute 1:00 to 1:30.1" on 90 s; the log stays empty;
    - `test_planner_range_ending_exactly_at_the_end_is_written`;
    - `test_hand_edits_past_the_end_are_refused` (`mute`, `denoise`, `add_mask`, `cut_range`);
    - `test_unmute_past_the_end_is_refused`;
    - `test_edits_without_a_known_duration_cant_be_checked_and_are_written`.
  - Mutation check: with the guard turned off (`if False:`), 11 of these fail.
- **"Second third" now matches in the planner too.** `FRACTION` is shared, and `parse_time_phrase("the second third")` returns `"second third"` (`test_planner_and_resolver_agree_on_second_third`).
- **One grammar.** `TS`, `LEADING_WORDS`, `FRACTION`, `SPAN`, `BETWEEN`, `AFTER` and `BEFORE` live in `toolbox/timecodes.py`. `agent/timeparse.py` imports them under its old private names, so the body of `parse_time_phrase` is unchanged and the agent tests pass untouched. `time_range.py` imports them too.
- **Leading words.** `resolve_range` strips the planner's `LEADING_WORDS` ("for", "during", "over", "everything", …) before "the", so it reads phrases the way the LLM passes them, e.g. "during the first half" (`test_resolver_ignores_the_planners_leading_words`).
- **A missing duration is a plain `ToolError` on purpose.** The video hasn't been probed, and that isn't something the user can fix by rephrasing the time. I documented this in the `resolve_time` and `TimeRangeError` docstrings, with a test.
- **ffmpeg skip helper (fourth copy): not changed here.** Sharing it means editing the video, render and benchmark conftests, which other people own. That's a follow-up in Next steps.
- **CI on the first push (`a1c42aa`) was green:** 752 passed, 0 skipped, so both 90 s round-trip tests ran through the ffmpeg fixture and passed on Ubuntu.

## How to check it
```bash
cd backend
uv run pytest tests/toolbox/test_time_range.py -v
uv run pytest
uv run ruff check && uv run ruff format --check
uv run python -m vibe.toolbox resolve_time --duration 90 --args '{"phrase": "the middle third"}'
```
I verified this on 2026-11-10 on Windows 11 Home (10.0.26300), Python 3.12.10, uv 0.13.6, **without ffmpeg on PATH**:
- `test_time_range.py`: 58 passed and 2 skipped. The 2 skipped are the ffmpeg round-trip tests.
- Full suite: 645 passed, 106 skipped (ffmpeg) and **1 failed**. The failure is `tests/toolbox/test_toolbox_cli.py::test_writing_keeps_the_timeline_permissions`, and it fails the same way on unchanged `origin/main` (`6dd4cc8`) on this machine. Windows has no POSIX mode bits, so `chmod(0o644)` reads back as `0o666`. On Ubuntu CI it doesn't apply.
- `ruff check` and `ruff format --check` are clean, and `uv sync --locked` is fine.
- **The round-trip tests were run once, not through the fixture.** A scratch script made an equivalent 90 s clip with PyAV (libx264 at 64x48 and 10 fps, a 16 kHz AAC tone; probed duration 90.0) and called both tests: both passed. The ffmpeg fixture itself only runs on CI.
- **Mutation checks:** I mutated each line below, ran the file, and restored the original, which was byte-identical afterwards.
  1. `check_range`: `r.end > duration` changed to `>=`. Caught by `test_end_exactly_at_the_duration_is_allowed` and `test_check_range_one_frame_past_the_duration_raises`.
  2. In the "first N" branch, start and end swapped (`_range(seconds, 0.0, …)`). Caught by `test_first_n_minutes`, `test_first_n_seconds` and `test_non_integer_minutes`.
  3. The "as long as the whole video" check removed. Caught by `test_one_frame_clip_refuses_a_span_longer_than_itself` and all three `test_clip_shorter_than_the_request_raises` cases.

After the review fixes (same machine and tools, on top of the `origin/main` merge):
- `test_time_range.py`: 77 passed and 2 skipped (ffmpeg).
- Full suite: 766 passed, 112 skipped (ffmpeg) and the same 1 Windows-only failure.
- ruff is clean.
- I read through the ffmpeg-only tests that run tools: `test_mute_end_to_end.py` uses ranges inside its 12 s clip, and the API tests don't run tools (there's no executor yet). So the new edit-tool check doesn't affect them.

CI for this push: see the PR.

## Decisions made
jean made these on 2026-11-10 for F-12. Say on the PR if you disagree. None is architecture-level, so there's no decisions-log row.
- **Reuse the contract `TimeRange`; no second model.** Its semantics (source seconds, end excluded, `end=None` = to the end) are what every op takes. A toolbox `TimeRange(start, end <= duration)` would clash by name and couldn't express "to the end".
- **`TimeRangeError` subclasses `ToolError`.** `call_tool`, the toolbox CLI and the API already present `ToolError`, so the new error needed no new handling.
- **No tolerance at the end.** "Before 1:30" on a clip probed at 89.98 s raises instead of clamping, which fails safe: the planner asks the user. See the open questions.
- **"Last half" is refused, not read as "second half"**, so a typo doesn't silently pick a range.

## Open questions
- **The plan executor (Sprint 4) has to give the user the `TimeRangeError` from an edit step** as a question about the time, not a generic failure. Today nothing runs plans, so no user sees it yet.
- **End tolerance:** should an end within half a frame past the probed duration be accepted? That needs the fps in `ToolContext`.
- **Request set:** add "blur faces for the last third" and "mute the middle third" to `tests/agent/requests_draft.json`? It's shared with R-1, so ask paul first.
- **F-20 (pablo):** `resolve_range` gives cut/keep their ranges. A "keep only X" would be `cut` on the complement, and the complement of an open-ended range is just `TimeRange(0, start)`.
- `docs/ARCHITECTURE.md` "Toolbox" still describes `resolve_time` as "arithmetic on the video duration". It's a shared file, so I didn't edit it.
- The F-12 roadmap row has no exit check. Proposed: "Every phrase in the grammar resolves or asks; ranges never pass the video's end; a round-trip render test passes."

## Next steps
- **jean:** post the heads-up above, then answer Eduardo's review on PR #30.
- **Follow-up, anyone:** one shared ffmpeg skip-or-fail helper for the video, render, benchmark and toolbox conftests, maybe with a PyAV fallback so the clip tests run on machines without ffmpeg. Ask the owners of those conftests first.
- **pablo (F-20):** `keep_range` gets the past-the-end check for free if it writes through `_write`.

## Gotchas
- Without ffmpeg on PATH, the two round-trip tests skip locally. Install ffmpeg, or rely on CI (`VIBE_REQUIRE_FFMPEG=1`).
- `test_writing_keeps_the_timeline_permissions` always fails on Windows (see above). It isn't caused by this branch.
