---
author: luis
date: 2026-11-25
branch: luis/timeline-trim-handles
features: [F-20, F-42, F-12]
base: untagged @ 29df1e2
head: see the PR
status: ready for review
---

> **Shared files changed:**
> - A new API route, `POST /api/projects/{id}/trim` (`openapi.json` and the generated UI types are regenerated).
> - **A change in how chat requests read times**: they're now read as on-screen time and turned into source time (`agent/execute.py`). This follows `docs/ARCHITECTURE.md`, which already says *"the toolbox converts what the user sees on screen into source time before writing an op"*, but it changes what an existing request does after a cut. Post a team heads-up before merging.
> - **Overlaps:** #44 rewrites `TimelineView.tsx` (lanes scroll) and #43 (F-44) gates cuts behind Apply/Discard. Both conflict with this in the timeline and session files, so whoever merges second rebases.

# Trim handles on the timeline, a playhead you can drag, and chat times that count after cuts

## What changed
- **Trim handles** (`frontend/src/components/TimelineView.tsx`):
  - A "Trim" row sits above the lanes, with two handles (start, end) that are real range inputs, so they work with the keyboard too.
  - What's outside the handles is shaded. **Trim to 0:02–0:08** applies it; **Reset** puts the handles back.
  - After a trim, the handles sit on what was kept, and what was cut shows hatched like the Cuts lane. The note says "Kept 0:03–0:09. Undo brings the rest back."
  - The handles can't leave the kept range, because cut footage only comes back with Undo (`keep_range` keeps earlier cuts).
- **`POST /api/projects/{id}/trim`** (`api/projects.py`):
  - Applies `keep_range` as a **hand edit**: through the toolbox, as the user, with its own request id, like Review and undo. Undo takes it back, and the chat says "Trimmed to 0:02–0:08."
  - 400 when the toolbox refuses (empty, backwards or past the end), and nothing is written.
- **The playhead drags** across the whole timeline:
  - Press on the ruler or the lanes and drag (pointer capture, so it keeps following outside the panel). The playhead has a grip.
  - The lanes are an accessible slider ("Playhead"): arrows move ±1 s, Page Up/Down ±5 s, Home and End.
- **Chat times are on-screen times** (`agent/execute.py`, `ScreenTime`):
  - A chat request's literal ranges ("mute 0:01 to 0:02") and `resolve_time` phrases ("the first 5 seconds", "second half", worked out on the output's length) are turned into source time with `TimelineState.output_to_source` before any op is written.
  - **Examples:** after trimming to 0:03–0:09, "mute the first 5 seconds" mutes source 0:03–0:08. A range to the end of what's on screen runs to the end, and a start past it is refused like any time past the end.
  - The reply keeps the user's times ("Muted 0:00–0:05.").
  - **Not converted:** hand edits (trim handles, Review), which already send source times, and `find_speech` ranges, which come from the transcript in source time.
  - **One test changed meaning:** "cut the first half" then "cut the second half" now cuts half of what's left (2.5 s of a 10 s clip remain), so the "nothing to render" test uses "cut 0:00 to 0:05" as its second request.

## How to check it
```bash
cd backend && uv run pytest -q     # 1647 passed, 2 skipped, 1 failed: test_writing_keeps_the_timeline_permissions (POSIX chmod; fails on main on Windows too)
cd frontend && npm test            # 120 passed
npm run lint
```
Windows 11, 2026-11-25. In the app (any backend, stub is fine):
1. Open a project, drag the trim handles to 0:03 and 0:09, and press "Trim to 0:03–0:09". The Cuts lane shows 0:00–0:03 and 0:09–end, the player jumps to 0:03, and the bar says "Exports as 0:06 after cuts".
2. Type "mute the first 2 seconds": it mutes source 0:03–0:05, and the reply says "Muted 0:00–0:02."
3. Drag the playhead over the lanes: the player follows.

All three were checked by hand in the browser on 2026-11-25.

## Decisions made
- The handles stay inside what's kept, instead of trying to undo a cut by dragging out: undo already does that, and `keep_range` keeps earlier cuts.
- Only chat requests are on-screen; hand edits send source times, since the UI already works in source time.

## Next steps
- When F-44 (#43) lands, a trim should wait while a preview is pending, as undo does there.
- The timeline ruler still shows source time. A toggle to show output time would match the chat's reading.

## Gotchas
- `keep_range` writes two `cut` ops, so the history lists a trim as `["cut", "cut"]`.
