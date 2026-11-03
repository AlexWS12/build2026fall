---
author: paulallain
date: 2026-11-03
branch: paulallain/f04-renderer
features: [F-04]
base: v0.1.0 @ f5ecc86
head: 427f2e2
status: ready for review
---

# F-04 renderer: per-frame path, export to MP4, blur / pixelate / box, cuts and mutes

> **Heads-up:** this adds **D-019** (export encodes with PyAV; Proposed) to the decisions log in `docs/ARCHITECTURE.md`, a shared file. Post in the team chat before merging. No other shared file changes: no new dependencies (PyAV and numpy were already locked), and the contract, toolbox and API are untouched.

## What changed
- **`backend/src/vibe/render/`** (was an empty package):
  - `frame.py`: **the per-frame path.**
    - `FrameRenderer(state, tracks).render(image, t)` applies every mask in effect at source time `t` and returns a new frame. `render_frame(...)` is the one-off version.
    - Pure: no files, no cache, no network.
    - **Fail closed:** a masked track id with no track masks the whole frame, and is reported once in `.issues`. Tracks sharing an id are all masked.
  - `geometry.py`: where a box is at time `t`.
    - Linear between keyframes.
    - Past either end of a track, it's held for `HOLD_SECONDS` (0.5 s) and grows (`HOLD_GROWTH`); after that, it's off screen.
    - Next to an `uncertain` keyframe, it gets `UNCERTAIN_PADDING` extra.
    - Padding grows the box by that fraction of its size, half on each side. Pixels round outward, so a mask never shrinks.
  - `effects.py`: `blur` (3 box-blur passes ≈ Gaussian), `pixelate` (block averages) and `box` (black), in numpy only. Sizes scale with the target's shorter side, with a floor, so strength 0 still hides it.
  - `audio.py`: `render_audio` silences all-audio mutes, then removes cuts, on samples from `extract_audio`.
  - `export.py`: **the export path.**
    - `export(source, state, tracks, out_path)` runs every frame that isn't cut through the per-frame path and writes an H.264 + AAC MP4 with PyAV.
    - Timestamps come from `source_to_output`, in the source's time base, so variable frame rates keep their timing.
    - The file is written under a temporary name and renamed at the end; on failure the temporary file is deleted.
    - An odd width or height loses its last row or column (yuv420p).
  - **Refused before anything is written** (`UnsupportedEdit`): standing rules (F-51), muting one speaker (F-32) and audio filters (F-33). The error names the card.
  - `__main__.py`: the CLI. `export CLIP --log timeline.jsonl [--tracks t.json] -o out.mp4`, or `export --project <id>`, which writes a new file in the project's `exports/` every time.
- **`backend/tests/render/`:** 86 tests.
  - `test_render_export.py` holds **the exit check.** It renders a 3 s ffmpeg-made clip with `render_session.jsonl` (blur 0–2 s, cut 1–1.5 s, mute 2–2.5 s) and checks:
    - the output is 2.5 s and 75 frames;
    - the masked region changed and the rest didn't;
    - the blur ends on time after the cut;
    - the muted span is silent and the rest isn't.
  - It also covers: refusals write nothing; a clip with no audio; odd sizes; cutting everything is an error.
  - The other files cover geometry, effects, the per-frame path (including a no-network test), audio and the CLI.
  - Clips come from `conftest.py`, the same way as the video tests: skipped without ffmpeg, and failing if `VIBE_REQUIRE_FFMPEG=1`.
- **Docs:**
  - `docs/ARCHITECTURE.md`: a code pointer and "Built so far" in "6. Renderer", plus the D-019 row.
  - `README.md`: the render CLI line in "Run the app".
  - `docs/ROADMAP.md`: claimed the Sprint 2 F-04 row, and set it and the backlog row to `done`.

## How to check it
```bash
uv run --directory backend pytest -q tests/render     # 86 passed (with ffmpeg on PATH; without it, 14 skip)
uv run --directory backend python -m vibe.render export --help
```
By hand, from `backend/` (ffmpeg makes the clip; the render itself doesn't need ffmpeg):
```bash
ffmpeg -f lavfi -i testsrc2=s=320x240:r=30:d=3 -f lavfi -i sine=f=440:d=3 -c:v libx264 -c:a aac -shortest clip.mp4
uv run python -m vibe.render export clip.mp4 --log tests/render/render_session.jsonl --tracks tests/render/render_session.tracks.json -o out.mp4
uv run python -m vibe.video probe out.mp4             # duration 2.5
```
In `out.mp4`, the middle of the frame is blurred until 1.5 s of output (2.0 s of source), 0.5 s is gone, and the sound is silent from 1.5 s to 2.0 s.

Verified on 2026-11-03 on Windows 11, Python 3.12.12, uv 0.13.0, PyAV 19.0.2, numpy 2.5.4, ffmpeg 9.0.2 (gyan.dev build), no GPU:
- `tests/render`: 86 passed, none skipped.
- `npm test`, backend step: 448 passed, 1 skipped, **1 failed**. The failure is `tests/toolbox/test_toolbox_cli.py::test_writing_keeps_the_timeline_permissions`, which already fails on `main` on Windows (see "Gotchas"). Because of it, `npm test` stopped before the frontend step.
- `uv run ruff check` and `uv run ruff format --check`: pass.
- I rendered a clip with the CLI and compared frames by eye: the blurred box is right, and output 1.6 s shows source 2.1 s.

**Not tested:**
- the frontend tests (its dependencies aren't installed on this machine; this branch changes no frontend files);
- CI, which runs on the PR;
- real phone footage, variable-frame-rate sources, audio with more than 2 channels, and long or 1080p clips. No speed was measured: there's no benchmark for the renderer yet.

## Decisions made
paulallain made these on 2026-11-03:
- **D-019 (Proposed): export encodes with PyAV** (H.264 `libx264` yuv420p CRF 18 + AAC in MP4), not the ffmpeg command line. Nobody has to install ffmpeg to export, and the frames go straight from the per-frame path to the encoder. Confirm on the PR.
- **Edits the renderer can't do yet are refused**, not skipped, so a privacy rule is never silently missing from an output.
- **Effects are numpy only**, with no OpenCV, so there's no dependency change.

F-04 details, not decisions-log material; say on the PR if you disagree:
- A masked target with no track masks the **whole frame** ("mask the whole affected segment", ARCHITECTURE "Renderer").
- Tracks sharing an id (e.g. a detector track and its hand correction) are **all** masked.
- Outputs are `.mp4` only.
- The exit-check timeline lives in `tests/render/`, not in `contract/examples/`, so the shared contract folder isn't touched.

## Next steps
- Post the D-019 heads-up, open the PR and get a review. After merging, F-04 is done.
- **F-11 (blur on real targets):** tune the constants at the top of `geometry.py` and `effects.py` on real detections. Masks come later through F-15 (D-018).
- **F-20 / F-30 (cut, mute):** the renderer already applies cuts and all-audio mutes. Those cards connect toolbox → API → UI → export, and the preview.
- **An export route (F-64 follow-up):** call `export(storage.video_path(project.info.video), project.state(), tracks, project.exports_dir / <new name>)`, as the CLI's `--project` does, with a new file name each time.
- **F-08 / F-10:** detector tracks from the analysis cache aren't loaded yet. `--project` uses the project's hand-drawn tracks plus `--tracks`. Add cache tracks once their format exists.
- **F-51 / F-32 / F-33:** replace the matching refusal in `check_video` / `check_audio` when each lands.

## Gotchas
- **A track with one keyframe** (e.g. a box drawn once) is only masked for 0.5 s on either side of it. F-14 should give drawn boxes keyframes along the clip, or the hold rule needs an exception for `user` tracks.
- **ffmpeg on Windows:** after `winget install ffmpeg`, terminals opened by an app that was already running (including the Claude app) don't see it. Refresh PATH in the terminal, or sign out and back in.
- **Pre-existing Windows test failure:** `test_writing_keeps_the_timeline_permissions` (F-06) checks POSIX permissions, which Windows doesn't have. Its neighbouring test already skips on Windows; this one should too. CI runs on Linux, so it's green there.
- **The exit check's blur test relies mainly on "the region changed a lot"** (about 92/255 measured). The neighbour-difference "detail" measure only drops to about 0.27 after encoding, because `testsrc2` has broad gradients. Don't tighten its 0.5 bound.
- **Untouched pixels aren't byte-identical** after encoding (RGB → yuv420p → H.264): about 3/255 on average. The tests allow 6.
- **AAC smears about 20–40 ms around a mute's edges.** The tests check inside the span with 0.1 s margins.
- `tests/render` needs unique file names (`test_render_*.py`, `render_clip.py`), because the test folders have no `__init__.py`.
- The pytest `httpx2` deprecation warning is the existing one from F-01.
