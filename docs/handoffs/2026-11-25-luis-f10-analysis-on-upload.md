---
author: luis
date: 2026-11-25
branch: luis/f10-analysis-on-upload
features: [F-10, F-31]
base: untagged @ 29df1e2
head: 81f8c45
status: ready for review
---

> **Overlaps with #44 (eduardo/f10b-vision, open).** #44 also makes `/analysis` run detectors, through F-08's `run_analysis` (local and remote), with a default detector set (D-022), stitching and head boxes. That part of #44 is more complete than the `/analysis` part here. What this branch has that #44 doesn't: the **ByteTrack fix** (first commit), the **analysis right after an upload** in the UI, and **`VIBE_TRANSCRIBE_BACKEND`**. Whoever merges should pick: take #44's `/analysis` and keep those three from here, or the other way round. The two branches conflict in `config.py`, `api/projects.py`, `modules/vision/cache.py`, `frontend/src/state/*` and the generated API types.

# F-10: the app analyses a video by itself, ByteTrack stops leaving one-frame copies, transcripts can run locally

## What changed
- **ByteTrack keeps one track per object** (`modules/vision/tracking.py`). Before, the stand-in track that covers an object until ByteTrack confirms it stayed a separate, one-keyframe track. Every person got a copy at the start of the clip. The description judge (F-13) can't answer for one crop, so the copy was `unsure` and masked, and the renderer kept masking its box for the rest of the clip. "blur the person in a neon green jacket" blurred all three people on trio-street-outfits. Now a newly confirmed track continues its stand-in (same id), from the first frame.
  - `ByteTracker.version = 2` enters the tracks cache key (`cache.tracks_key(..., version)`). Version 1 adds nothing to the hash, so existing keys and the `iou` tracker's are unchanged.
- **`POST /api/projects/{id}/analysis` runs the detectors** when nothing is cached (`modules/vision/run.py`, `api/projects.py`):
  - The detector, tracker and stride come from `VIBE_DETECTORS`, `VIBE_TRACKER` and `VIBE_DETECT_EVERY`.
  - The result is cached and becomes the video's default.
  - There is no default detector (D-015 is Proposed). `stub` never runs a model, and without `VIBE_DETECTORS` it's the 503 it was.
  - Bad settings or a missing package are a 503 that says so, before any frame is read.
  - It runs on this machine even with `VIBE_BACKEND=remote`: there's no remote vision server here.
  - The `analyze` CLI now uses the same `analyse_and_store`.
- **The UI analyses a new upload by itself** (`frontend/src/state/useSession.ts`). A 503 right after an upload settles quietly, and the header still offers "Analyse video". Any other failure is shown. Reopening a project doesn't start an analysis.
- **`VIBE_TRANSCRIBE_BACKEND`** (`config.py`, transcript route): transcripts on another backend than `VIBE_BACKEND`. For example, Whisper on this machine while the description judge goes to a remote GPU. Unset means `VIBE_BACKEND`, as before.

## How to check it
```bash
cd backend && uv run pytest -q     # 1679 passed, 2 skipped, 1 failed: test_writing_keeps_the_timeline_permissions (POSIX chmod; fails on main on Windows too)
cd frontend && npm test            # 113 passed
npm run lint
```
Run on Windows 11, RTX 4050 Laptop 6 GB, 2026-11-25. In the app:
```bash
VIBE_BACKEND=local VIBE_DETECTORS=tv-frcnn-mobile VIBE_TRACKER="bytetrack:min_conf=0.85,iou_match=0.2,max_gap=3" npm run dev
```
Upload a clip and the header goes "Analysing the video…", then "Analysed · N targets".
- **Upload in the UI:** a 22 s 1080x1920 clip gave 240 targets.
- **Through the API:** `cars_street.mp4` took 29 s and gave 61 tracks.

These are wall times by hand on this laptop, not benchmark numbers.

**Tracker, before and after**, same cached detections and settings:

| clip | person tracks before | after | of which one keyframe, before → after |
|---|---|---|---|
| people_waling (crowd) | 156 | 85 | 69 → 9 |
| 15561430 (crowd) | 115 | 62 | 49 → 5 |
| trio-street-outfits | 8 | 4 | 4 → 0 |

On trio-street-outfits, "blur the person in a neon green jacket" now masks that person (plus one far-away fragment the judge is unsure of) instead of all three.

## Decisions made
- No default detector or tracker: the app analyses only when someone sets `VIBE_DETECTORS`. #44 proposes a default (D-022); if it merges, that's the default.
- A 503 after an upload is quiet; the user can still press "Analyse video" and see the reason.

## Next steps
- Settle the overlap with #44 (see the note at the top).
- The remaining one-keyframe tracks are real single detections. Stitching (#44) or a minimum track length would cut more.

## Gotchas
- On Windows, a repo under a long OneDrive path can exceed 260 characters in cache file names, which shows up as "No such file or directory". Use a short `VIBE_DATA_DIR`.
- `faster-whisper` is a hand-installed model package (`uv pip install faster-whisper`), like the detectors'. The first transcript downloads Whisper `small` (about 460 MB).
