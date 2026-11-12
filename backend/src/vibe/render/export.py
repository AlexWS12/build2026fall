"""The export path: a whole clip, through the per-frame path, into one MP4 file.

Every frame that isn't cut goes through ``FrameRenderer``, and the audio through
``apply_audio_edits`` a chunk at a time, so memory stays small whatever the clip's length. Both
are encoded with PyAV (H.264 and AAC, from the ffmpeg libraries in its wheel), so exporting needs
no ffmpeg install. Sound is encoded in step with the picture and the file's index goes at its
start, so a browser can play it while it downloads. The file is written under a temporary name
and renamed once complete, so a failed render never leaves a half-written export behind.
"""

import os
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import av.error
import numpy as np
from av.container import OutputContainer
from av.video.reformatter import ColorRange, Colorspace

from vibe.contract import TimelineState, Track
from vibe.render.audio import apply_audio_edits, check_audio
from vibe.render.errors import RenderError
from vibe.render.frame import FrameRenderer, RenderIssue
from vibe.render.mask_report import MaskAudit, audit_masks
from vibe.video import AudioInfo, FrameIndex, VideoInfo, VideoReader, iter_audio_chunks, probe

VIDEO_CODEC = "libx264"
PIX_FMT = "yuv420p"
CRF = 18
"""x264 quality: 18 looks close to lossless. Lower is better and bigger."""

COLORSPACE = Colorspace.ITU709
COLOR_RANGE = ColorRange.MPEG
BT709_TAG = 1
"""The number for BT.709 in a stream's colour primaries and transfer tags."""

AUDIO_CODEC = "aac"
FALLBACK_SAMPLE_RATE = 48000
"""Used when the AAC encoder can't take the source's rate (e.g. 192 kHz)."""

AUDIO_CHUNK_SECONDS = 0.1
"""How much sound is decoded, edited and encoded at a time."""


@dataclass(frozen=True)
class RenderResult:
    path: Path
    frames: int
    duration: float
    """Length of the output in seconds."""
    issues: tuple[RenderIssue, ...]
    """What the renderer masked more because of (see ``FrameRenderer.issues``)."""
    audit: MaskAudit
    """Every masked stretch, in continuous time, where ``issues`` is what frames actually hit. A
    lost stretch too short to reach a frame is here but not in ``issues``, so ``audit.clean`` can
    be false with no issues. A rule never gets this far, so ``rules_not_audited`` is empty."""


def export(
    source: str | Path,
    state: TimelineState,
    tracks: Iterable[Track],
    out_path: str | Path,
) -> RenderResult:
    """Render ``source`` with ``state`` and ``tracks`` into an MP4 at ``out_path``.

    Raises ``UnsupportedEdit`` before anything is written if the timeline has an edit this
    renderer can't apply yet, ``RenderError`` if the output can't be made (e.g. the cuts remove
    everything, or ``out_path`` is the source), and the video layer's errors for a source it
    can't read.
    """
    source, out_path = Path(source), Path(out_path)
    if out_path.suffix.lower() != ".mp4":
        raise RenderError(f"exports are .mp4 files, got {out_path.name}")
    if source.exists() and out_path.exists() and out_path.samefile(source):
        raise RenderError(f"{out_path} is the source video: exporting there would overwrite it")
    tracks = list(tracks)  # read twice below, and it may be a generator
    check_audio(state)
    renderer = FrameRenderer(state, tracks)
    info = probe(source)
    index = FrameIndex.build(source)
    audit = audit_masks(state, tracks, duration=index.end, frame_times=index.times.tolist())

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=out_path.parent, prefix=f".{out_path.stem}.", suffix=".mp4")
    os.close(fd)
    tmp = Path(name)
    try:
        frames, duration = _write(source, info, index, state, renderer, tmp)
        os.replace(tmp, out_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return RenderResult(out_path, frames, duration, tuple(renderer.issues), audit)


def _write(
    source: Path,
    info: VideoInfo,
    index: FrameIndex,
    state: TimelineState,
    renderer: FrameRenderer,
    path: Path,
) -> tuple[int, float]:
    # yuv420p stores colour at half resolution, so it needs an even width and height.
    width, height = info.width // 2 * 2, info.height // 2 * 2
    try:
        with (
            av.open(str(path), "w", format="mp4", options={"movflags": "+faststart"}) as out,
            VideoReader(source, index=index) as reader,
        ):
            video = out.add_stream(VIDEO_CODEC, rate=info.fps)
            video.width, video.height, video.pix_fmt = width, height, PIX_FMT
            # Output timestamps in the source's time base keep variable-frame-rate timing.
            video.codec_context.time_base = info.time_base
            video.options = {"crf": str(CRF)}
            context = video.codec_context
            context.colorspace, context.color_range = COLORSPACE, COLOR_RANGE
            context.color_primaries, context.color_trc = BT709_TAG, BT709_TAG
            sound = None
            if info.audio is not None:
                sound = _AudioWriter(out, source, info.audio, index.end, state)

            frames, last_pts = 0, -1
            for frame in reader.iter_frames():
                if state.is_cut(frame.time):
                    continue
                following = frame.index + 1
                until = index.time_of(following) if following < len(index) else index.end
                image = renderer.render(frame.image, frame.time, until)[:height, :width]
                encoded = _to_bt709(image)
                encoded.time_base = info.time_base
                output_time = state.source_to_output(frame.time)
                # Rounding after a cut could land on the previous frame's tick: keep it after.
                encoded.pts = last_pts = max(round(output_time / info.time_base), last_pts + 1)
                out.mux(video.encode(encoded))
                frames += 1
                if sound is not None:
                    sound.write_until(output_time)
            if frames == 0:
                raise RenderError("the cuts remove the whole clip: nothing to render")
            out.mux(video.encode())
            if sound is not None:
                sound.finish()
    except av.error.FFmpegError as e:
        raise RenderError(f"encoding {path.name} failed: {e}") from e
    return frames, state.source_to_output(index.end)


def _to_bt709(image: np.ndarray) -> av.VideoFrame:
    """An RGB image as a tagged BT.709 frame. Left to itself, the conversion to yuv420p uses
    BT.601 and tags nothing, and players then show HD video with the wrong colours."""
    frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(image), format="rgb24").reformat(
        format=PIX_FMT, dst_colorspace=COLORSPACE, dst_color_range=COLOR_RANGE
    )
    frame.colorspace, frame.color_range = COLORSPACE, COLOR_RANGE
    return frame


def _encodable_rate(rate: int) -> int:
    rates = av.codec.Codec(AUDIO_CODEC, "w").audio_rates
    return FALLBACK_SAMPLE_RATE if rates and rate not in rates else rate


class _AudioWriter:
    """Encodes a clip's sound a chunk at a time, called along with the picture."""

    def __init__(
        self,
        out: OutputContainer,
        source: Path,
        audio: AudioInfo,
        end: float,
        state: TimelineState,
    ) -> None:
        self._out = out
        self._rate = _encodable_rate(audio.sample_rate)
        # The AAC encoder refuses some layouts, and some files don't name theirs ("2 channels"),
        # so the output is always mono or stereo.
        self._layout = "mono" if audio.channels == 1 else "stereo"
        self._stream = out.add_stream(AUDIO_CODEC, rate=self._rate, layout=self._layout)
        self._chunks = self._edited_chunks(source, round(end * self._rate), state)
        self._written = 0

    def _edited_chunks(
        self, source: Path, last_sample: int, state: TimelineState
    ) -> Iterator[np.ndarray]:
        """The sound up to ``last_sample`` (none past the last frame), muted and cut."""
        first = 0
        for chunk in iter_audio_chunks(
            source, AUDIO_CHUNK_SECONDS, self._rate, mono=False, layout=self._layout
        ):
            if first >= last_sample:
                return
            samples = chunk.samples[: last_sample - first]
            yield apply_audio_edits(samples, self._rate, state, first_sample=first)
            first += len(chunk.samples)

    def write_until(self, time: float) -> None:
        """Encode sound up to ``time`` seconds of output."""
        while self._written < time * self._rate:
            samples = next(self._chunks, None)
            if samples is None:
                return
            self._encode(samples)

    def finish(self) -> None:
        for samples in self._chunks:
            self._encode(samples)
        self._out.mux(self._stream.encode())

    def _encode(self, samples: np.ndarray) -> None:
        if not len(samples):
            return  # the whole chunk was cut
        frame = av.AudioFrame.from_ndarray(
            np.ascontiguousarray(samples.T), format="fltp", layout=self._layout
        )
        frame.sample_rate = self._rate
        frame.time_base = Fraction(1, self._rate)
        frame.pts = self._written
        self._out.mux(self._stream.encode(frame))
        self._written += len(samples)
