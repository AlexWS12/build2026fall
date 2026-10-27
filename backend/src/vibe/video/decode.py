"""Decode frames by number, in order or at random: the per-frame path the renderer and live use.

Frames come out as RGB ``uint8`` arrays shaped (height, width, 3), rotated the way a player shows
them, so a portrait phone clip gives portrait frames.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import av.error
import numpy as np
from av.video.frame import VideoFrame

from vibe.video._media import open_media, video_stream
from vibe.video.errors import UnreadableMedia, VideoError
from vibe.video.timebase import FrameIndex


@dataclass(frozen=True)
class Frame:
    index: int
    time: float
    image: np.ndarray


class VideoReader:
    """An open clip you can read frames from. Use it as a context manager.

    Reading frames in order decodes straight through. Jumping backwards, or far enough ahead to
    pass a keyframe, seeks to the keyframe before the target and decodes forward from there, so
    every read returns exactly the frame asked for.

    ``size`` is an optional (width, height) to scale frames to, as displayed (after rotation).
    """

    def __init__(
        self,
        path: str | Path,
        index: FrameIndex | None = None,
        size: tuple[int, int] | None = None,
    ) -> None:
        self.path = Path(path)
        # Built before opening the decoder, so a bad file fails here with the usual errors.
        self.index = index if index is not None else FrameIndex.build(self.path)
        self._size = size
        self._container = open_media(self.path)
        self._stream = video_stream(self._container, self.path)
        self._stream.thread_type = "AUTO"
        self._frames: Iterator[VideoFrame] | None = None
        self._next = 0  # the frame the decoder will produce next, when _frames is live

    def __enter__(self) -> "VideoReader":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._frames = None
        self._container.close()

    def __len__(self) -> int:
        return len(self.index)

    def read(self, index: int) -> Frame:
        """Frame number ``index``."""
        start = self.index.keyframe_before(index)  # also checks the range
        if self._frames is None or index < self._next or start > self._next:
            self._seek(start)
        target = self.index.pts_of(index)
        assert self._frames is not None
        try:
            for frame in self._frames:
                if frame.pts is None:
                    continue
                if frame.pts == target:
                    self._next = index + 1
                    return self._to_frame(index, frame)
                if frame.pts > target:
                    self._frames = None
                    raise VideoError(f"{self.path}: frame {index} was never decoded")
        except av.error.FFmpegError as e:
            self._frames = None
            raise UnreadableMedia(f"{self.path}: decoding frame {index} failed: {e}") from e
        self._frames = None
        raise VideoError(f"{self.path}: the stream ended before frame {index}")

    def read_at(self, time: float) -> Frame:
        """The frame shown at ``time`` seconds."""
        return self.read(self.index.frame_at(time))

    def iter_frames(self, start: int = 0, end: int | None = None, step: int = 1) -> Iterator[Frame]:
        """Frames ``start``, ``start + step``, ... up to but not including ``end`` (default: the
        last frame)."""
        if step < 1:
            raise ValueError(f"step must be at least 1, got {step}")
        end = len(self) if end is None else end
        for i in range(start, end, step):
            yield self.read(i)

    def _seek(self, keyframe: int) -> None:
        self._container.seek(
            self.index.pts_of(keyframe), stream=self._stream, backward=True, any_frame=False
        )
        self._frames = self._container.decode(self._stream)
        self._next = keyframe

    def _to_frame(self, index: int, frame: VideoFrame) -> Frame:
        turns = (int(frame.rotation) % 360) // 90  # counterclockwise quarter turns
        if self._size is None:
            image = frame.to_ndarray(format="rgb24")
        else:
            width, height = self._size
            if turns % 2:
                width, height = height, width  # scale in stored orientation, then rotate
            image = frame.to_ndarray(format="rgb24", width=width, height=height)
        if turns:
            image = np.ascontiguousarray(np.rot90(image, turns))
        return Frame(index=index, time=self.index.time_of(index), image=image)
