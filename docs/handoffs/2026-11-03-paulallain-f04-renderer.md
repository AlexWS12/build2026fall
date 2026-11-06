---
author: paulallain
date: 2026-11-03
branch: paulallain/f04-renderer
features: [F-04]
base: v0.1.0 @ d95ede3
head: b7c5f51
status: ready for review
---

# F-04 renderer: per-frame path, export to MP4, blur / pixelate / box, cuts and mutes

> **Heads-up:** this adds **D-019** (export encodes with PyAV; Proposed) to the decisions log in `docs/ARCHITECTURE.md`, a shared file. Post in the team chat before merging. No other shared file changes: no new dependencies (PyAV and numpy were already locked), and the contract, toolbox and API are untouched. `iter_audio_chunks` in `video/audio.py` (F-03) gained an optional `layout` argument; existing calls behave the same.

## What changed
- **`backend/src/vibe/render/`** (was an empty package):
  - `frame.py`: **the per-frame path.**
    - `FrameRenderer(state, tracks).render(image, t)` applies every mask in effect at source time `t` and returns a new frame. `render_frame(...)` is the one-off version.
    - Pure: no files, no cache, no network.
    - **Fail closed:** a masked track id with no track masks the whole frame, and is reported in `.issues`. Tracks sharing an id are all masked.
  - `geometry.py`: where a box is at time `t`.
    - Linear between keyframes up to `MAX_GAP_SECONDS` (1 s) apart. Across a longer gap, the box covers both keyframes and the space between them.
    - Past either end of a track, the nearest box is held for as long as the mask lasts, growing (`HOLD_GROWTH`) up to `MAX_HOLD_GROWTH`. More than `HOLD_SECONDS` (0.5 s) from a keyframe, it's marked `lost`, and each lost stretch gets an entry in `.issues`.
    - Next to an `uncertain` keyframe, it gets `UNCERTAIN_PADDING` extra.
    - Padding grows the box by that fraction of its size, half on each side. Pixels round outward, so a mask never shrinks.
  - `effects.py`: `blur` (3 box-blur passes ≈ Gaussian), `pixelate` (block averages) and `box` (black), in numpy only. Sizes scale with the target's shorter side, with a floor, so strength 0 still hides it.
  - `audio.py`: `render_audio` silences all-audio mutes, then removes cuts. `apply_audio_edits` does the same for one chunk.
  - `export.py`: **the export path.**
    - `export(source, state, tracks, out_path)` runs every frame that isn't cut through the per-frame path and writes an H.264 + AAC MP4 with PyAV.
    - Timestamps come from `source_to_output`, in the source's time base, so variable frame rates keep their timing.
    - Sound is edited and encoded in 0.1 s chunks alongside the picture, and comes out mono or stereo.
    - The file's index goes at its start (`faststart`).
    - The file is written under a temporary name and renamed at the end; on failure the temporary file is deleted. Exporting over the source is refused.
    - An odd width or height loses its last row or column (yuv420p).
  - **Refused before anything is written** (`UnsupportedEdit`): standing rules (F-51), muting one speaker (F-32) and audio filters (F-33). The error names the card.
  - `__main__.py`: the CLI. `export CLIP --log timeline.jsonl [--tracks t.json] -o out.mp4`, or `export --project <id>`, which writes a new file in the project's `exports/` every time.
- **`backend/tests/render/`:** 123 tests.
  - `test_render_export.py` holds **the exit check.** It renders a 3 s ffmpeg-made clip with `render_session.jsonl` (blur 0–2 s, cut 1–1.5 s, mute 2–2.5 s) and checks:
    - the output is 2.5 s and 75 frames;
    - the masked region changed and the rest didn't;
    - the blur ends on time after the cut;
    - the muted span is silent and the rest isn't.
  - It also covers: refusals write nothing; a clip with no audio; odd sizes; cutting everything is an error; audio layouts; sound and picture stored together; the index first; refusing to overwrite the source.
  - The other files cover geometry, effects, the per-frame path (including a no-network test), audio and the CLI.
  - Clips come from `conftest.py`, the same way as the video tests: skipped without ffmpeg, and failing if `VIBE_REQUIRE_FFMPEG=1`.
- **Docs:**
  - `docs/ARCHITECTURE.md`: a code pointer and "Built so far" in "6. Renderer", plus the D-019 row.
  - `README.md`: the render CLI line in "Run the app".
  - `docs/ROADMAP.md`: claimed the Sprint 2 F-04 row, and set it and the backlog row to `done`.
- **Review fixes (PR #21, Edugre):** every comment is addressed, two of them differently from what was suggested.
  - **A lost target stayed visible, with no report.** A box stopped 0.5 s after a track's last keyframe even with the mask still in effect. It's now held for as long as the mask lasts, and the track is reported in `.issues`. This replaces the old "off screen" behaviour, and `at()` no longer returns `None`.
  - **A box slid across a long gap.** Past `MAX_GAP_SECONDS`, the box now covers both keyframes and everything between them. Edugre suggested holding the two end boxes; a target crossing the frame during the gap would be visible between those two, so the box covers the whole span instead.
  - **Export could overwrite its source.** It's now refused, using `samefile`, which also catches other spellings of the path and hard links.
  - **Some audio made the export fail.** `5.1(side)` from the review worked here, but 7.1 layouts failed, and so did any source with no named layout (stereo PCM in MKV reports "2 channels"). Output is now always mono or stereo, through a new `layout` argument on `iter_audio_chunks`.
  - **The source was reopened to read its layout.** No longer needed, so `AudioInfo` didn't need a `layout` field.
  - **Sound wasn't stored with the picture, and the index was at the end.** Below about 10 s the muxer hid this; a 30 s clip had 599 video packets before the first audio. Sound is now encoded with the picture (a 15 s clip has 2), and `faststart` moves the index to the front.
  - **All the audio was held in memory.** It's now processed in chunks, which took the peak for a 30-minute clip from 2,778 MB to 98 MB (measured below).
  - **Only `FileNotFoundError` was handled by the CLI.** It now catches `OSError`, so a permission error prints `error: ...`.
  - **`check_video` ran twice.** Removed from `export()`; `FrameRenderer` does it.
- **Merged `origin/main`** to resolve a conflict in `docs/ROADMAP.md`, keeping F-64 and F-09 from `main` and the F-04 row from this branch. It's a merge rather than a rebase, so review comments stay attached and no force-push is needed.
- **Code review fixes (`/code-review`, 2026-11-05):**
  - **HD colours were wrong.** Output was BT.601 with no tag, which players read as BT.709 on HD. Frames are now converted to BT.709 and tagged. A flat red 720p clip now keeps its Y=62 (it was 80).
  - **Some sample rates made the export fail.** 37.8, 176.4 and 192 kHz are refused by AAC. Those now come out at 48 kHz; the valid rates come from `Codec.audio_rates`.
  - **A mask started up to a frame late.** `render` gained an optional `until`, the next frame's time. Export passes it, so a mask starting while a frame is on screen covers that frame, and a span shorter than a frame still applies. Live and preview don't pass it, and behave as before.
  - **Only the first lost stretch of a track was reported.** `RenderIssue` gained `last_seen`, and each stretch gets its own entry.

## How to check it
```bash
uv run --directory backend pytest -q tests/render     # 123 passed (with ffmpeg on PATH; without it, the clip tests skip)
uv run --directory backend python -m vibe.render export --help
```
By hand, from `backend/` (ffmpeg makes the clip; the render itself doesn't need ffmpeg):
```bash
ffmpeg -f lavfi -i testsrc2=s=320x240:r=30:d=3 -f lavfi -i sine=f=440:d=3 -c:v libx264 -c:a aac -shortest clip.mp4
uv run python -m vibe.render export clip.mp4 --log tests/render/render_session.jsonl --tracks tests/render/render_session.tracks.json -o out.mp4
uv run python -m vibe.video probe out.mp4             # duration 2.5
```
In `out.mp4`, the middle of the frame is blurred until 1.5 s of output (2.0 s of source), 0.5 s is gone, and the sound is silent from 1.5 s to 2.0 s.

Verified on 2026-11-05 on Windows 11, Intel64 Family 6 Model 154 laptop, Python 3.12.12, uv 0.13.0, PyAV 19.0.2, numpy 2.5.4, ffmpeg 9.0.2 (gyan.dev build), no GPU:
- `tests/render`: 123 passed, none skipped.
- Backend suite: 576 passed, 1 skipped, **1 failed**. The failure is `tests/toolbox/test_toolbox_cli.py::test_writing_keeps_the_timeline_permissions`, which already fails on `main` on Windows (see "Gotchas").
- `uv run ruff check` and `uv run ruff format --check`: pass.
- I reran a reproduction of each review comment and of the four code review fixes, and none of them happens now.
- I compared frames by eye, on the original exit-check render, on a track lost at 1 s under a 3 s blur (the box stays and grows), and on a 720p BT.709 clip before and after export (the colours match).
- **Measured by scratch script, on a 30-minute, 48 kHz stereo, 64×48 clip, peak process memory for one export:** 2,778 MB before, 98 MB after.
- **These two figures come from scratch scripts that aren't in the repo, so nobody else can rerun them.** They're one-off checks, not benchmark results, and they were taken before the code review fixes.
- **Speed, same machine, a 60 s clip of the same kind, 8 alternating rounds:** best 1.55 s before and 1.71 s after. This laptop drops to about a fifth of its speed under sustained load (both reach about 7.8 s), so compare only like with like.

**Not tested:**
- the frontend tests (its dependencies aren't installed on this machine; this branch changes no frontend files);
- CI, which runs on the PR;
- HD clips with no colour tag at all, and HDR clips such as iPhone HLG (see "Gotchas");
- real phone footage, and 1080p clips. Variable frame rate and rotated clips worked in a one-off script, but no test covers them. The renderer has no speed benchmark yet; the figures above only compare before and after on a tiny clip.

## Decisions made
paulallain made these on 2026-11-03:
- **D-019 (Proposed): export encodes with PyAV** (H.264 `libx264` yuv420p CRF 18 + AAC in MP4), not the ffmpeg command line. Nobody has to install ffmpeg to export, and the frames go straight from the per-frame path to the encoder. Confirm on the PR.
- **Edits the renderer can't do yet are refused**, not skipped, so a privacy rule is never silently missing from an output.
- **Effects are numpy only**, with no OpenCV, so there's no dependency change.

F-04 details, not decisions-log material; say on the PR if you disagree:
- A masked target with no track masks the **whole frame** ("mask the whole affected segment", ARCHITECTURE "Renderer").
- A lost target keeps its last box rather than masking the whole frame, which would blur the rest of the edit whenever anyone left the shot.
- Tracks sharing an id (e.g. a detector track and its hand correction) are **all** masked, and the id only counts as lost when all of them are.
- Outputs are `.mp4` only, always BT.709, and mono or stereo.
- Sample rates AAC can't use become 48 kHz.
- A mask covers a frame if it's in effect when the frame appears or starts while it's on screen.
- The exit-check timeline lives in `tests/render/`, not in `contract/examples/`, so the shared contract folder isn't touched.

## Next steps
- Post the D-019 heads-up, open the PR and get a review. After merging, F-04 is done.
- **F-11 (blur on real targets):** tune the constants at the top of `geometry.py` and `effects.py` on real detections, especially `MAX_GAP_SECONDS` and `MAX_HOLD_GROWTH`, which are unmeasured starting values. Masks come later through F-15 (D-018).
- **F-41 (review queue):** `RenderResult.issues` lists each lost stretch to show for review.
- **Remaining `/code-review` findings, not fixed here:**
  - slow blur on large boxes (about 311 ms for a quarter of a 1080p frame, measured with a one-off script, on the machine above). This blocks live mode, and needs blurring at reduced resolution. It should be its own PR;
  - exports get owner-only `0600` permissions on Linux;
  - non-square pixel aspect ratio is dropped;
  - `apply_audio_edits` is public but doesn't run `check_audio`;
  - encoding errors show the temporary file name;
  - cut frames are decoded and then discarded;
  - x264 uses slice threading;
  - benchmark numbers need a committed script;
  - D-019 doesn't record that the wheel's libx264 is GPL;
  - no automated test for variable frame rate or rotated clips;
  - `mono=False` is redundant next to `layout`, and `render_audio` is only used by tests.
- **F-20 / F-30 (cut, mute):** the renderer already applies cuts and all-audio mutes. Those cards connect toolbox → API → UI → export, and the preview.
- **An export route (F-64 follow-up):** call `export(storage.video_path(project.info.video), project.state(), tracks, project.exports_dir / <new name>)`, as the CLI's `--project` does, with a new file name each time.
- **F-08 / F-10:** detector tracks from the analysis cache aren't loaded yet. `--project` uses the project's hand-drawn tracks plus `--tracks`. Add cache tracks once their format exists.
- **F-51 / F-32 / F-33:** replace the matching refusal in `check_video` / `check_audio` when each lands.

## Gotchas
- **A held box is the last known position, not a guarantee.** A lost target can move out of it, so this still isn't fully fail-closed. That's why it's reported. Masking the whole frame would be, at the cost of blurring the rest of the edit whenever anyone leaves the shot.
- **Held boxes can leave masked patches where nobody is.** A whole-clip mask keeps a box before a track starts and after it ends, so a clip with many short tracks shows many patches, and a box across a long gap can be wide. F-10 / F-11 should reduce this with steadier tracks.
- **HD with no colour tag isn't handled specially.** PyAV may read it as BT.601, while players assume BT.709 for HD, so the export could look different from the source in a player. My check didn't cover this, because ffmpeg tagged the test clip by itself.
- **HDR isn't converted to SDR.** Output is tagged BT.709, so HDR clips (e.g. iPhone HLG) may look washed out. Not tested.
- **Timing on this laptop is unreliable:** the same export took between 8 s and 40 s depending on the moment. Alternate what you compare, and use the best times. I blamed AAC frame sizes for a slowdown that was really this, so don't reintroduce a frame-size workaround: alternating runs showed frame size makes no difference.
- **Downmixing can go above 1.0** on very loud multichannel audio, because channels are added together. It's rare and isn't handled.
- **`import vibe.render.export as x` gives the function, not the module,** because `vibe.render` exports a function with that name.
- **ffmpeg on Windows:** after `winget install ffmpeg`, terminals opened by an app that was already running (including the Claude app) don't see it. Refresh PATH in the terminal, or sign out and back in.
- **Pre-existing Windows test failure:** `test_writing_keeps_the_timeline_permissions` (F-06) checks POSIX permissions, which Windows doesn't have. Its neighbouring test already skips on Windows; this one should too. CI runs on Linux, so it's green there.
- **The exit check's blur test relies mainly on "the region changed a lot"** (about 92/255 measured). The neighbour-difference "detail" measure only drops to about 0.27 after encoding, because `testsrc2` has broad gradients. Don't tighten its 0.5 bound.
- **Untouched pixels aren't byte-identical** after encoding (RGB → yuv420p → H.264): about 3/255 on average. The tests allow 6.
- **AAC smears about 20–40 ms around a mute's edges.** The tests check inside the span with 0.1 s margins.
- `tests/render` needs unique file names (`test_render_*.py`, `render_clip.py`), because the test folders have no `__init__.py`.
- The pytest `httpx2` deprecation warning is the existing one from F-01.
