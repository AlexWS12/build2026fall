"""Read a clip's metadata without decoding the whole file."""

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import av.error

from vibe.video._media import open_media, video_stream
from vibe.video.errors import UnreadableMedia


@dataclass(frozen=True)
class AudioInfo:
    sample_rate: int
    channels: int
    codec: str


@dataclass(frozen=True)
class VideoInfo:
    """What's in a clip. Field names follow the contract's ``video`` block where they overlap.

    ``width`` and ``height`` are as displayed, after ``rotation`` is applied, so a portrait phone
    clip reports a portrait size. ``fps`` is exact; for a variable-frame-rate clip it's the
    average. ``frame_count`` comes from the container header, or is estimated from duration × fps
    when the container doesn't store it (MKV, WebM); ``FrameIndex`` has the exact count.
    """

    path: Path
    duration: float
    width: int
    height: int
    rotation: int
    fps: Fraction
    time_base: Fraction
    frame_count: int
    is_vfr: bool
    start_time: float
    codec: str
    pix_fmt: str
    audio: AudioInfo | None


def probe(path: str | Path) -> VideoInfo:
    """Return a clip's metadata.

    Raises ``FileNotFoundError`` for a missing file, ``UnreadableMedia`` for a file ffmpeg can't
    read, and ``NoVideoStream`` for media with no video (e.g. an audio-only file).
    """
    path = Path(path)
    with open_media(path) as container:
        video = video_stream(container, path)
        time_base = video.time_base
        fps = video.average_rate or video.guessed_rate
        if time_base is None or not fps:
            raise UnreadableMedia(f"{path}: video stream has no frame rate")

        if video.duration is not None:
            duration = float(video.duration * time_base)
        elif container.duration is not None:
            duration = container.duration / av.time_base
        else:
            raise UnreadableMedia(f"{path}: duration unknown")
        start_time = float(video.start_time * time_base) if video.start_time else 0.0

        # Rotation lives in per-frame side data, so decode the first frame. This also catches
        # files whose header parses but whose video doesn't decode.
        try:
            first = next(container.decode(video))
        except (StopIteration, av.error.FFmpegError) as e:
            raise UnreadableMedia(f"{path}: no decodable video frame") from e
        rotation = int(first.rotation) % 360

        width, height = video.codec_context.width, video.codec_context.height
        if rotation in (90, 270):
            width, height = height, width

        frame_count = video.frames or round(duration * fps)
        # A constant-rate stream's average rate equals its base (nominal) rate.
        is_vfr = video.base_rate is not None and video.average_rate != video.base_rate

        audio = None
        if container.streams.audio:
            stream = container.streams.audio[0]
            audio = AudioInfo(
                sample_rate=stream.rate,
                channels=stream.channels,
                codec=stream.codec_context.name,
            )

        return VideoInfo(
            path=path,
            duration=duration,
            width=width,
            height=height,
            rotation=rotation,
            fps=Fraction(fps),
            time_base=Fraction(time_base),
            frame_count=frame_count,
            is_vfr=is_vfr,
            start_time=start_time,
            codec=video.codec_context.name,
            pix_fmt=video.codec_context.pix_fmt,
            audio=audio,
        )
