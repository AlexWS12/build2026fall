---
author: eduardo
date: 2026-11-05
branch: eduardo/f30-mute
features: [F-30]
base: v0.1.0 @ 6dd4cc8
head: 2817d53
status: ready for review
---

# F-30 mute a time range: edge fades, an `unmute` tool, "mute it" no longer mutes the whole clip

> **Heads-up:** this adds a toolbox tool (`unmute`), and tool names and arguments are a shared file (`HANDOFF.md`, "Shared files"). It also adds a method to `TimelineState` in the contract; no `schema_version` bump, because the schema doesn't change. Post in the team chat before merging.

Most of F-30 was already in place before this branch: the `mute` / `unmute` ops and fold (F-02), the `mute` tool and its request template (F-05/F-06), and all-audio mutes in the renderer (F-04, PR #21). This branch fills the gaps.

## What changed
- **Fades at mute and cut edges** (`render/audio.py`): the sound fades out over `FADE_SECONDS` (10 ms) before each mute and each cut, and back in after, so an edit doesn't click.
  - The fades sit outside the edited range: a muted span is still exactly zero, and a cut keeps exactly the same samples.
  - The gain is worked out from each sample's number in the whole clip, so chunk-by-chunk rendering (export, later live) gives the same output as the whole clip at once. A test checks this for several chunk sizes.
  - At 10 samples a second (the old audio tests) the fade is 0 samples, so those tests didn't change.
  - Cut joins fade too, which is F-20's area; it uses the same code, so it seemed odd to leave it out.
- **`TimelineState.mutes_at(t)`** (`contract/state.py`): the mutes in effect at one source time, next to `masks_at` and `is_cut`, for the per-frame path.
- **`unmute` tool** (`toolbox/tools.py`, `registry.py`, CLI example): writes an `unmute` op with an optional `time_range` and `speaker`. Unlike undo, it can lift part of a mute ("mute 0:10 to 0:20", then "unmute 0:12 to 0:15") or a mute from an earlier request. With no speaker it lifts only the all-audio mute, not a speaker's.
- **Request templates** (`agent/templates.py`, `planner.py`):
  - "unmute", "unmute the audio", "unmute 0:10 to 0:20" and "restore the sound/audio ..." plan an `unmute`. "unmute" and "restore" also start a new clause after "and".
  - **Fix:** "mute it" and "silence it" used to mute the whole clip (Paul flagged it in the R-1 handoff). "it" may be a person or a sound the user means, so now it's declined like other pronouns, and the planner asks or hands it to the LLM. "mute the audio / sound / video / clip" still mutes the whole clip.
  - "unmute him" gets an unmute-specific question. "restore the cut part" gets the general question, because "restore" alone may mean bringing back a cut.
- **End-to-end test** (`tests/render/test_mute_end_to_end.py`): "mute 0:02 to 0:06", "unmute 0:03 to 0:04" and "silence the last 2 seconds" go through `plan_request`, `call_tool`, the timeline and `export` on a 12 s generated clip with a tone. It checks that the muted stretches are silent and the rest keeps its level. There's no plan executor yet, so the test runs the calls and fills `$ref`s itself.
- Docs: `docs/ARCHITECTURE.md` (toolbox "Built so far" and renderer "Audio"), and the F-30 row in `docs/ROADMAP.md`.

## How to check it
From `backend/`:
- `uv run pytest -q`: 725 passed, 1 warning (macOS, Python 3.12, ffmpeg on PATH, no GPU).
- `uv run ruff check` and `uv run ruff format --check`: clean.
- `uv run pytest -q tests/render/test_mute_end_to_end.py tests/render/test_render_audio.py` for just the F-30 parts.
- `uv run python -m vibe.toolbox unmute --help`.

Not checked: listening to an export by ear. The fade tests check the shape of the gain, not how it sounds. Frontend checks weren't run; nothing in `frontend/` changed.

## Decisions made
- Fade length 10 ms, linear, outside the edited range. It's a constant in `render/audio.py`, so it's easy to change if it sounds wrong.
- `unmute` with no `speaker` lifts the all-audio mute only, matching the contract's `Unmute` op.

## Next steps
- Post the heads-up (new tool), open the PR, get a review.
- **pablo:** the "mute it" change touches your parser. Please check it in review.
- **Plan executor (unclaimed):** once it exists, `apply` in the end-to-end test can call it instead.
- **F-32 (speaker mutes):** the renderer still refuses speaker mutes. `mutes_at` and `unmute` already take a `speaker`.
- **F-44 (preview):** ARCHITECTURE says to preview mutes; nothing does that yet.
- Someone should listen to an export with a mute in the middle of speech, to confirm 10 ms doesn't sound clipped.

## Gotchas
- `extract_audio` defaults to 16 kHz mono, not the clip's rate. Use the rate it returns when turning seconds into sample numbers in a test.
- Audio tests at a low sample rate (10 Hz) get no fade at all, by design. To test fades, use 1 kHz or more.
