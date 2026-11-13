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
from vibe.modules.denoise import (
    DenoiseError,
    NoiseProfile,
    ProfileSource,
    check_backend,
    learn_noise_from_file,
)
from vibe.render.audio import DenoiseMix, apply_audio_edits, check_audio, denoise_spans
from vibe.render.errors import RenderError
from vibe.render.frame import FrameRenderer, RenderIssue
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


def export(
    source: str | Path,
    state: TimelineState,
    tracks: Iterable[Track],
    out_path: str | Path,
    noise_profile: ProfileSource | None = None,
) -> RenderResult:
    """Render ``source`` with ``state`` and ``tracks`` into an MP4 at ``out_path``.

    ``noise_profile(sample_rate, layout)`` gives the noise profile for denoise spans, e.g. from
    the analysis cache (``cached_noise_profile``). Without it, export learns one from ``source``
    first, which reads the sound twice.

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
    check_audio(state)
    renderer = FrameRenderer(state, tracks)
    info = probe(source)
    profile = _profile(source, info, state, noise_profile)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=out_path.parent, prefix=f".{out_path.stem}.", suffix=".mp4")
    os.close(fd)
    tmp = Path(name)
    try:
        frames, duration = _write(source, info, state, renderer, tmp, profile)
        os.replace(tmp, out_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return RenderResult(out_path, frames, duration, tuple(renderer.issues))


def _profile(
    source: Path, info: VideoInfo, state: TimelineState, given: ProfileSource | None
) -> NoiseProfile | None:
    """Pass 1 of denoise, before anything is written: the profile at the output's rate and
    layout, or None when there's nothing to denoise."""
    if info.audio is None or not denoise_spans(state):
        return None
    try:
        check_backend()
    except DenoiseError as e:
        raise RenderError(f"can't apply denoise: {e}") from e
    rate, layout = _audio_format(info.audio)
    profile = given(rate, layout) if given else learn_noise_from_file(source, rate, layout)
    channels = 1 if layout == "mono" else 2
    if profile.sample_rate != rate or profile.channels != channels:
        raise RenderError(
            f"the noise profile is for {profile.sample_rate} Hz, {profile.channels} channels; "
            f"the export is {rate} Hz, {channels} channels"
        )
    return profile


def _audio_format(audio: AudioInfo) -> tuple[int, str]:
    """The output's sample rate and layout. The AAC encoder refuses some layouts, and some files
    don't name theirs ("2 channels"), so the output is always mono or stereo."""
    return _encodable_rate(audio.sample_rate), "mono" if audio.channels == 1 else "stereo"


def _write(
    source: Path,
    info: VideoInfo,
    state: TimelineState,
    renderer: FrameRenderer,
    path: Path,
    profile: NoiseProfile | None,
) -> tuple[int, float]:
    index = FrameIndex.build(source)
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
                sound = _AudioWriter(out, source, info.audio, index.end, state, profile)

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
        profile: NoiseProfile | None,
    ) -> None:
        self._out = out
        self._rate, self._layout = _audio_format(audio)
        self._stream = out.add_stream(AUDIO_CODEC, rate=self._rate, layout=self._layout)
        self._chunks = self._edited_chunks(source, round(end * self._rate), state, profile)
        self._written = 0

    def _edited_chunks(
        self,
        source: Path,
        last_sample: int,
        state: TimelineState,
        profile: NoiseProfile | None,
    ) -> Iterator[np.ndarray]:
        """The sound up to ``last_sample`` (none past the last frame), denoised, muted and cut.

        Denoise hands back sound about one FFT frame behind what it's given; ``first`` counts
        what comes out, so mutes and cuts still land on the right samples.
        """
        mix: DenoiseMix | None = None
        read = first = 0
        for chunk in iter_audio_chunks(
            source, AUDIO_CHUNK_SECONDS, self._rate, mono=False, layout=self._layout
        ):
            if read >= last_sample:
                break
            samples = chunk.samples[: last_sample - read]
            read += len(samples)
            if profile is not None:
                mix = mix or DenoiseMix(profile, state, samples.dtype)
                samples = mix.push(samples)
            if len(samples):
                yield apply_audio_edits(samples, self._rate, state, first_sample=first)
                first += len(samples)
        if mix is not None:
            samples = mix.finish()
            if len(samples):
                yield apply_audio_edits(samples, self._rate, state, first_sample=first)

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
