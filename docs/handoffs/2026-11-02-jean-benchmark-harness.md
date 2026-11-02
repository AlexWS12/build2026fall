---
author: jean
date: 2026-11-02
branch: jean/benchmark-harness
features: [F-09]
base: v0.1.0 @ 21ee75c
head: (set after the first commit)
status: in progress
---

# F-09 benchmark harness, first cut: clip manifest, label format, vision scoring and `report`

> **No shared file changes.** New subpackage `backend/src/vibe/benchmark/`, a one-line entry in ARCHITECTURE "Code layout", and my own F-09 rows in the roadmap. The **planner is not scored here**: R-1's bench (`vibe.agent.bench`, on `paul/r1-planner-llm`) already does that on the shared request set, so this doesn't build a second scorer, and `backend/tests/agent/requests_draft.json` stays where it is (F-06 and R-1 both edit it on their branches).

## What changed
- **`backend/src/vibe/benchmark/`** (run as `uv run python -m vibe.benchmark ...`):
  - `manifest.py` and `clips.json`: the clip manifest. One entry per clip: content hash (a storage `VideoRef`), size, duration, resolution, exact fps, audio, `source`, `consent`, `consent_cloud` (R-4: does the consent cover third-party cloud processing; default `false`), `license`, `tags`. Videos are never committed. `add-clip FILE` probes the file, hashes it and stores it once with `Storage.add_video`. A file already in the manifest is just stored, which is how teammates get the team's clips. A new one needs `--id`, `--source` and `--consent`, and nothing is stored or written until every check passes. `clip_status` says whether this machine has the right file (fast: size; `--verify`: re-hash and re-probe).
  - `labels.py` and `labels/<clip_id>.json`: ground truth per clip. Visual targets are contract `Track`s (normalized boxes, D-013), so a model's tracks and the truth have the same shape. Speech segments (speaker, text) are in the format for F-31/F-32 but not scored yet. `check_labels` catches labels for the wrong video and times past the clip's end. The module docstring is the labelling guide.
  - `score.py`: compares a model's tracks with the labels at every ground-truth keyframe. **Detection:** one-to-one matching at IoU >= 0.5, giving recall and precision. **Exposure:** is the true box at least 90% covered by a predicted box grown by the effect padding (0.15, the contract default)? If not, the target would still be visible after masking. Both are reported because they disagree: a big shifted box is a bad detection but a good mask, and a tight box is a good detection that leaks a chin. Predictions are interpolated between keyframes like a tracker, and only the same label counts.
  - `report.py`: `report` checks every manifest clip and its labels, then scores **every** cached track set for the clip (`cache/<sha256>/tracks/<key>.json`), so two models come out side by side. Every report states the commit, uncommitted changes, CPU, threads, GPU (NVIDIA via `nvidia-smi`), Python and backend. `--json` prints it as JSON; `--out DIR` writes `report.json` and `report.md`. Output is ASCII so Windows consoles can pipe it. Exit 1 if anything needs fixing (invalid labels, a wrong file, unreadable cached tracks); a clip that just isn't on this machine is reported as `missing`, not as a failure, so CI passes without the videos.
  - `new-labels CLIP_ID` writes an empty labels file to start from.
- **Tests, `backend/tests/benchmark/`:** 51 tests, no GPU, no network. Clips are generated with the ffmpeg CLI (flags that work on ffmpeg 4.4+) and skipped without it unless `VIBE_REQUIRE_FFMPEG=1`, as CI sets. The scoring tests show the geometry each expectation comes from. Checked by mutation: removing padding, matching across labels, or letting one box detect two faces each makes a test fail. `test_smoke.py` now imports `vibe.benchmark` too.
- `docs/ARCHITECTURE.md`: `benchmark/` in "Code layout". `docs/ROADMAP.md`: my branch in the Sprint 2 F-09 row; backlog status `in progress`.

## How to check it
```bash
cd backend
uv run pytest tests/benchmark tests/test_smoke.py -q      # 62 passed
uv run ruff check && uv run ruff format --check
uv run python -m vibe.benchmark report                    # the exit-check table (no clips yet)
```
Verified on 2026-11-02 in a Linux VM (Ubuntu 22.04, kernel 6.8, 2 vCPUs of an Intel i9-13900H, no GPU), Python 3.12.15, uv 0.13.4, ffmpeg 4.4.2. Full suite: 322 passed. The other 8 (1 failed, 7 errors, all in `tests/video/`) also fail on unchanged `main` in that VM, because ffmpeg 4.4 lacks the `-fps_mode` and `-display_rotation` flags their fixtures use; CI's ffmpeg is newer. CI hasn't run yet.

## Decisions made
jean made these on 2026-11-02 for F-09; say on the PR if you disagree. None is architecture-level, so there's no decisions-log row.
- **Planner scoring stays with R-1.** One scorer per thing measured. R-1's bench scores plans by what they do; F-09 scores vision against ground truth.
- **Exposure is the headline privacy number.** The privacy edits fail closed, so the number that matters for "blur every face" is how often a face stays visible after masking, not IoU alone.
- **Padding means "grow width and height by `padding`, centred, clipped to the frame".** The renderer (F-04) owns this. If F-04 does it differently, `score.pad` changes to match, so exposure measures what a viewer would see.
- **Cached tracks are a JSON list of contract `Track`s** (the shape of `tracks.schema.json`), one file per model and settings key. That's an assumption until F-08 settles the cache format; a file in another shape shows up as `unreadable`, never as a silent zero.
- **The manifest is written by code** (`add-clip`), and a test checks the committed file is in that exact form, so diffs stay readable.

## Next steps
- **jean:** film or pick 3 to 5 consented clips (faces, people, a plate, speech, handheld, one low-light), put them on the team drive, register each with `add-clip`, label them (`new-labels`, then a keyframe per second per target), and commit `clips.json` and `labels/`. That closes the exit check ("clips listed in the manifest").
- **F-08 / F-10:** write detector output to `Storage.cache_path(sha256, "tracks", key)` as a list of `Track`s, and `report` scores it against the labels with no other change.
- **F-04:** confirm or correct the padding meaning above.
- **R-1 (paul):** asked for a teammate to review the gold plans in `requests_llm.json`. I'll do it.
- **Later:** speech scoring (F-31/F-32: word timing, diarization), per-stage timing for F-50 on the 5070, and maybe `--min-coverage` / `--iou` flags once someone needs other thresholds.

## Gotchas
- **Labels are only as good as their keyframes.** Scores are taken at ground-truth keyframes, so a target with no keyframe in a stretch isn't measured there. One per second while visible, plus entry and exit.
- **Same label or nothing.** A `person` box over a face doesn't count for `face`, because "blur faces" masks face tracks only. Label with the detector's names (`face`, `person`, `license_plate`).
- **`--verify` reads every clip in full** (hash and probe), so it's slow on long 4K clips; the default only checks sizes.
- **On Windows, don't point a Linux shell at the repo's `.venv`.** If you test from WSL or a VM, set `UV_PROJECT_ENVIRONMENT` to a folder outside the repo, or uv rebuilds the venv and breaks the Windows one.
