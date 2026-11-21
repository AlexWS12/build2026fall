---
author: nickolas
date: 2026-11-21
branch: nickolas/f34-isolate-voice
features: [F-34]
base: v0.4.0 @ 6491308 (stacked on nickolas/f33b-render-audio-filters, #40)
head: 5e44a36
status: ready for review
---

# isolate_voice (phase A): keeps the voices and turns down taps, clicks and fans (stub backend)

## What changed
- **Contract:** `AudioFilterType` gains `"isolate_voice"` (new value, `schema_version` stays 1). JSON schemas and `openapi.json` regenerated; `generated/schema.ts` edited by hand (Node wasn't installed), so re-run `npm run gen:api` to confirm.
- **Toolbox:** new tool `isolate_voice(time_range?, strength?)`, same arguments as `denoise`.
- **Module** `modules/isolate_voice.py`, `stub` backend in numpy: F-33's noise gate, a click gate (median filtering, as in harmonic/percussive separation) and a 70 Hz high-pass. `VoiceIsolator` streams like `NoiseGate`. `local` / `remote` and the live `isolate_voice_chunk` raise.
- **Renderer:** `DenoiseMix` became `FilterMix`, chaining denoise then isolate_voice. A backend that can't run a filter is a 422 naming it ("can't apply the audio filters: ...").
- **Planner:** sound lists with any short sound ("the finger tapping and the computer fans") plan `isolate_voice`; steady ones only plan `denoise`. "Keep only the voices" and "clean up the audio" plan `isolate_voice`.
- After Edugre's review on #41: "keep ... voices/dialogue/speech" with a time ("keep only the dialogue from 0:30 to 1:00") is asked about, since it may mean keep only that part. "isolate the voices from 0:10 to 0:20" still plans. Singular "speaker" no longer matches (phase A keeps every voice).

## How to check it
- `cd backend && .venv/Scripts/python.exe -m pytest tests/modules/test_isolate_voice.py tests/agent tests/render -q`
- In the app (`VIBE_BACKEND=stub`): upload a clip, ask "remove the finger tapping and the computer fans" or "keep only the voices", export.
- Full suite on Windows, no GPU: 1514 passed, 118 skipped, 1 failed (the Windows-only `test_writing_keeps_the_timeline_permissions`).
- Checked on synthetic sound only. No quality numbers from the benchmark harness yet, and not checked by listening to real recordings.

## Decisions made
- None at architecture level. The speech enhancement model for `local` / `remote` isn't chosen.

## Next steps
- Review nits still open on #41: `NOISE_WORDS` in the planner's question makes failed "mute the typing" requests get the noise question; "sounds" in `CLICK_WORDS` does the same for "mute the sounds"; the denoise/isolate merge only works for literal ranges.
- Add an isolate_voice case to the benchmark harness.
- Phase B (keep only the main speaker) needs F-32.

## Gotchas
- Background voices stay, and music with a beat partly stays.
- Merge #40 first; this PR then retargets to `main`.
