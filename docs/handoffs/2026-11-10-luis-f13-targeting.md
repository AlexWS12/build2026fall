---
author: luis
date: 2026-11-10
branch: luis/f13-targeting-by-description
features: [F-13]
base: v0.1.0 @ 751159b
head: b97f2a0 (nothing of F-13 is committed yet)
status: in progress (first cut committed; review fixes, combined criteria and the VLM judge still open)
---

# F-13 first cut: pick tracks by description ("the person in red"), with a colour stub and a slot for the VLM

> **The path works end to end with a stub judge; the real model isn't chosen or built.** A request like "blur everyone except the person in red" now becomes a plan, runs `find_by_description`, and writes a mask op. The stub only understands clothing colours; everything else comes back *unsure*, which is masked. There are **no measured numbers**: no labeled, consented clip exists (F-09b), so the exit check ("picks the right tracks on a labeled clip") can't be run.

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
Checked on Windows 11, Python 3.12, no GPU used. Full suite (2026-11-10): **864 passed, 1 skipped, 1 failed**; ruff check and format clean. The failure is `test_writing_keeps_the_timeline_permissions` (POSIX `chmod`, Windows only, same as in the F-10 handoff); not F-13.

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

## What is missing to close F-13
Exit check: *a request picks the right tracks on a labeled clip; unsure = masked.* Not met yet:
1. **A real judge.** The colour stub only answers one clothing colour. "With a bag", "in a grey t-shirt and a bag", "the tall one" are all `unsure` today (so masked, and too many tracks). A VLM judge for `local` (and `remote` through F-08's model server) is the main missing piece.
2. **A labeled, consented clip** (F-09b, Jean) and a script that reports right / wrong / unsure per description. Until then every number in this file is an eyeball check, not a measurement.
3. **Review fixes below**, mainly the ones where the stub says a *confident* answer it shouldn't (fail-open).
4. **Team steps:** heads-up about the new tool and `ToolContext.describer`; the owner of the plan executor wires `describer=project_describer(...)`; claim the F-13 row in ROADMAP; D-017 is still *Proposed*.

## VLM choice (not decided; the team picks, then it goes in the ARCHITECTURE decisions log)
Needs: runs on a team machine (D-017: crops are media, they don't go to a third-party API without an opt-in), fits 6 GB (RTX 4050 laptops) or at least the 5070's 12 GB, permissive license, answers `yes` / `no` / `unsure` for one track from 3 crops, ideally several attributes in one call.
- Candidates to evaluate, **none measured by us yet**: a small Qwen2.5-VL (3B / 7B, 4-bit), InternVL, SmolVLM, MiniCPM-V, moondream. Check each one's license (some sizes have different licenses), VRAM at 4-bit, and seconds per track on our hardware.
- How to decide: a script that runs each candidate on the same labeled clip, reports right / wrong / unsure and seconds per track with the hardware stated (same rule as the benchmark). Pick the smallest model that never says a confident wrong "no" on that clip.
- Interface is ready: implement `Judge` (`describe.py`), return `unsure` on any doubt or parse failure. Open design points: batching and progress (a 24 s clip had 677 person tracks), asking several attributes in one prompt, and where fail-closed aggregation lives so every judge shares it.
- The prompt must not ask who someone is (identity is F-52 and biometric under D-017); only "does this match the description".

## Improvements we have in mind
**Behaviour (what the demo showed).** On the 24 s street clip and the grey-t-shirt-plus-bag clip, the code masks hundreds of tracks because most are short fragments and `unsure` is masked. Things that would make the result look like a clean one-box-per-person video, without breaking "unsure = masked":
- **Combine criteria** ("grey t-shirt *and* a bag", any attribute): three-valued AND (any `no` → no, all `yes` → yes, otherwise unsure). A detection-based rule for objects ("with a bag" = overlap with handbag / backpack / suitcase tracks) is cheap and needs no VLM.
- **Track stitching with an appearance check (F-10b).** 451 of 677 person tracks last under 0.2 s; geometric-only stitching merged different people in crowds (prototype rejected, not in the repo). Fewer, longer tracks means fewer judge calls and answers that agree.
- **Per-track lifetime masks** instead of holding the last box: the renderer (F-04) grows the box when a track ends (fail-closed by design), which is why the blur "stayed fixed" on a moving person. Needs the renderer owners' decision.
- **Surface "sure" vs "possible"** in the UI: show confident targets and ask about the unsure ones, instead of silently masking all.
- **Short noise fragments** (under ~5 keyframes) inside a longer track: decide with the team whether they inherit the longer track's verdict. Measured on the grey + bag clip: 349 of 405 masked tracks had under 5 keyframes (155 inside a yes/unsure track, 167 alone, 27 inside a sure-no track), so this is the biggest lever and also the riskiest for privacy.

**Review findings, confirmed and not fixed yet** (an independent review, items I reproduced):
- Planner templates: "except … from 0:10" parses a negated time as the opposite range; the first of two time ranges is dropped; compound requests fold into one description; phrases that aren't descriptions after `in` / `with` / `wearing` become whole-clip descriptions ("in 0:10", "in the video", "with a strong blur", "box everyone in black").
- Stub judge fail-open: confident `no` for navy, burgundy, pastel, dark and teal; confident `yes` on skin tones (e.g. 150,80,60 reads as red); a single usable crop can give a sure verdict (require 2+); a keyframe past the end of the clip is cropped from the last frame yet gets a sure verdict (a test currently locks this in); words the stub can't tokenise are ignored.
- Tool: accepts non-person labels (the stub then answers clothing questions for faces).
- Describer: only `ModuleError` is converted (judge or cache-write errors escape); concurrent `ask` calls lose cache entries; cache entries aren't validated against the request; crops are views that keep full frames alive (`np.ascontiguousarray`); cache key doesn't cover the crop pipeline or the colour constants; punctuation variants make separate cache files.
- Tests: the black-in-the-chest-band test and the past-the-end test lock in unsafe behaviour; missing tests for the template findings, the real describer with `ColorJudge` through the tool, judge and cache exceptions, and the one-usable-crop case.

## Gotchas
- **The stub is easily fooled** by coloured backgrounds, lighting, and a box that includes a barrier or another person. It is there to test the path and give CPU laptops a narrow answer, not to be good.
- **The describer reads the video file.** A project's stored copy is used by `project_describer`; the CLI reads the path you pass. Frames are decoded once per call, in order.
- **Several track sets and no default** make `describe` stop with `AmbiguousAnalysis`, same as everywhere: pass `--key` or set a default (`python -m vibe.modules.vision default`).
- **A request that mixes a described person with other clauses** works ("blur the person in red and mute the audio"), but "blur faces and the person in red" is not planned.
- **Not tested:** a hand-drawn (`user`) track through the describer on a real clip; the `local` / `remote` backends (they raise by design); portrait/rotated videos beyond what `VideoReader` already handles.
