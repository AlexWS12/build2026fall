---
author: eduardo
date: 2026-10-26
branch: eduardo/video-io
features: [F-03]
base: untagged @ 84a095d
head: 0180759
status: ready for review
---

# Video I/O: probe, exact frame↔time, frame decoding with exact seeks, audio extraction

> **Heads-up:** this changes shared files: `backend/pyproject.toml` and `backend/uv.lock` (adds `av` and `numpy`) and `.github/workflows/backend.yml` (installs ffmpeg on the runner). Post in the team chat before merging.

## What changed
- **`backend/src/vibe/video/`**, the layer that reads clips. It never edits or writes video. Everything is exported from `vibe.video`:
  - `probe(path) -> VideoInfo`: display size (after rotation), rotation, exact `fps` and `time_base` (`Fraction`), duration, frame count, `is_vfr`, start time, codec, pix_fmt, and `audio: AudioInfo | None`.
  - `FrameIndex.build(path)`: every frame's timestamp, read from the container's packets with no decoding. `time_of(i)`, `frame_at(t)`, `times`, `end`, `is_vfr`, `keyframes`, `keyframe_before(i)`, `pts_of(i)`.
  - `VideoReader(path, index=None, size=None)`: a context manager with `read(i)`, `read_at(t)` and `iter_frames(start, end, step)`, returning `Frame(index, time, image)`. Images are RGB `uint8`, shaped (height, width, 3), rotated as a player shows them.
  - `extract_audio(path, sample_rate=16000, mono=True) -> (samples, rate)`, `iter_audio_chunks(path, chunk_seconds=1.0, ...)` and `write_wav(path, samples, rate)`. Samples are `float32` in [-1, 1].
  - Errors: `VideoError`, with `UnreadableMedia`, `NoVideoStream` and `NoAudioStream` under it. A missing file raises the standard `FileNotFoundError`.
  - CLI: `uv run --directory backend python -m vibe.video probe clip.mp4` prints the metadata as JSON.
- **Tests (`backend/tests/video/`, 62 tests):** `conftest.py` builds the clips with the ffmpeg CLI once per session, from the constants in `clip_spec.py`:
  - a lossless H.264 clip whose frames encode their own number as brightness, plus a 440 Hz tone;
  - VFR, B-frames, lossless WebM, no audio, delayed audio, rotated 90°, audio-only and junk variants.
- **CI:** the backend workflow installs ffmpeg with apt and sets `VIBE_REQUIRE_FFMPEG=1`, so a missing ffmpeg fails the run instead of skipping the video tests.
- **Docs:** a README note under "Run the app" (the tests need the ffmpeg CLI; the probe command), and F-03 claimed in the ROADMAP.
- Rebased on [2026-10-26-alex-contract-v0.md](2026-10-26-alex-contract-v0.md) (F-02). It touches nothing in `vibe.contract`.

## How to check it
```bash
brew install ffmpeg      # or: winget install ffmpeg / sudo apt install ffmpeg
npm install
npm run lint             # Ruff: all checks pass
npm test                 # 105 passed (62 of them F-03's)
uv run --directory backend python -m vibe.video probe path/to/clip.mp4
```
Verified on 2026-10-26 (macOS, Apple Silicon, Python 3.12.12, ffmpeg 8.0.1 CLI, PyAV 19.0.2 bundling ffmpeg 9.0.2): lint passes, `uv sync --locked` passes, and all 105 tests pass with `VIBE_REQUIRE_FFMPEG=1`. To check that the tests catch real bugs, I broke the decoder's frame matching on purpose (5 decode tests failed) and switched off the audio start alignment (the delayed-audio test failed).

**CI:** not confirmed yet. The `backend` run on PR #6 is the remaining part of F-03's exit check. F-03 stays `in progress` in the ROADMAP until it passes.

## Decisions made
- **PyAV for runtime decoding; the ffmpeg CLI only to generate test clips.** PyAV's wheels bundle the ffmpeg libraries, so the app needs no system ffmpeg to read video. F-04's export path will need the CLI anyway. This picks PyAV from the tentative stack. **It isn't in the decisions log yet:** add a row if the team agrees.
- **Frame↔time always uses the timestamp table, never `i / fps`.** It's exact for constant-rate and VFR clips alike, and it fits F-02, which keeps every time in seconds and track keyframes by `t`. `frame_at(t)` allows half a timestamp unit, because WebM rounds timestamps to 1 ms. Without that allowance, 23 of 60 frames came back wrong.
- **Conventions:**
  - Frames are numbered from 0 in display order.
  - The frame on screen at time t is the last one whose timestamp is at or before t.
  - Audio sample 0 is at the first video frame: late audio is padded with silence, and audio from before the first frame is dropped.
  - Frames are RGB, rotated to display orientation. `size` is given as displayed.
  - Audio defaults to 16 kHz mono, which is what speech models expect.
- **Plain frozen dataclasses, not Pydantic.** These are internal values, not contract data. `VideoInfo`'s field names match the contract's wherever they overlap.

## Next steps
- Get PR #6 reviewed and merged once CI passes, then mark F-03 `done` in the ROADMAP.
- Post the shared-files heads-up, and decide whether "PyAV for decoding" becomes a decisions-log row.
- F-04 (renderer) can build on `VideoReader` for its per-frame path and on `FrameIndex.frame_at` to map contract times to frames. F-31 (transcription) and F-33 (noise reduction) can use `extract_audio` and `iter_audio_chunks`.
- `FrameIndex` is cheap to rebuild, but it could be saved in the analysis cache once R-2 decides how the cache is stored.

## Gotchas
- **CI's ffmpeg is Ubuntu's 6.1**, while local testing used 8.0.1. If a fixture flag isn't supported there, the most likely one is `-display_rotation` for the rotated clip, and CI will show it.
- **Rotation isn't exposed on the stream in PyAV 19.** `probe` reads it from the first decoded frame, which also catches files whose header looks fine but whose video won't decode.
- **Lossless H.264 (`-qp 0`) ignores `-bf`**, so the B-frame fixture is a separate, lossy clip.
- **Brightness has to stay inside video range (16–235)**, or the lowest frames all decode as black. The fixtures use luma 16 + 3n.
- **Clips copied with `-c copy` and `-itsoffset` keep AAC's encoder priming at the start.** The delayed-audio fixture uses PCM in MKV for that reason.
- **AAC encodes the two stereo channels slightly differently**, so the stereo test compares them within 0.001.
- **Not tested:** dropping audio that starts *before* the first video frame. The code path exists, but no fixture covers it.
- The Starlette `httpx2` warning from the F-01 handoff is still there, and still unchecked.
