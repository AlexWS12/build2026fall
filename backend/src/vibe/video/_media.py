"""Opening a media file, with PyAV's errors mapped to the video layer's own."""

from pathlib import Path

import av
import av.error
from av.container import InputContainer
from av.video.stream import VideoStream

from vibe.video.errors import NoVideoStream, UnreadableMedia


def open_media(path: Path) -> InputContainer:
    """Open ``path`` for reading. A missing file raises the standard ``FileNotFoundError``."""
    try:
        return av.open(str(path))
    except av.error.FileNotFoundError:
        raise
    except av.error.FFmpegError as e:
        raise UnreadableMedia(f"{path}: {e}") from e


def video_stream(container: InputContainer, path: Path) -> VideoStream:
    """The first video stream, or ``NoVideoStream``."""
    if not container.streams.video:
        raise NoVideoStream(f"{path} has no video stream")
    return container.streams.video[0]
