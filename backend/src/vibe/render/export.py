"""The export path: a whole clip, through the per-frame path, into one MP4 file.

Every frame that isn't cut goes through ``FrameRenderer``, and the audio through
``render_audio``. Both are encoded with PyAV (H.264 and AAC, from the ffmpeg libraries in its
wheel), so exporting needs no ffmpeg install. The file is written under a temporary name and
renamed once complete, so a failed render never leaves a half-written export behind.
"""

import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import av.error
import numpy as np
from av.audio.stream import AudioStream
from av.container import OutputContainer

from vibe.contract import TimelineState, Track
from vibe.render.audio import check_audio, render_audio
from vibe.render.errors import RenderError
from vibe.render.frame import FrameRenderer, RenderIssue, check_video
from vibe.video import FrameIndex, VideoInfo, VideoReader, extract_audio, probe

VIDEO_CODEC = "libx264"
PIX_FMT = "yuv420p"
CRF = 18
"""x264 quality: 18 looks close to lossless. Lower is better and bigger."""

AUDIO_CODEC = "aac"
AUDIO_CHUNK = 1024
"""Samples handed to the audio encoder at a time."""


@dataclass(frozen=True)
class RenderResult:
    path: Path
    frames: int
    duration: float
    """Length of the output in seconds."""
    issues: tuple[RenderIssue, ...]
    """What the renderer masked more because of (see ``FrameRenderer.issues``)."""


def export(
    source: str | Path,
    state: TimelineState,
    tracks: Iterable[Track],
    out_path: str | Path,
) -> RenderResult:
    """Render ``source`` with ``state`` and ``tracks`` into an MP4 at ``out_path``.

    Raises ``UnsupportedEdit`` before anything is written if the timeline has an edit this
    renderer can't apply yet, ``RenderError`` if the output can't be made (e.g. the cuts remove
    everything), and the video layer's errors for a source it can't read.
    """
    source, out_path = Path(source), Path(out_path)
    if out_path.suffix.lower() != ".mp4":
        raise RenderError(f"exports are .mp4 files, got {out_path.name}")
    if source.exists() and out_path.exists() and out_path.samefile(source):
        raise RenderError(f"{out_path} is the source video: exporting there would overwrite it")
    check_video(state)
    check_audio(state)
    renderer = FrameRenderer(state, tracks)
    info = probe(source)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=out_path.parent, prefix=f".{out_path.stem}.", suffix=".mp4")
    os.close(fd)
    tmp = Path(name)
    try:
        frames, duration = _write(source, info, state, renderer, tmp)
        os.replace(tmp, out_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return RenderResult(out_path, frames, duration, tuple(renderer.issues))


def _write(
    source: Path, info: VideoInfo, state: TimelineState, renderer: FrameRenderer, path: Path
) -> tuple[int, float]:
    index = FrameIndex.build(source)
    # yuv420p stores colour at half resolution, so it needs an even width and height.
    width, height = info.width // 2 * 2, info.height // 2 * 2
    audio = _render_audio(source, info, index, state)
    try:
        with (
            av.open(str(path), "w", format="mp4") as out,
            VideoReader(source, index=index) as reader,
        ):
            video = out.add_stream(VIDEO_CODEC, rate=info.fps)
            video.width, video.height, video.pix_fmt = width, height, PIX_FMT
            # Output timestamps in the source's time base keep variable-frame-rate timing.
            video.codec_context.time_base = info.time_base
            video.options = {"crf": str(CRF)}
            sound = None
            if audio is not None:
                samples, rate, layout = audio
                sound = out.add_stream(AUDIO_CODEC, rate=rate, layout=layout)

            frames, last_pts = 0, -1
            for frame in reader.iter_frames():
                if state.is_cut(frame.time):
                    continue
                image = renderer.render(frame.image, frame.time)[:height, :width]
                encoded = av.VideoFrame.from_ndarray(np.ascontiguousarray(image), format="rgb24")
                encoded.time_base = info.time_base
                # Rounding after a cut could land on the previous frame's tick: keep it after.
                pts = round(state.source_to_output(frame.time) / info.time_base)
                encoded.pts = last_pts = max(pts, last_pts + 1)
                out.mux(video.encode(encoded))
                frames += 1
            if frames == 0:
                raise RenderError("the cuts remove the whole clip: nothing to render")
            out.mux(video.encode())

            if sound is not None:
                _encode_audio(out, sound, samples, rate)
    except av.error.FFmpegError as e:
        raise RenderError(f"encoding {path.name} failed: {e}") from e
    return frames, state.source_to_output(index.end)


def _render_audio(
    source: Path, info: VideoInfo, index: FrameIndex, state: TimelineState
) -> tuple[np.ndarray, int, str] | None:
    """The output's audio as ``(samples, rate, layout)``, or ``None`` for a silent clip."""
    if info.audio is None:
        return None
    rate, mono = info.audio.sample_rate, info.audio.channels == 1
    samples, _ = extract_audio(source, sample_rate=rate, mono=mono)
    samples = samples[: round(index.end * rate)]  # no sound past the last frame
    if mono:
        layout = "mono"
    else:
        with av.open(str(source)) as container:
            layout = container.streams.audio[0].layout.name
    return render_audio(samples, rate, state), rate, layout


def _encode_audio(
    out: OutputContainer, stream: AudioStream, samples: np.ndarray, rate: int
) -> None:
    planar = np.ascontiguousarray(samples.reshape(len(samples), -1).T)  # (channels, n)
    layout = stream.codec_context.layout.name
    for start in range(0, planar.shape[1], AUDIO_CHUNK):
        frame = av.AudioFrame.from_ndarray(
            np.ascontiguousarray(planar[:, start : start + AUDIO_CHUNK]),
            format="fltp",
            layout=layout,
        )
        frame.sample_rate = rate
        frame.time_base = Fraction(1, rate)
        frame.pts = start
        out.mux(stream.encode(frame))
    out.mux(stream.encode())
