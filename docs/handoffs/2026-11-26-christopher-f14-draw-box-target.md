---
author: christopher
date: 2026-11-26
branch: christopher/f14-draw-box-target
features: [F-14]
base: v0.4.0 @ 7b8273f
head: de0b4e8
status: ready for review
---

# F-14: click a target or draw a box around anything in the player, and it's masked through the clip

> **Shared files changed** (`HANDOFF.md`, "Shared files"), so post a heads-up in the team chat before merging:
> - a new hand-only tool `mask_targets` in `toolbox/registry.py`;
> - a new route `POST /api/projects/{id}/targets`, with `openapi.json` regenerated;
> - `frontend/src/api/`: `addTarget`, the `TargetRequest` and `TargetReply` types, and the regenerated `generated/schema.ts`.
>
> The contract is unchanged (no `schema_version` bump).
>
> **This overlaps #44** (eduardo, F-42 "remove a mask") in `Player.tsx`, `registry.py`'s `_HAND_TOOLS`, `test_hand_tools.py`, the API client files and the session files. Whichever merges second rebases. Before the rebase onto #46 and #48, I did a trial merge with #44 (at `c0afb28`): 9 files conflict, and each conflict is two additions side by side. I kept both and put `remove_mask` and `mask_targets` together in the `test_hand_tools.py` set. For `editable`, I used #44's `canEditByHand`, and both features' props go on `Player`. With that, both suites passed: backend 1770 passed, 4 skipped; frontend 133 passed; ruff, tsc and oxlint clean. Regenerate `openapi.json` (`uv run python -m vibe.api`) and `schema.ts` (`npm run gen:api`) after the rebase instead of merging them by hand.

## What changed
- **Box follower (`backend/src/vibe/modules/vision/follow.py`, new).** `follow_box(path, t, box, track_id=..., label=...)` follows a box drawn on the frame at `t` forwards and backwards through the clip. It returns a `Track` with `source="user"`, plus where it lost the target in each direction (`lost_before` / `lost_after`).
  - It matches with zero-mean normalized cross-correlation (FFT plus integral images) on grey frames scaled to 320 px wide, about 10 frames a second. It tries 3 box sizes per step and follows the target's speed.
  - **numpy only:** no model, no GPU and no new dependency, so it runs on tier 0 laptops and in CI. It's a classical tracker, not an AI module, so it has no stub / local / remote split.
  - **Fails closed.** The drawn frame's keyframe is `user` (conf 1), and a weak match becomes an `uncertain` keyframe. After `lost_after` (0.5 s) of no match, or when most of the box has left the frame, it stops in that direction. If the clip ends while the target is still missing, that also counts as lost.
  - Going backwards, it decodes 32 sampled frames at a time and reverses them, so it doesn't seek back to a keyframe for every frame.
  - The settings in `FollowSettings` are starting points, not tuned values.
- **Hand tool `mask_targets` (`toolbox/registry.py`).** It's `add_mask` with literal track ids, in `HAND_TOOLS` next to `confirm_mask`, so the planner never sees it. Plan validation keeps `add_mask`'s targets `$ref`-only.
- **Route `POST /api/projects/{id}/targets` (`backend/src/vibe/api/targets.py`, new file, included in `app.py`).** It's a separate file so `projects.py` (which #44 rewrites) doesn't change. The body is `t` plus either `point` or `box`, an optional `effect` (default blur) and an optional `label` (default `object`). The reply's `how` says what happened:
  - `picked`: a click on a followed target (detector or drawn). The smallest box under the click wins, so a face inside a person box can be clicked.
  - `matched`: a drawn box that an existing track fills (IoU ≥ 0.5). That track is masked, so drawing over a detected person, or drawing the same thing twice, doesn't add a copy.
  - `followed`: any other box. It's followed, saved with `save_user_tracks`, then masked.
  - Picked and matched tracks are masked `while_tracked`, as F-13 does for one described target. A followed box is masked over the whole clip (see "Decisions made").
  - The mask is one `mask_targets` op with source `user` and its own request id, so undo takes it back. If writing the mask fails, the new track is removed again.
  - The reply text says the followed span and any losses, e.g. "Blurred your box and followed it from 0:00 to 0:05.9." It sends a lost stretch to the review queue.
  - Errors:
    - 400: nothing under a click, `t` past the end, or the toolbox refuses.
    - 409: the project is busy.
    - 422: a bad body (both or neither of `point`/`box`, a box out of 0..1 or under 0.5% of the frame, an unknown effect, an odd label).
    - 500: the video or the project's files can't be read, with no server path in the message.
- **Player (`frontend/src/components/TargetLayer.tsx`, new, plus a small hook-up in `Player.tsx` and `App.tsx`).**
  - A **Target** toggle next to Preview / Original. Turning it on pauses the video and covers the picture with a layer that outlines what's followed at this frame (dashed and named; drawn tracks in yellow).
  - A click sends the point. A drag draws a yellow box and sends it on release; a drag under 1% of the picture counts as a click.
  - A bar under the picture picks Blur / Pixelate / Black box and has **Done**. Escape also leaves target mode.
  - Clicks are ignored while the project is busy.
  - Helpers (`targetsAt`, `CLICK_SLOP`) are in `components/targets.ts`.
- **Session (`state/session.ts`, `useSession.ts`).** A new `targeting` activity ("Following your target…"). On settle it takes the server's timeline, history, review queue and tracks, and puts the reply in the chat as a note. A failure says "Couldn't mask that: …" in the chat.
- **Docs:** F-14 roadmap row (claimed, ready for review), and an "as built" bullet under "Human in the loop" in `ARCHITECTURE.md`.

## How to check it
```bash
cd backend
uv run pytest -q tests/vision/test_follow.py tests/test_api_targets.py tests/toolbox   # follower, route, hand tools
uv run pytest -q                                   # 1760 passed, 3 skipped (44 of them new)
uv run ruff check && uv run ruff format --check
uv run python -m vibe.api --check                  # openapi.json is current
cd ../frontend
npm test                                           # 137 passed (17 of them new)
npm run lint && npm run typecheck
```

By hand: `npm run dev` at the repo root, upload a clip (H.264; Chrome won't play MPEG-4 Part 2), pause on a frame, click **Target**, and drag a box around something. The chat says what it followed. Click **Done**, and the preview mask follows the target as it plays. Then **Export**. Undo takes the mask back. On an analysed video, the outlined targets can be clicked.

Results on the Linux cloud workspace (CPU only), Python 3.12.3, Node 22, headless Chrome through Playwright:
- **Backend:** 1760 passed, 3 skipped, after rebasing onto `7b8273f` (#46 and #48 merged) on 2026-11-27. Ruff and the OpenAPI check pass.
- **Frontend:** 137 passed after the same rebase, and oxlint and tsc pass.
- **The rebase:** #48's trim conflicted with F-14 in the API mock, the session, `openapi.json` and `schema.ts`. I kept both sides in the mock and the session, and regenerated the two spec files.
- **End to end in the browser** (2026-11-26, before the rebase), on a generated 6 s, 640 × 360, 25 fps H.264 clip with a textured target walking across a textured background:
  - I drew the box at 0:02. It was followed both ways, 0:00 to 0:05.9 (75 keyframes), with no losses and an empty review queue.
  - In the preview at 0:04, the mask sat on the target.
  - The export had no issues. I checked how much detail was left inside the target at 7 times from 0:00.2 to 0:05.8, as the mean absolute difference between neighbouring pixels: 8.6–9.0 in the source, 0.1–0.2 in the export. Away from the target it stayed the same (1.8 → 1.8, 2.1 → 2.2).
  - The reply arrived 1.3 s after the drag. That's the wall time of one run on that machine, not a benchmark.

**Not tested:**
- Real footage (people, lighting changes, occlusion by other people). Every test uses generated clips.
- Chris's Windows laptop. The known Windows failure in `test_toolbox_cli.py` (see the F-11 entry) wasn't rechecked.
- Clicking an outlined detector target in the browser. The route tests cover `picked` and `matched` with cached tracks, but no analysed video was used in the UI run.
- Long clips. The follower reads the whole clip in both directions on every drawn box, and its time and memory on a several-minute 1080p clip weren't measured.
- CI hasn't run on this branch.

## Decisions made
None is architecture-level.
- **A followed box is masked over the whole clip,** not `while_tracked`. Where the follower lost the target, the renderer holds its last box, marked lost, and the review queue lists that stretch. So a weak tracker never quietly unmasks something, and the person sees where to look. Picked and matched tracks come from the detector or an earlier box, and are masked while tracked, as F-13 does for one described target.
- **numpy template matching, not OpenCV or a model.** OpenCV isn't a dependency (adding it is a shared-file change), and Chris is tier 0. When a GPU tracker lands (SAM 2, or F-15's segmenter), `follow_box` is the one function to swap.
- **A hand tool instead of letting `add_mask` take literal ids.** This keeps the planner unable to invent track ids, like `confirm_mask` (D-021) and #44's `remove_mask`.
- **Undo removes the mask but keeps the drawn track** in `tracks.user.json`. Geometry isn't in the op log, and an unmasked track changes nothing in the render. Drawing over it again reuses it (`matched`).
- **New boxes are labelled `object`.** The UI has no label picker yet, so the timeline shows "object 1", "object 2".

## Next steps
- **Review, rebase with #44, and merge** (see the conflict note at the top). The two features fit together: outside target mode, #44's click-to-select a mask works as before; inside it, clicks go to the target layer.
- **F-42 "correct a box"** can reuse `follow_box`: when a box is dragged to a new spot, re-follow from there. ARCHITECTURE "Direct manipulation" describes that behaviour. It also needs to take back `reviewed` over the corrected range (ARCHITECTURE, "Known limitation").
- **A label picker** (person / face / plate / object) in the target bar, so the timeline and the review queue say what was drawn.
- **Tune `FollowSettings`** on the F-09b labelled clips, and time it on a long 1080p clip on a tier 0 machine.
- **The review panel's note for unanalysed videos** says masks on detected targets cover the whole frame. With only drawn boxes, that note is misleading. It's paulallain's F-41 file, so I didn't change it.

## Gotchas
- **A flat target can't be followed.** Zero-mean NCC scores a patch with no detail as 0, never a false match, so the follower stops right away. The mask then holds the drawn box, marked lost after 0.5 s, and the review queue lists it. Drawing a box with a little background around the target gives it edges to match.
- **A target half out of the frame isn't matched,** because matching needs the whole patch inside the frame. Following stops at the last full sighting, and the renderer's held, growing box covers the exit.
- **Target mode covers the video's own controls.** Seek with the timeline, or press Done.
- **Don't resolve the #44 conflicts by blindly keeping both sides.** In `registry.py`, `client.ts` and `mock.ts`, the conflict boundaries fall in the middle of an entry, so "keep both" gives broken code. Take #44's file and add the F-14 entry back instead.
- **Test clips for the browser must be H.264.** PyAV's `mpeg4` clips (used by the backend tests) don't play in Chrome. `ffmpeg -c:v libx264` works.
