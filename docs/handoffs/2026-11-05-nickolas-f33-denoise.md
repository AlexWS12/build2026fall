---
author: nickolas
date: 2026-11-05
branch: nickolas/f33-denoise
features: [F-33]
base: v0.1.0 @ d95ede3
head: a67d1f7
status: ready for review
---

# F-33: `denoise` toolbox tool, planner template, and a stub spectral-gate noise reducer

> **Shared files changed:** `backend/src/vibe/toolbox/registry.py` and `tools.py` gain a new tool, `denoise`. No existing tool's name or arguments changed, and neither did the contract (`add_audio_filter` with `filter: "denoise"` was already in it, so `schema_version` stays the same). Post a heads-up in the team chat before merging.

## What changed
- **Module** `backend/src/vibe/modules/denoise.py`: `denoise(samples, sample_rate, strength=None, backend=None)` handles mono `(n,)` or multichannel `(n, channels)` float audio and returns the same shape and dtype.
  - The `stub` backend is a numpy spectral gate. It runs an STFT (~32 ms frames, at least 16 samples, 75% overlap), takes the noise level per bin as the 10th percentile over up to 2000 frames inside the clip, and applies a gain with 3x over-subtraction and a 24 dB floor. `strength` blends that gain with 1, so small values change the audio only a little. Overlap-add is vectorized. It's deterministic and uses no model or GPU.
  - `local` / `remote` raise `DenoiseError` and never hand back noisy audio.
  - It takes floating-point audio only (integer PCM raises `ValueError`) and computes in the input's precision (float16 in float32).
  - `strength=0` returns a copy, digital silence stays silent, and a clip shorter than one frame comes back unchanged.
  - **Live shape:** `denoise_chunk(chunk, state, strength)` plus `DenoiseState` (running noise estimate and overlap tail). It checks its arguments, then raises `DenoiseError` on every backend until the streaming model lands.
- **Toolbox tool** `denoise(time_range?, strength?)` writes `AddAudioFilter(filter="denoise", ...)`. A null `time_range` means the whole clip; a null `strength` means the strongest. `strength` is validated to 0–1, and extra args are rejected.
- **Planner templates** (`agent/templates.py`): "denoise [the audio]" and "remove / reduce / cut / delete the [background] noise(s) / hiss(es) / hum(s) [in / from the audio]" plan a `denoise` call, with an optional time phrase. The new pattern is checked **before** `_CUT`, so "remove the noise" filters the audio instead of cutting video. "cut / delete the noise" **with a time range** is asked instead ("reduce the noise in that part, or cut that part out?").
- **Planner questions** (`agent/planner.py`, `_question_for`): noise requests the templates can't read get a noise-specific question.
- **CLI:** `python -m vibe.toolbox denoise --help` shows an example.
- **Review of PR #23** (Edugre): all six inline comments and both notes are addressed in `a67d1f7`. The one exception: the noise-profile FFTs still re-transform up to 2000 sampled frames, which costs little and keeps memory flat.
- Tests: `tests/modules/test_denoise.py` (new), plus new cases in `test_tools.py`, `test_registry.py`, `test_templates.py`, `test_planner.py` and `test_toolbox_cli.py`.
- Docs: the F-33 notes in `docs/ARCHITECTURE.md` (toolbox "Built so far" and the Audio modules list), and the F-33 status in `docs/ROADMAP.md`.

## How to check it
```
cd backend && uv run pytest -q
uv run python -m vibe.toolbox denoise --help
uv run python -m vibe.toolbox denoise --log timeline.jsonl --args '{"strength": 0.5}'
```
Result on 2026-11-05, after the review fixes (Windows, no GPU): **437 passed, 76 skipped, 1 failed**. Ruff check and format are clean.
- The failure is `tests/toolbox/test_toolbox_cli.py::test_writing_keeps_the_timeline_permissions`. It also fails on `main` at `d95ede3`, so it predates this branch. The test compares POSIX file modes, which Windows reports as `0o666`, so it's probably Windows-only. It hasn't been checked on Mac or Linux.
- The skips are the video tests, which need the ffmpeg command line.

I haven't listened to or measured denoise quality on real recordings. The tests use synthetic audio: a 440 Hz tone with gaps plus white noise. They check that noise in the gaps drops by more than 6 dB and that the tone stays within -20 dB error. That's a correctness check, not a benchmark.

## Decisions made
- The stub backend does real (modest) noise reduction instead of passing audio through, so CI and CPU laptops exercise the whole path. It's meant for steady noise (hiss, hum, fans), not music or voices in the background (that's F-34).
- Backends that aren't built raise instead of returning the input unchanged (fail safe: never skip errors silently).
- The planner reads "remove / reduce / cut the noise" as denoise, not cut. With a time range, "cut / delete the noise" is asked about, since it may mean cutting that part out.
- The model is still open. DeepFilterNet is the lead candidate, but it isn't chosen and isn't in the decisions log.

## Next steps
- Renderer: nothing applies `audio_filters` spans yet. The renderer needs to decode the audio in each span, call `denoise`, and mux it back in.
- Pick and wire the `local` / `remote` model (DeepFilterNet or another), with numbers from the benchmark harness.
- Per-chunk (live) path: implement `denoise_chunk` with a running noise estimate kept in `DenoiseState`. It's planned to come with a streaming model.

## Gotchas
- The noise estimate comes from the clip's quieter frames. On a clip that's speech end to end with no pauses, the 10th percentile can include speech, and strong settings will thin the voice.
- Applying a time range means running denoise on that slice only, so a very short range gives a poor noise estimate. Consider estimating from the whole clip and applying to the range.
- Frame size is rounded to a power of two (~1024 samples at 32 kHz, 512 at 16 kHz, 2048 at 44.1/48 kHz).
