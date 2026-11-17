---
author: christopher
date: 2026-11-10
branch: christopher/f11-track-loading
features: [F-11]
base: v0.1.0 @ 6dd4cc8
head: ccc26c2
status: ready for review
---

# F-11: `--project` export uses the video's cached F-10 tracks, plus export tests for pixelate, long gaps and uncertain keyframes

> **Builds on `jonathan/f11-mask-effects`, which isn't merged yet.** This branch contains Jonathan's commits (`9f616b4`..`3670e18`), so a PR from here into `main` also carries them. Merge Jonathan's PR first, or review the two together. No shared files from `HANDOFF.md`'s list changed. `render/__main__.py` is paulallain's F-04 file and gained a flag (see "For paulallain").

## What changed
This covers items 3 and 4 from the "For Christopher" section of [Jonathan's F-11 entry](2026-11-06-jonathan-f11-mask-effects.md). Everything is in one commit, `ccc26c2`.

- **Item 3: cached tracks in `--project` export (`backend/src/vibe/render/__main__.py`).**
  - New `_cached_tracks()` reads the video's tracks from the F-10 cache (`cache.load_tracks`) in `_run`, before the render starts. The per-frame path never touches the cache, and `export()` is unchanged.
  - The tracks passed to `export()` are now: cached tracks, then hand-drawn tracks (`project.user_tracks()`), then `--tracks`.
  - **New flag `--analysis KEY`** picks one cached track set. Without it, the video's default set is used, or its only set.
  - **Unanalysed project:** it renders with the hand-drawn tracks only. A mask with no track hides the whole frame and shows up as `audit.totals["no_track"]`, so privacy fails closed. `cache.project_tracks` isn't used, because it returns `None` in this case even when boxes were drawn.
  - **Errors, each with a message and no traceback, and nothing written:**
    - several sets with no default (`AmbiguousAnalysis`, which names the keys);
    - an `--analysis` key that isn't cached (lists the cached keys, or says the video isn't analysed);
    - an invalid key;
    - a default whose tracks file is gone;
    - `--analysis` without `--project`.
  - `main()` now catches `cache.CacheError`.
- **Item 4: export tests (`backend/tests/masks/test_mask_export.py`, 15 new tests).**
  - Pixelate in an export matches the per-frame path's pixelate, is blocky rather than blurred, and leaves the rest of the frame untouched.
  - A long gap gives `held`, `gap`, `lost`, `gap`, `held`. Every frame between the two keyframes hides both boxes and the space between them, and the `RenderIssue` covers the same frames as the `lost` stretch.
  - An uncertain keyframe widens the mask by `UNCERTAIN_PADDING` only on the frames next to it. It's listed in the audit, but the audit stays `clean` and there are no `issues`.
  - `--project` cases, which check the exported pixels as well as the audit:
    - cached tracks only;
    - cached plus hand-drawn;
    - unanalysed with hand-drawn;
    - unanalysed with a missing track (the whole frame goes black);
    - the ambiguous-sets error;
    - picking a set with `--analysis`, both ways;
    - the default set used without asking;
    - each of the errors above.
- **`backend/tests/masks/mask_helpers.py`:**
  - `write_clip` takes an optional `picture`.
  - New `texture()` makes a grey picture with detail everywhere and nothing darker than 64, so a blur, a pixelate and a black box all show on it.
  - The clip size is now in `WIDTH`/`HEIGHT`.
- **`docs/ROADMAP.md`:** the F-11 row is split into Jonathan's part (mask audit) and mine (cached tracks + export tests). Both stay `in progress`, because F-11's tuning isn't done.

## How to check it
```bash
cd backend
uv run pytest -q tests/masks      # 73 passed (58 before this branch), no ffmpeg needed
uv run pytest -q
uv run ruff check && uv run ruff format --check
```

By hand, on a project in `VIBE_DATA_DIR`:
```bash
uv run python -m vibe.modules.vision list <video>             # which track sets are cached
uv run python -m vibe.render export --project <id>            # default or only set
uv run python -m vibe.render export --project <id> --analysis <key>
```

Results on 2026-11-10, Windows 11, Python 3.12.4, no ffmpeg on PATH, no GPU used:
- `tests/masks`: 73 passed.
- Full backend suite: 660 passed, 104 skipped, **1 failed**. The failure is `tests/toolbox/test_toolbox_cli.py::test_writing_keeps_the_timeline_permissions`, the known Windows failure from the F-04, F-33 and F-11 entries.
- `ruff check` and `ruff format --check`: pass.

**Not tested:**
- Real footage and real detector tracks. Every test uses PyAV-generated clips and tracks written into the cache by the test, not a real F-10 analysis.
- The `--project` export by hand on a real project. Only the tests drive it.
- The ffmpeg-dependent tests in `tests/render`, including export with audio, were skipped because ffmpeg isn't installed. This branch doesn't change `export.py`, but Jonathan's commits under it do, so CI still needs to run them.
- CI hasn't run on this branch.

## For paulallain
- **The render CLI has a new `--analysis KEY` option** (only with `--project`), and `--project` now includes the video's cached detector tracks. `export()`, `RenderResult` and the per-frame path are unchanged.

## Decisions made
None is architecture-level.
- **A key asked for by name must exist.** An `--analysis` key that isn't cached is an error, not a fallback to hand-drawn tracks only. With no key, an unanalysed video still renders, as Jonathan's entry decided.
- **A default set whose file was deleted is an error,** not treated as an unanalysed video. Treating it that way would quietly turn every detector mask into a full-frame mask.
- **The tracks are loaded in the CLI, not in `export()`,** so the caller decides which analysis to use and the render never reads the cache.

## Next steps
- **Review and merge:** Jonathan's PR first (or both together), then this branch, rebased on `main`.
- **The export API route doesn't exist yet.** When it's added, it needs the same loading as `_cached_tracks()` in `render/__main__.py`, including a way to pass the analysis key. Moving `_cached_tracks` somewhere shared, like `vibe.storage` or `vibe.modules.vision.cache`, may be worth it at that point.
- **Tuning is still blocked on labeled clips** (see Jonathan's entry).

## Gotchas
- **Compare a rendered frame with the source clip's frame at the same time, not with `texture()`.** The MPEG-4 test clips are lossy, so even one picture gives slightly different pixels frame to frame. The tests allow `LOSSY` (6.0 mean per-pixel difference) for this.
- **The test regions are tied to the boxes.** `INSIDE`, `CLEAR`, `BETWEEN`, `BELOW` and `DRAWN_INSIDE` are pixel slices of the 64 × 48 clip chosen for `BOX`, `LEFT`/`RIGHT` and `DRAWN`. Change a box and you have to recheck its slices.
- **Track ids carry the analysis key's hash.** A mask made against one analysis finds no track under another and blacks out the whole frame. `test_analysis_picks_one_of_several_cached_track_sets` checks this on purpose.
- **Hand-drawn boxes don't stand in for the analysis.** A mask naming a detector track id still fails closed on an unanalysed video, even when boxes were drawn.
