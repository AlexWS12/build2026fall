# F-13: Targeting by description — implementation plan

**Status:** plan, not merged. A first draft of steps 1–5 exists uncommitted on `luis/f13-targeting-by-description`; nothing below is on `main`.
**Owner:** Luis. **Depends on:** F-10 (merged, PR #22). **Card:** `docs/ROADMAP.md`, Sprint 4, "F-13 Targeting by description".

## 1. What the card asks for

> F-13 Targeting by description ("the person in red", "everyone except me"). Exit check: a request picks the right tracks on a labeled clip; unsure = masked.

F-10 finds every person, face and plate in a video and gives each one a track id. `find_objects` can only filter those tracks by label, so "blur people" works and "blur the person in red" does not: today the planner answers with a question.

F-13 adds the missing step: **given the tracks and a description in words, decide which tracks the user means.** The result is a list of track ids, the same shape `find_objects` returns, so `add_mask` and the renderer need no change.

## 2. Rules already fixed elsewhere

| Rule | Where it comes from | What it means for F-13 |
|---|---|---|
| The planner never sees pixels | D-017 (Proposed), ARCHITECTURE "Agent / planner" | The LLM only names the description. A separate vision model looks at the video |
| VLM yes/no per track | ARCHITECTURE, Knight Hacks lessons | Detect everyone first, then ask one question per track. Don't ask an open-vocabulary detector for "guy in red" |
| Media stays on team machines | D-017 | The VLM runs locally or on the 5070 through the `remote` backend. A cloud VLM needs the project's opt-in and consented clips |
| Privacy fails closed | CLAUDE.md "Fail safe" | "Unsure" is masked. An error is never treated as "nobody matches" |
| Stub mode, tests without a GPU | CLAUDE.md "Architecture" | The module must give an answer with no model installed |
| Tech choices are tentative | CLAUDE.md "Tentative vs decided" | Which VLM is a team decision with a decision row, not something this card picks silently |
| Shared files need a heads-up | HANDOFF.md "Shared files" | A new toolbox tool and a new `ToolContext` field must be announced before they're committed |

## 3. Design

```
request text ──► planner ──► plan: find_by_description ──► add_mask
                                     │
                 ToolContext.tracks  │  ToolContext.describer
                 (F-10 cache)        ▼
                          VideoDescriber (per video, cached)
                                     │  a few crops per track
                                     ▼
                          Judge: yes / no / unsure per track
                          stub = clothing colour · local/remote = VLM (open)
```

### 3.1 The three verdicts and how they become targets

| Request | Tool call | Tracks returned |
|---|---|---|
| "blur the person in red" | `exclude=false` | `yes` + `unsure` |
| "blur everyone except the person in red" | `exclude=true` | `no` + `unsure` |

In both cases an unsure track is masked. The only tracks ever left unmasked are the ones the judge is sure about. A track the judge gave no answer for counts as unsure. The result also lists which ids were included because of "unsure", so the agent can say so (F-40) and the review queue can show them (F-41).

### 3.2 Pieces

**A. `vibe.modules.vision.describe` (new module)**
- `Judge`: interface with one method, `judge(crops, label, description) -> "yes" | "no" | "unsure"`. Has a `name`, a `version` and `settings`, all hashed into the cache key.
- `sample_keyframes(track, n=3)`: which keyframes to crop. Spread over the track, skipping the first and last (targets entering the frame are half cut off). Keyframes a model saw (`detected`) or a person drew (`user`) come first; `uncertain` / `tracked` ones are used only when there are no others.
- `describe_tracks(video, tracks, description, judge)`: reads each needed frame once, in order, crops the boxes, asks the judge. A track with no usable crop (box under 4 px) is unsure. An unreadable video raises `DescribeError`.
- `VideoDescriber(storage, sha256, video, judge)`: the object the toolbox talks to. Caches verdicts in `cache/<sha256>/attributes/<judge>-<hash>.json`, one file per judge and description. Each entry stores a signature of the track's sampled boxes, so a hand-corrected box under the same id is asked again. Only tracks not yet answered reach the judge. Crops are never written to disk.
- `project_describer(storage, project)`: builds the describer for a project, next to the existing `project_tracks`.

**B. Stub judge: `ColorJudge`**
- numpy only. Converts the crop to HSV and measures what share of the middle of the crop is the asked colour (for a `person` box: the chest region).
- Answers only when the description is one colour plus connectors and upper-body clothing words ("in red", "wearing a blue shirt"). Anything else ("wearing a hat", "the tall one", two colours) is unsure.
- Two thresholds with a gap between them (yes ≥ 0.35, no ≤ 0.08, unsure in between). **These are starting points, not measured.**
- Purpose: a real, narrow answer on CI and CPU laptops, and a way to test the whole path end to end.

**C. Real judge (VLM): a slot, not built**
- `make_judge()` returns the stub for `VIBE_BACKEND=stub` and raises a clear error for `local` / `remote`, the way `denoise` does, instead of quietly answering with the colour stub.
- Filled in after the model decision (section 6).

**D. Toolbox (shared files)**
- `ToolContext.describer: Describer | None`. `None` means nothing here can look at the video.
- New query tool `find_by_description(label, description, exclude=false)` → `{track_ids, count, unsure}`, `returns="track_ids"`, so a plan can `$ref` it as `add_mask` targets exactly like `find_objects`.
- Errors, never empty results: no analysis (`tracks=None`), no describer, or a describer that can't look.

**E. Planner templates**
- After a person noun the parser accepts a description starting with `in`, `wearing`, `with` or `dressed in`, and an optional `except … / but … / other than …` in front.
- A time phrase may follow the description ("… in red from 0:10 to 0:20", "… for the first half"); the longest ending that parses as a time is the time.
- Still declined, so the planner asks: "blur faces in red" (needs faces linked to people), "blur everyone except me" (identity, F-52), "blur the guy in red" and "the person on the left" (left to the LLM planner).
- The LLM planner (R-1b) sees the new tool through the registry with no extra work.

**F. CLI**
- `python -m vibe.modules.vision describe VIDEO "in red" [--label person] [--key KEY]` prints a verdict per track, for trying it by hand on a real clip.

## 4. Steps and exit checks

| # | Step | Files | Done when |
|---|---|---|---|
| 1 | Describe module + stub judge + cache | `backend/src/vibe/modules/vision/describe.py` | Tests: colours, sampling, crops, cache hits, corrected box re-asked, unreadable video raises |
| 2 | Toolbox tool + context field | `toolbox/context.py`, `tools.py`, `registry.py`, `__init__.py` | Tests: include / exclude / unsure / missing answer / errors. **Heads-up posted first** |
| 3 | Planner templates | `agent/templates.py` | Tests: described requests plan `find_by_description` → `add_mask`; declined cases still give `None` |
| 4 | CLI `describe` | `modules/vision/__main__.py` | Test on the synthetic clip; one manual run on a real analysed clip |
| 5 | Full suite, lint, format | — | `uv run pytest`, `ruff check`, `ruff format --check` clean (one known Windows-only failure, section 7) |
| 6 | VLM decision | `docs/ARCHITECTURE.md` decision row (ask the team first) | Candidates compared on the reference machine; row accepted |
| 7 | VLM judge, `local` | `describe.py` or `vision/judges/` | Runs on a real clip on a GPU; hardware stated in the handoff |
| 8 | VLM judge, `remote` | fit under F-08's `run_analysis` / model server | A laptop with no GPU gets verdicts from the 5070 |
| 9 | Measure the exit check | `vibe.benchmark` (Jean's, needs their OK) | A labeled clip says which track is "the person in red"; a script reports right / wrong / unsure |
| 10 | Handoff + ROADMAP row | `docs/handoffs/`, `docs/ROADMAP.md` | Says what works, what was not tested |

Steps 1–5 need no GPU and no decision. Steps 6–9 are blocked on things outside this card (section 5).

## 5. What F-13 does not cover, and what blocks it

- **"Everyone except me" is identity, not description.** It needs face matching against an enrolled user (F-52), which is biometric data under D-017. F-13 alone keeps asking the user for that request.
- **No plan executor yet.** It's an open Sprint 4 card. Until it exists, a request is planned but not applied. Whoever builds it must pass `ToolContext(tracks=project_tracks(...), describer=project_describer(...))`.
- **No labeled, consented clips** (F-09b open), so the exit check can't be measured. Stock clips don't qualify.
- **D-017 is still Proposed.** F-13 follows it; if the team changes it, where the judge may run changes too.
- **Fragmented tracks** mean more judge calls and answers that disagree between pieces of the same person. Stitching is F-10b.
- **Descriptions the stub can't check** ("on the left", "the tall one", "holding a phone") are all unsure, so everyone is masked. Correct for privacy, useless for selection, until the VLM lands.

## 6. Open decisions

1. **Which VLM.** Not chosen. To compare: does it fit next to the detector in 6 GB (RTX 4050) or need the 5070; license; yes/no accuracy on crops; seconds per track. Numbers only from a script, hardware stated.
2. **Where it runs by default**: local when a GPU is there, else `remote` to the 5070. Cloud only with the opt-in D-017 describes.
3. **How many crops per track** (default 3). More is slower and may be more stable; measure, don't guess.
4. **Whether relative position ("on the left") belongs here** as a geometric rule on the boxes with no model, or goes to the VLM.

## 7. Risks and known issues

- A wrong **"no"** on an include request, or a wrong **"yes"** on an exclude request, leaves someone unmasked. The unsure band is the only guard; the VLM judge needs the same three-way answer, not a forced yes/no.
- The colour stub is fooled by coloured backgrounds, lighting and partial occlusion. It is a stub, not a quality bar.
- Verdicts are cached. A better judge has a different name or version, so it gets its own cache file and never serves the old answers.
- `test_writing_keeps_the_timeline_permissions` fails on Windows (POSIX `chmod`); it passes on CI (Linux) and is unrelated to F-13.

## 8. Before the first commit

- Post in the team chat: new toolbox tool `find_by_description` and new optional `ToolContext.describer` (additive, nothing existing changes).
- Claim the F-13 row in `docs/ROADMAP.md` (owner, branch).
- Decide whether this plan stays here in `docs/research/` or folds into the F-13 handoff.
