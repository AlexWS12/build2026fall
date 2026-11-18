---
author: jonathan
date: 2026-11-06
branch: jonathan/f11-mask-effects
features: [F-11]
base: v0.1.0 @ 6dd4cc8
head: 00e1292
status: in progress
---

# F-11: mask audit aligned with the F-04 renderer, and added to RenderResult

> **Heads-up for paulallain:** `RenderResult` has a new required `audit` field, the render CLI's JSON output has a new `audit` key, and three F-04 files changed (see "For paulallain"). No shared files in `HANDOFF.md`'s list changed: the contract, toolbox, API routes, dependencies and `docs/ARCHITECTURE.md` are untouched.

> **The F-11 card isn't finished.** The audit work below is done. Christopher is taking cached tracks and the export tests (see "For Christopher"), and tuning is blocked on labeled clips.

## What changed
Builds on [paulallain's F-04 entry](2026-11-03-paulallain-f04-renderer.md), whose last next step asked for `mask_report.py` to be aligned with the final renderer. `origin/main` was merged into this branch (`3ccbd5f`).

- **Item 1, audit aligned with the renderer (`e84e870`), in `backend/src/vibe/render/mask_report.py`:**
  - `off_screen` is gone, because a mask in effect always has a box now. It's replaced by `lost`: a box stays masked, but the target may have left it. This changes what the audit concludes, not just a name.
  - A gap over `MAX_GAP_SECONDS` is now `gap`, `lost`, `gap`, matching the renderer: `lost` is only more than `HOLD_SECONDS` from both keyframes.
  - A shared id is `lost` only when all its tracks are, as in `FrameRenderer`.
  - `HOLD_SECONDS` and `MAX_GAP_SECONDS` come from `geometry.py`. The audit's own `GAP_SECONDS` is deleted, and `audit_masks` takes only `duration`.
  - `clean` now means the renderer would report nothing and no rules are unchecked.
  - `tests/masks/mask_audit.expected.json` was reworked by hand, not regenerated. The scenario's inputs didn't change. `lost` is 32 s (the old 30 s of `off_screen` plus 2 s from `trk_gap`) and `gap` drops from 3 s to 1 s.
  - New tests compare the audit with the real `FrameRenderer`, frame by frame, and check that the expected file's settings match `geometry.py`.
- **Item 2, audit in `RenderResult` (`00e1292`):**
  - `RenderResult.audit` holds a `MaskAudit`, computed before anything is written.
  - `export()` turns `tracks` into a list first, because `FrameRenderer` empties a generator and the audit would then see no tracks.
  - The audit's duration is `FrameIndex.end`. `FrameIndex.build` moved from `_write` up into `export()`, and `_write` takes `index` as a parameter.
  - `MaskAudit.to_json()` gives plain data including `totals` and `clean`, because the CLI's `json.dumps` crashed on `Effect`.
  - `audit_masks` stays callable on its own, with no video, and is exported from `vibe.render` along with `MaskAudit`.
  - New `tests/masks/test_mask_export.py` (9 tests) uses clips from PyAV through `write_clip` in `mask_helpers.py`, so it needs no ffmpeg.

## How to check it
```bash
cd backend
uv run pytest -q tests/masks      # 58 passed, no ffmpeg needed
uv run pytest -q                  # see the results below
uv run ruff check && uv run ruff format --check
```

Result on 2026-11-06, Windows 11, Python 3.12.9, no ffmpeg on PATH, no GPU used:
- `tests/masks`: 58 passed.
- Full backend suite: 645 passed, 104 skipped, **1 failed**. The failure is `tests/toolbox/test_toolbox_cli.py::test_writing_keeps_the_timeline_permissions`, the known Windows failure from the F-04 and F-33 entries.
- `ruff check` and `ruff format --check`: pass.
- I broke the code on purpose to confirm the tests can fail: 3 ways for item 1 and 8 for item 2. All were caught. Those were one-off checks, and the scripts aren't in the repo.

**Not tested:**
- **The F-04 export tests didn't run on my machine, because ffmpeg is missing.** `tests/render` gives 97 passed, 26 skipped, and the skips include the exit check and the audio export tests. They go through `_write`, whose signature changed. My new tests only export silent clips, so **export with audio is unverified until CI runs it.**
- Real footage and real detector tracks: everything here uses generated clips and hand-written tracks.
- CI hasn't run on this work.

## For paulallain
- **`RenderResult` has a new required field, `audit: MaskAudit`.** `export()` is the only place that builds one.
- **The CLI's JSON output has a new `audit` key,** with `spans`, `rules_not_audited`, `totals` and `clean`. Nothing was removed.
- **Three of your F-04 files changed:**
  - `render/export.py`: the field, `list(tracks)`, and `FrameIndex.build` moved up into `export()`.
  - `render/__main__.py`: the summary in `main()`.
  - `render/__init__.py`: exports `MaskAudit` and `audit_masks`.
- `frame.py`, `geometry.py`, `effects.py`, `audio.py` and your tests are unchanged, so nothing changed on the per-frame path.
- **`docs/ARCHITECTURE.md` section 6 could get a line about the audit** under "Built so far". I left that section for you.
- Please look at the CI result for the export tests, since they didn't run here.

## For Christopher
You're taking items 3 and 4. Neither is started.

**Item 3: load F-10's cached tracks in export.** Today `--project` uses only hand-drawn tracks plus `--tracks`, so a mask on a detector track finds no box and blurs the whole frame.
- Load the tracks in the caller (`_run` in `render/__main__.py`, and the export route when there is one), not in `export()`, so the per-frame path never reads the cache.
- Add a way to pick the analysis key, for videos with several cached track sets.
- Catch `CacheError` in `main()`, which also covers `AmbiguousAnalysis`. It isn't caught now, so users would get a traceback.
- **Unanalysed project (decided):** render with the hand-drawn tracks, and let unmatched masks fall back to the whole frame. `project_tracks` returns `None` in this case even when hand-drawn boxes exist, so it can't be used as it is. `audit.totals["no_track"]` shows the result.
- Track ids carry the analysis key's hash, so masks made on one analysis find no track under another. That fails closed to the whole frame.

**Item 4: export tests.** `tests/masks/test_mask_export.py` already covers a lost target and audit against issues across a cut, a missing track, and a fully covered track. Still missing at export level:
- pixelate;
- a long gap;
- an uncertain keyframe;
- `--project` using cached tracks (needs item 3).

`write_clip` in `tests/masks/mask_helpers.py` makes silent clips with no ffmpeg, so these can run on any machine. A test with sound would need an audio stream added to it, or the ffmpeg clips in `tests/render/conftest.py`.

**Possible small conflict:** adding `CacheError` to the `except` in `main()` in `render/__main__.py` lands three lines above my summary change. `_run`, `_load_tracks` and `--project` are untouched, so it should be quick to resolve.

## Decisions made
None is architecture-level.
- **The audit is both standalone and in `RenderResult`.** F-41 can call `audit_masks` before a render, and an export carries its own audit.
- **An unanalysed project renders with its hand-drawn tracks** (for item 3, above).
- **`audit.clean` can be stricter than `issues`.** A lost stretch too short to reach a frame is in the audit but not in `issues`. That errs on the safe side.
- **`issues` stays alongside `audit`:** `issues` is what frames actually hit, and `audit` is the full breakdown in continuous time.

## Next steps
- Item 3 and item 4: Christopher.
- **Tuning is blocked on labeled clips.** `backend/src/vibe/benchmark/clips.json` has 0 clips, so `MAX_GAP_SECONDS`, `HOLD_SECONDS`, `MAX_HOLD_GROWTH` and the effect sizes can't be measured yet. If they change, `mask_audit.expected.json` has to be reworked by hand; a test fails until it is.
- **The plan executor has no owner.** Until it lands, no chat request changes the timeline, so masks don't reach the renderer from the UI. See the [F-64 entry](2026-11-03-jonathan-f64-api-routes.md) for what it involves.

## Gotchas
- **The audit repeats the renderer's rules.** Sharing the constants doesn't stop the logic drifting. The comparison with `FrameRenderer` in `test_mask_report.py` is what catches that, so keep it passing rather than loosening it.
- **The one-frame difference at a hold boundary was fixed in the review:** see [2026-11-11-jonathan-f11-review-fixes.md](2026-11-11-jonathan-f11-review-fixes.md).
- **An issue runs across a cut, and audit stretches don't.** A track lost on both sides of a cut is one `RenderIssue` and two stretches.
- **`rules_not_audited` is always empty on a `RenderResult`,** because standing rules are refused first. It only matters when `audit_masks` is called on its own.
- **A normal clip can't tell `index.end` from `probe` duration:** both were 4.0 on the test clip. That's why the test patches `probe`.
