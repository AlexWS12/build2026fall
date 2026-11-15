---
author: eduardo
date: 2026-11-15
branch: eduardo/f09b-pexels-clips
features: [F-09b]
base: v0.1.0 @ 1642a7d
head: fd798f2
status: ready for review
---

# F-09b first clips: 9 licensed Pexels clips in the manifest, person labels for 5 of them

> **Exit check not met yet** ("`benchmark report` scores an F-10 run against real labels"): the labels are in, but no detector has written tracks for these clips, so there's nothing to score. Running F-10 on them closes it.

> **Heads-up for jean:** these are the first entries in jean's `clips.json` and `labels/` (F-09), and filming clips was on jean's F-09 next steps. No code changed; only the committed manifest and label files. Not posted yet: eduardo needs to tell jean.

## What changed
- **`backend/src/vibe/benchmark/clips.json`: 9 clips**, registered with `add-clip`. All are Pexels stock footage under the Pexels License, picked for F-13 (people in distinct clothes, people with bags, crowds).
  - `consent`: "Licensed stock footage (Pexels License); no consent recorded from the individuals. Internal benchmark use only; do not redistribute."
  - `consent_cloud`: **false** for all (D-017: nobody in them agreed to cloud processing).
  - `source`: Pexels id, creator and page URL; tags: `people`, `clothing`, `bags`, `crowd`, `portrait`, `pastels`, `cut-off`, `small-targets`.

| Clip | Length | Size | Labels |
|---|---|---|---|
| trio-street-outfits | 11.3 s | 1920x1080 | 3 people, 39 keyframes |
| trio-skatepark-pastels | 12.0 s | 1920x1080 | 3 people, 37 keyframes |
| trio-sidewalk-totes | 10.0 s | 1080x1920 (portrait) | 3 people, 27 keyframes |
| pair-backpacks-behind | 7.5 s | 1280x720 | 17 people, 65 keyframes |
| hikers-backpacks | 8.6 s | 1920x1080 | 6 people, 54 keyframes |
| street-torsos | 77.4 s | 1920x1080 | none |
| crossing-crowd-above | 11.9 s | 1080x1920 (portrait) | none |
| crowd-dark-coats | 14.9 s | 1920x1080 | none |
| crowd-orange | 4.0 s | 1920x1080 | none |

- **`backend/src/vibe/benchmark/labels/`: 5 label files**, `person` tracks only, `source="user"`.
  - Keyframes about once a second while visible, plus extra ones at entries and exits.
  - **How they were made:** Claude drafted every box from frames with a 10% grid; eduardo then reviewed an overlay of every keyframe and approved them. They are hand-checked estimates, not pixel-exact boxes.
  - Each file's `notes` lists who is who (clothing per track id) and its limits.
- **`docs/ROADMAP.md`:** F-09b claimed (eduardo, this branch, in progress).

## How to check it
Get the videos (not in git): download each Pexels page in `clips.json` at the listed size, or ask eduardo for `~/vibe-clips/pexels/`, then:
```bash
cd backend
uv run python -m vibe.benchmark add-clip PATH/TO/pexels-<id>.mp4   # for each; the manifest already knows them
uv run python -m vibe.benchmark report      # 9 clips ok, 5 with labels, "Problems: none"
uv run pytest tests/benchmark -q
```
Checked on macOS (Apple M2, no GPU): `report` lists all 9 clips `ok` and 5 label files valid; backend `pytest` 1318 passed; frontend `npm test` 53 passed; ruff clean. **Not checked:** any score, since no detector tracks exist for these clips yet.

## Decisions made
eduardo made these on 2026-11-15; say on the PR if you disagree. None is architecture-level.
- **Licensed stock is allowed** (README "Constraints": "properly licensed public video"). The manifest says plainly that nobody in the clips consented, and cloud processing stays off.
- **Person boxes cover the body**, not carried things (skateboards, tote bags hanging outside it). If the team wants carried objects inside the person box, these need widening.
- **Partly hidden people are boxed by their visible part**; someone almost fully hidden has no box for that second.
- **In busy clips, everyone at least ~10% of the frame height is boxed**; smaller figures aren't, so a detector that finds them is scored as a false positive. Stated in that clip's notes.
- **Track ids across seconds are best guesses for passers-by.** `score.py` matches boxes per moment, so ids don't change today's numbers; they would for a tracking metric.
- **Faces are not labelled.** Face scores need face tracks added to these files.

## Next steps
- **eduardo:** tell jean about this; get jean's OK on a way to record F-13 description labels ("the person in red" = which track ids) in the label format.
- **F-10 (detector bake-off):** run detectors on these 9 clips so `report` scores them; that closes F-09b's exit check.
- **Label the remaining 4** (`street-torsos`, `crossing-crowd-above`, `crowd-dark-coats`, `crowd-orange`). The crowds have dozens of people per frame; consider labelling a sample of seconds, and say so in the notes.
- **Watch `crowd-orange` once**: it was registered without being inspected and is only 4 s long; drop it if it isn't a crowd in orange.
- Optional: face tracks for the three trio clips.

## Gotchas
- **`pair-backpacks-behind` is 720p, not 1080p.** Pexels named the file `hd_1920_1080`, but it decodes as 1280x720. It's also the only clip with audio.
- **The two portrait clips** (`trio-sidewalk-totes`, `crossing-crowd-above`) are 1080x1920; boxes are normalized, so nothing special is needed, but they're the first portrait clips the benchmark sees.
- **NTSC frame rates:** `trio-sidewalk-totes` (23.976 fps) has keyframes at frame times like 1.001 s and 2.002 s, not whole seconds.
- **Exit keyframes matter:** in the skatepark and sidewalk clips people walk out of frame; their tracks end at the last frame they're visible in (extra frames were pulled to place those).
- The label overlays used for review are local only (`~/vibe-clips/label-review/` on eduardo's machine), not in git.
