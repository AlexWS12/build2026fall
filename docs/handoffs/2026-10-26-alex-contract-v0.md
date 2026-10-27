---
author: alex
date: 2026-10-26
branch: alex/contract-v0
features: [F-02]
base: untagged @ 69c48d2
head: 927f0e2
status: ready for review
---

# Edit timeline contract v1 in code: ops, fold, undo/redo, tracks, schemas, examples

> **Heads-up:** this is the shared contract that all of Sprint 2 builds on, and it adds `pydantic` to `backend/pyproject.toml` / `uv.lock`. Post in the team chat before merging. It proposes **D-013**, which the team confirms on the PR.

## What changed
- **`backend/src/vibe/contract/`:**
  - `ops.py`: one Pydantic model per op kind: `add_mask`, `remove_mask`, `set_effect`, `cut`, `uncut`, `mute`, `unmute`, `add_audio_filter`, `remove_audio_filter`, `add_rule`, `remove_rule`, `undo`, `redo`. `Op` is their union, told apart by `kind`, and `OP_ADAPTER` validates any of them.
  - `fold.py`: `fold(ops) -> TimelineState` and `history(ops)` (which requests are in effect, what undo or redo would act on).
  - `state.py`: `TimelineState`, with per-frame lookups (`masks_at`, `rules_at`, `is_cut`) and `source_to_output` / `output_to_source`.
  - `tracks.py`: `Track` / `Keyframe`, with normalized boxes.
  - `log.py`: `OpLog` (append-only, refuses ops that break the rules; `undo()` / `redo()` helpers; JSONL with a `schema_version` header).
  - `common.py`: `SCHEMA_VERSION = 1`, `TimeRange`, `Effect`, `ContractError`.
  - `schema.py` + `__main__.py`: `python -m vibe.contract [--check]` regenerates or checks the generated files.
- **Generated:** `schema/op.schema.json`, `state.schema.json`, `tracks.schema.json`. **Examples:** `examples/blur_first_half`, `edit_session` (every edit kind plus undo and redo), `live_only_me` (standing rules) as `.jsonl` with their folded `.state.json`, plus `street.tracks.json`.
- `backend/tests/test_contract.py`: 43 tests. They cover validation (typos, unknown fields and kinds, bad ranges), mask painting, cuts and time mapping, audio, rules, undo and redo (linear, cleared by new edits, illegal sequences rejected), JSONL round trips, tracks, the examples, and stale generated files.
- `pydantic>=2.9` is now an explicit dependency (it was already pulled in by FastAPI).
- Docs:
  - `docs/ARCHITECTURE.md`: the "The contract" section is rewritten for v1 and points at the code, the live-mode example is updated, and **D-013** is added (Proposed).
  - `HANDOFF.md`: the shared-files entry now points at `backend/src/vibe/contract/`.
  - `docs/ROADMAP.md`: F-02 is claimed.

## How to check it
```bash
cd backend
uv sync
uv run pytest -q                      # 52 passed (9 skeleton + 43 contract)
uv run ruff check && uv run ruff format --check
uv run python -m vibe.contract --check   # exit 0: schemas and example states are up to date
```
Verified on 2026-10-26 on Windows 11 (Python 3.12.12, uv 0.9.26, pydantic 2.14.1). CI hasn't run yet; it runs on the PR.

## Decisions made
Alex made these on 2026-10-26; they're proposed as D-013 for the team to confirm:
- **Geometry lives in tracks, not in ops or state.** Ops name track ids, and the renderer is `render(video, state, tracks)`. A better detector doesn't invalidate edits, and live mode feeds tracks per frame. Boxes are normalized 0..1.
- **All times are source time in seconds**, start included and end excluded, with `end: null` meaning "to the end". A cut never shifts other edits. Seconds rather than frames, because phone clips often have variable frame rates and audio has no frames.
- **Undo and redo are appended ops**, linear, per request. Nothing is ever deleted from the log.
- **Later ops win over their own range.** `set_effect` only changes parts that are already masked. Speaker mutes and the all-audio mute are tracked separately.
- **Left out of v1 on purpose:**
  - `keep`: the toolbox writes cuts around the part to keep;
  - `remove_object`: added when inpainting research lands; a new op kind doesn't need a version bump;
  - video metadata in the state: the renderer gets it from F-03.
- **R-2 (storage) is not decided here.** The contract only defines the JSONL format, not where the file lives.

## Next steps
- Open the PR, post the heads-up, and get a review. After merging, set F-02 to `done` in `docs/ROADMAP.md` and D-013 to Accepted, if the team agrees.
- **F-06 (toolbox)** can start now: tools build ops with `AddMask(...)` and so on, and append them with `OpLog.append`.
- **F-04 (renderer)** can build against `examples/*.state.json` and `street.tracks.json` without running anything.
- **F-07 (UI)**: once API routes return these models, the generated TS types follow from OpenAPI (D-009).
- **R-2:** decide where `timeline.jsonl` and the tracks cache live per project.

## Gotchas
- **Edited a model? Run `uv run python -m vibe.contract` and commit the regenerated files**, or `test_generated_files_are_up_to_date` fails.
- **Example `.jsonl` files need explicit `op_id`s and `rule_id`s.** Their defaults are random, which would make the generated example states change on every run.
- `OpLog.append` re-folds the whole log on every append to validate it. That's fine for hundreds of ops; if logs ever get large, validate incrementally.
- A request's ops must be contiguous in the log. The server needs to apply one request at a time per project; Knight Hacks used a lock per video for this.
- The pytest warning about `httpx2` is the existing one from F-01 (see eduardo's repo-skeleton handoff), not new.
