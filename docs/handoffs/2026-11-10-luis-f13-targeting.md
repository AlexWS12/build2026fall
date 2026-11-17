---
author: luis
date: 2026-11-10
branch: luis/f13-targeting-by-description
features: [F-13]
base: origin/main @ e4a96f9 (merged in, not rebased)
head: see the PR
status: in progress (PR #33 review fixes done; the VLM judge, D-020, is the next PR)
---

# F-13 first cut: pick tracks by description ("the person in red"), with a colour stub and a slot for the VLM

> **The path works end to end with a stub judge (plus a detection-based rule for objects); the real model isn't chosen or built.** A request like "blur everyone except the person in red" now becomes a plan, runs `find_by_description`, and writes a mask op. The stub only understands clothing colours; everything else comes back *unsure*, which is masked. There are **no measured numbers**: no labeled, consented clip exists (F-09b), so the exit check ("picks the right tracks on a labeled clip") can't be run.

> **Shared file changes, heads-up needed before this merges:** a new toolbox tool `find_by_description` (`toolbox/registry.py`, `tools.py`) and a new optional field `ToolContext.describer` (`toolbox/context.py`). Both are additive: no existing tool, argument or op changes, no `schema_version` bump, no dependency changes.

Plan with the full reasoning: [`docs/research/f13-targeting-by-description-plan.md`](../research/f13-targeting-by-description-plan.md).

## What changed
- **`backend/src/vibe/modules/vision/describe.py` (new).** For each track it crops 3 keyframes from the video, asks a `Judge`, and returns `yes` / `no` / `unsure` per track.
  - `Judge` is the interface (`judge(crops, label, description)`); `ColorJudge` is the stub (numpy only, HSV): it answers one clothing colour ("in red", "wearing a blue shirt") by measuring the chest region of a `person` box (the middle of any other box). Thresholds: yes at 35% of those pixels, no at 8% or less, unsure between. **Starting points, not measured.** Any other word ("hat", "tall", two colours) gives `unsure`.
  - `make_judge()` returns the stub for `VIBE_BACKEND=stub` and raises `DescribeError` for `local` / `remote`, the way `denoise` does, rather than answering with the colour stub on a backend that should use a VLM.
  - `VideoDescriber` caches verdicts in `cache/<sha256>/attributes/<judge>-<hash>.json`, one file per judge and description. Each entry stores a signature of the track's sampled boxes, so a hand-corrected box under the same id is asked again. Crops are never written. `project_describer(storage, project)` sits next to `project_tracks`.
- **Toolbox.** `find_by_description(label, description, exclude=false)` returns `{track_ids, count, unsure}` and `returns="track_ids"`, so a plan passes it to `add_mask` as a `$ref` like `find_objects`. **Unsure tracks are always returned:** "the person in red" masks `yes` + `unsure`; with `exclude`, "everyone except the person in red" masks `no` + `unsure`. No analysis, no describer, or a describer that can't look is a `ToolError`, never an empty result.
- **Planner templates (`agent/templates.py`).** After a person noun the parser takes `in …`, `wearing …`, `with …`, `dressed in …`, optionally behind `except / but / other than`, and an optional time after it ("… in red from 0:10 to 0:20", "… for the first half"). Still declined, so the planner asks: faces ("faces in red"), "except me" (identity, F-52), "the guy in red", "on the left".
- **CLI.** `python -m vibe.modules.vision describe VIDEO "in red" [--label person] [--key KEY] [--all]` prints a count of verdicts and the yes / unsure tracks (`--all` adds the nos).
- **Tests:** `tests/vision/test_describe.py`, `tests/toolbox/test_find_by_description.py`, `tests/agent/test_templates_described.py`, plus the registry and template lists updated. No GPU, no network.

## How to check it
```bash
cd backend
uv run pytest tests/vision tests/toolbox tests/agent -q
uv run ruff check && uv run ruff format --check
# on a clip you already analysed (several track sets need --key):
uv run python -m vibe.modules.vision describe CLIP.mp4 "in red" --key <tracks key>
```
Checked on Windows 11, Python 3.12, no GPU used. Full suite (rebased on origin/main 1642a7d): **1382 passed, 2 skipped, 1 failed**; ruff check and format clean. The failure is `test_writing_keeps_the_timeline_permissions` (POSIX `chmod`, Windows only, same as in the F-10 handoff); not F-13.

**Run by hand on a real clip** (the street clip from F-10, `rfdetr-s` + ByteTrack, 549 person tracks, CPU only): "in red" gave 6 yes, 29 unsure, 514 no. I looked at crops of one track of each: the yes was a person in a magenta top, the unsure a maroon top next to a red barrier, the no a white shirt. **That is an eyeball check on three tracks, not a measurement.** Through the planner, "blur the person in red" masked 35 of 549 tracks (29 only because unsure), and "blur everyone except the person in red" masked 543 (the same 29 unsure). "Blur everyone except me" still asks the user.

## Decisions made
luis made these on 2026-11-10; say on the PR if you disagree. None is architecture-level.
- **Unsure is masked on both sides** (include and exclude). The only tracks ever left unmasked are ones the judge is sure about. A track the describer says nothing about counts as unsure.
- **A verdict has three values, and a real judge must keep them.** A VLM forced into yes/no would turn doubt into a wrong "no", which leaves someone unmasked.
- **Descriptions go to the judge in the user's words**; the tool doesn't parse them. The stub parses colours itself, and a VLM would read the sentence.
- **Verdicts are cached, crops are not.** The cache folder is `attributes`, not biometric under D-017: a verdict says "matches 'in red'", never who someone is.
- **Face + description is not planned.** "Blur faces in red" needs faces linked to people (F-10b), so the parser declines it instead of masking all faces or none.

## Next steps
1. **Heads-up to the team** about the new tool and `ToolContext.describer`, then commit and open the PR. Needs the owner of the plan executor (F-06 / F-64) to know the context needs `describer=project_describer(storage, project)` next to `tracks=project_tracks(...)`.
2. **Choose the VLM** (decision row in ARCHITECTURE, ask the team first): fit in 6 GB (RTX 4050) or the 5070, license, accuracy on crops, seconds per track. Numbers only from a script, hardware stated. D-017 is still *Proposed*; the VLM must run on a team machine.
3. **Build the VLM judge** for `local`, then `remote` through F-08's model server.
4. **Measure the exit check** once Jean has consented, labeled clips (F-09b): a script that reports right / wrong / unsure per description. Needs Jean's OK to add it to `vibe.benchmark`.
5. **Claim the F-13 row in `docs/ROADMAP.md`** (owner and branch; it says `_open_`).
6. **Track stitching (F-10b)** matters here: 549 person tracks in a 24-second clip means a judge call per fragment and answers that can disagree between pieces of one person.

## Second step: combined criteria, following the target, review fixes
Done on top of the first cut, after watching the demo video where one box followed the right person.
- **Several criteria in one request.** "blur the person in a grey t-shirt with a bag" is split into "in a grey t-shirt" (the judge) and "with a bag" (answered from the tracks: is a handbag / backpack / suitcase track inside the person's box in at least half of their keyframes). Combined three-valued: any `no` is a no, all `yes` is a yes, else `unsure`. Any detector label works as the object ("with a dog"); words like "bag" or "phone" map to labels. "With a red bag" needs eyes, so it goes to the judge as a whole. A judge that reads sentences (`Judge.compound = True`, the VLM) gets the whole description instead.
- **No object track anywhere = unsure, not no** (maybe the detector never looked for bags). Since the PR review, the object rule never says `no` at all (see "Third step").
- **The mask follows the target.** `add_mask(while_tracked=True)`: each target is masked from just before its track starts to just after it ends (since the PR review: at least the renderer's 0.5 s hold, plus up to a quarter-second grid; see "Third step"), targets that come and go together share one op, and it stays inside the requested range. The planner sets it for one described person; "everyone except ..." keeps whole-range masks because it hides bystanders.
- **"Only if sure."** `find_by_description(unsure="leave")`, only when the user says so in words ("only if sure", "the sure ones only"). Default is still `mask`. Nobody sure is an error, not an empty result; the unsure ones are still reported (`unsure`, `left_out`).
- **Review findings fixed:** stub judge (dark / pale / teal colours are unsure, skin is not red or orange, colour elsewhere in the box blocks a "no", one crop is never enough, unreadable words make it unsure, clothing words on non-person labels are unsure); keyframes past the end of the video give no crop; crops are copies; cache entries are validated and re-read before writing, a cache key covers the crop pipeline; every module error comes out as `DescribeError`; the planner declines negated or duplicated times, a second target in one description, identity words, places, effect words ("with a strong blur"), "box everyone in black".
- **The executor now passes a describer** (`execute_plan(describer=...)`, `execute_request` builds `project_describer`), so the chat path works. On a backend with no judge, only a request that describes someone fails, with the reason.
- **Tests:** 1451 passed, 2 skipped, 1 failed (the Windows-only permissions test). ruff clean.

**Run by hand** (4791180 clip, 28 s, `rfdetr-s` + ByteTrack, 677 person tracks, RTX 4050 Laptop, CPU stub judge; eyeball checks, not measurements):
- "in a grey t-shirt with a bag": grey 56 yes / 614 unsure / 7 no; bag 49 / 540 / 88; combined **11 yes / 572 unsure / 94 no**.
- "…, only if sure": 11 tracks, 11 mask ops, 841 frames rendered in 28 s. I checked that the mask of the woman in the grey t-shirt with the white bag (track 23) sits on her box in every frame I sampled between 0.3 s and 4.4 s: she moves from x=0.87 to 0.74 of the frame, and she is small (about 58 px wide), so the box looks nearly still while the camera pans with her.
- Default (unsure masked): 583 person tracks, 245 ops, 841 frames in 100 s, 87 render notes. Correct by the privacy rule, and crowded: this is the cost of 572 unsure tracks, most of them fragments under 5 keyframes.
- Wrong or doubtful in the sure-only video: one of the 11 sure tracks is a second person (man in a grey t-shirt pushing a stroller with a pink bag; arguably a match); two are 2-keyframe fragments.

## Third step: Edugre's review on PR #33, and main merged in
luis did these on 2026-11-17, after merging `origin/main` (e4a96f9: F-41, F-43, F-09b, D-020) into the branch. The merge had two conflicts, both in the toolbox imports: main's `HAND_TOOLS` / `UI_TOOLS` sit next to `Describer`, and main renamed `MuteSpan` to `Span` in that import.
- **The object rule never says `no`** (`with_object`, review item 1). Before, a person seen in 10+ keyframes with no bag box inside theirs got `no`, which made the whole description `no` and left them unmasked whenever the detector missed their bag. Now it is `yes` or `unsure`; only a judge looking at pixels can rule someone out. Cost: "with a bag" narrows only through `yes`, so more people are masked until the VLM. `OBJECT_MIN_SEEN` is gone.
- **The object rule counts only `detected` / `user` keyframes** (item 2), like `sample_keyframes`: predicted boxes may have drifted onto someone else's bag.
- **A followed mask lasts at least the renderer's hold** (item 3). `TRACKED_PAD_S` is now 0.5 s (was 0.25), then rounded outward to the quarter-second grid, so 0.5 to 0.75 s each side. Before, the mask could stop up to 0.25 s before the renderer's `HOLD_SECONDS`, at each end and at every handover between fragments of one person. Two fragments up to 1 s apart now overlap. The constant isn't imported from `render.geometry` (that loads av and numpy into the planner, which a test forbids); a test keeps `TRACKED_PAD_S >= HOLD_SECONDS`. Cost: past the hold, the renderer keeps the held box and marks it lost, so a few more render notes.
- **"Only if sure" works before a time too** (item 4, a bug): "blur the person in red only if sure from 0:10 to 0:20" used to keep "only if sure" in the description, so the stub was unsure about everyone and everyone in the range was blurred. The template now looks for it on both sides of the time. Any other "sure" / "certain" / "unsure" word in a description makes the template decline, so the planner asks.
- **"And" splits only before a new criterion** (item 5): "in black and white", "wearing a red shirt and a hat" and "with a black and white dog" stay whole (unsure for the stub, so masked). "In red, and carrying a bag" still splits. What "and" between colours means is left to the VLM, which reads the sentence.
- **Not re-run by hand:** the street-clip numbers in the second step predate these fixes. With the object rule never saying `no`, the 94 combined `no`s for "in a grey t-shirt with a bag" will drop (some become `unsure`, so masked).
- **Checks:** Windows 11, Python 3.12, no GPU. `ruff check` and `ruff format --check` clean. Full suite 1529 passed, 2 skipped, 1 failed (`test_writing_keeps_the_timeline_permissions`, POSIX `chmod`, Windows only).

## What is missing to close F-13
Exit check: *a request picks the right tracks on a labeled clip; unsure = masked.* Not met yet:
1. **A real judge.** The colour stub only answers one clothing colour, and objects the detector has labels for. "The tall one", "with a red bag", "wearing glasses" are all `unsure` today (so masked, and too many tracks). A VLM judge for `local` (and `remote` through F-08's model server) is the main missing piece.
2. **A labeled, consented clip** (F-09b, Jean) and a script that reports right / wrong / unsure per description. Until then every number in this file is an eyeball check, not a measurement.
3. **The remaining review notes below** (small; none leaves anyone unmasked).
4. **Team steps:** heads-up about the new tool and `ToolContext.describer` (the executor already passes a describer, see the second step); D-017 and D-020 are still *Proposed*.

## VLM choice (D-020 proposes Qwen3.5-4B local / 9B on the 5070; accepted only after it's measured)
The notes below are from before D-020; see that row in `docs/ARCHITECTURE.md` and `docs/handoffs/2026-11-15-eduardo-d020-vlm-qwen.md`.
Needs: runs on a team machine (D-017: crops are media, they don't go to a third-party API without an opt-in), fits 6 GB (RTX 4050 laptops) or at least the 5070's 12 GB, permissive license, answers `yes` / `no` / `unsure` for one track from 3 crops, ideally several attributes in one call.
- Candidates to evaluate, **none measured by us yet**: a small Qwen2.5-VL (3B / 7B, 4-bit), InternVL, SmolVLM, MiniCPM-V, moondream. Check each one's license (some sizes have different licenses), VRAM at 4-bit, and seconds per track on our hardware.
- How to decide: a script that runs each candidate on the same labeled clip, reports right / wrong / unsure and seconds per track with the hardware stated (same rule as the benchmark). Pick the smallest model that never says a confident wrong "no" on that clip.
- Interface is ready: implement `Judge` (`describe.py`), return `unsure` on any doubt or parse failure. Open design points: batching and progress (a 24 s clip had 677 person tracks), asking several attributes in one prompt, and where fail-closed aggregation lives so every judge shares it.
- The prompt must not ask who someone is (identity is F-52 and biometric under D-017); only "does this match the description".

## Improvements we have in mind
**Behaviour (what the demo showed).** On the 24 s street clip and the grey-t-shirt-plus-bag clip, the code masks hundreds of tracks because most are short fragments and `unsure` is masked. Things that would make the result look like a clean one-box-per-person video, without breaking "unsure = masked":
- ~~Combine criteria~~ (done in the second step).
- **Track stitching with an appearance check (F-10b).** 451 of 677 person tracks last under 0.2 s; geometric-only stitching merged different people in crowds (prototype rejected, not in the repo). Fewer, longer tracks means fewer judge calls and answers that agree.
- **Per-track lifetime masks** (done in the toolbox as `while_tracked`, no renderer change). The renderer (F-04) still holds and grows the last box when a track ends, by design; the renderer owners may want to tell the executor how long a target can be gone before it counts as lost.
- **Surface "sure" vs "possible"** in the UI: show confident targets and ask about the unsure ones, instead of silently masking all.
- **Short noise fragments** (under ~5 keyframes) inside a longer track: decide with the team whether they inherit the longer track's verdict. Measured on the grey + bag clip: 349 of 405 masked tracks had under 5 keyframes (155 inside a yes/unsure track, 167 alone, 27 inside a sure-no track), so this is the biggest lever and also the riskiest for privacy.

**Review findings, confirmed** (an independent review before the PR, then Edugre's review on PR #33). Everything from the first review is fixed (second step); Edugre's items 1 to 5 are fixed in the third step. **Still open:**
- **Describer:** two `ask` calls at the same time can still lose each other's cache entries (it re-reads before writing, but there is no lock). The cache key doesn't cover the colour stub's constants, so tuning them needs a `_CROPS_VERSION` bump by hand.
- **Memory and speed:** every crop of every track is held before anything is judged (about 2,000 crops on the 677-track clip). Judge each track as its last crop is read, in batches, with progress: part of the VLM work.
- **Two word lists:** `_NOT_A_DESCRIPTION` (templates) and `ColorJudge._FILLER` / `color_in` overlap and will drift. One shared vocabulary later.
- **Not tested:** a hand-drawn (`user`) track through the describer on a real clip.

## Gotchas
- **The stub is easily fooled** by coloured backgrounds, lighting, and a box that includes a barrier or another person. It is there to test the path and give CPU laptops a narrow answer, not to be good.
- **The describer reads the video file.** A project's stored copy is used by `project_describer`; the CLI reads the path you pass. Frames are decoded once per call, in order.
- **Several track sets and no default** make `describe` stop with `AmbiguousAnalysis`, same as everywhere: pass `--key` or set a default (`python -m vibe.modules.vision default`).
- **A request that mixes a described person with other clauses** works ("blur the person in red and mute the audio"), but "blur faces and the person in red" is not planned.
- **Not tested:** a hand-drawn (`user`) track through the describer on a real clip; the `local` / `remote` backends (they raise by design); portrait/rotated videos beyond what `VideoReader` already handles.
