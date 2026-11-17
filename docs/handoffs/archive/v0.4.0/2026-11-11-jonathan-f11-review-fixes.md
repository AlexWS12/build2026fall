---
author: jonathan
date: 2026-11-11
branch: jonathan/f11-mask-effects
features: [F-11]
base: v0.1.0 @ 44708a6
head: 419285c
status: ready for review
---

# F-11 review fixes on PR #29: the audit matches the renderer at hold edges and on frames a mask starts between

Follows my [F-11 entry](2026-11-06-jonathan-f11-mask-effects.md) and [Christopher's track-loading entry](2026-11-10-christopher-f11-track-loading.md). `origin/main` was merged into the branch first (`a94bca5`, no rebase). The only conflict was the F-11 row in `docs/ROADMAP.md`, which is now one row marked "in review (#29)".

> **For paulallain:** the renderer, `geometry.py` and your F-04 tests are unchanged. `export()` now passes the frame times to `audit_masks`, and the render CLI's JSON gives each stretch a `start_included` key. Nothing was removed.

## What changed
- **Hold boundary (`673bcea`):**
  - The renderer counts a box as lost only when `held > HOLD_SECONDS`, so a frame exactly at the end of a hold isn't lost. The audit said it was.
  - `Stretch` gets `start_included`, which is `False` for a `lost` stretch that starts where a hold ends, and `Stretch.contains(t)` applies it. `start`, `end` and the totals don't change, and neither does `mask_audit.expected.json`.
  - `to_json` carries the flag. Hold *starts* already agreed, because a piece includes its start.
- **A mask starting between frames (`e6dfe28`):**
  - `FrameRenderer` applies such a mask to the earlier frame, with the box at that frame's time. The audit started at the mask's start, so it could say `clean` while the renderer reported that frame as lost.
  - `audit_masks(..., frame_times=None)` now audits such a mask from that frame. There are three exceptions:
    - a frame lands exactly on the start;
    - the earlier frame is cut, since export skips it;
    - the mask starts at or after the end of the video.
  - `export()` passes `index.times.tolist()`, a plain list because `mask_report` must import only the contract, the geometry module and the standard library. Without frame times, the audit works as before (say, a preview).
- **`clean` docstrings (`c3ee17a`):** `MaskAudit.clean` and `RenderResult.audit` both say "no `lost`/`no_track` time, in continuous time, and no unchecked rules", as the reviewer worded it.
- **CLI summary (`419285c`):** built from `path`, `frames`, `duration`, `issues` and `audit` directly, instead of `dataclasses.asdict`, which deep-copied the whole audit only to throw it away. The output is the same: the extended CLI test passes on both the old code and the new.

## How to check it
```bash
cd backend
uv run pytest -q tests/masks      # 77 passed, no ffmpeg needed
uv run pytest -q
uv run ruff check && uv run ruff format --check
```

Results on 2026-11-11, Windows 11, Python 3.12.9, no ffmpeg on PATH, no GPU used, after each of the four commits:
- `tests/masks`: 77 passed at `419285c`.
- Full backend suite: 978 passed, 118 skipped, **1 failed** at `419285c` (975 passed after the first commit). The failure is `tests/toolbox/test_toolbox_cli.py::test_writing_keeps_the_timeline_permissions`, the known Windows failure. 116 skips are the ffmpeg tests, and 2 need POSIX permissions.
- `ruff check` and `ruff format --check`: pass.
- **The new tests fail before their fixes:**
  - The hold-edge test failed with the audit flagging 1.5 and 3.5, which the renderer doesn't.
  - The between-frames export test failed because the renderer reported a lost frame at 0.5 with no `lost` stretch in the audit.
  - The two pure between-frames tests failed with `TypeError` before `frame_times` existed.

**Not tested:**
- The ffmpeg render tests in `tests/render`, which cover export with audio, were skipped here. CI has to run them.
- Real footage and real detector tracks.

## Decisions made
None is architecture-level.
- **The renderer's `>` is the rule, and the audit follows it.** Switching `geometry._held` to `>=` doesn't change any pixels (`lost` only drives the issue flag). It would only move the disagreement to the hold starts, since the points that aren't lost would then form an open interval, which `[start, end)` can't express either. It would also change three F-04 tests and the documented meaning of `HOLD_SECONDS`.
- **Frame times are optional in `audit_masks`.** The audit stays pure and still works before any render.

## Next steps
- **Review and merge PR #29.**
- **Follow-up, shared geometry (from the review):** `_pieces` and `_kind_at` in `mask_report.py` restate `TrackPath.at`'s rules: holding, the long-gap hull, uncertain keyframes and shared ids. When F-11 tunes `geometry.py`, geometry should expose one function giving each track's kind intervals, and both the renderer's `lost` flag and the audit should use it. **Keep the renderer's `>` (lost only when `held > HOLD_SECONDS`) as the rule:** the intervals that aren't lost are closed, `[k − HOLD, k + HOLD]`. This also removes the floating-point gap described under Gotchas.
- **Tuning is still blocked on labeled clips** (see my earlier entry).

## Gotchas
- **Use `Stretch.contains(t)`, not `start <= t < end`,** to ask whether a frame is in a stretch. The two differ at the end of a hold.
- **The edges can still differ by a rounding error.** The renderer computes `t - last.t > HOLD`, and the audit's edge is `last.t + HOLD`. When that arithmetic isn't exact (say `0.7 - 0.2`), a frame on the edge can tip either way. The renderer comparisons sample mid-frame for this reason, and the exact-edge test uses quarter-second times, which add up exactly. The shared-geometry follow-up fixes it.
- **An audit from `export()` can start earlier than its mask,** at the frame the mask first shows on. `RenderResult.audit` equals `audit_masks(..., frame_times=index.times.tolist())`, not the call without frame times.
