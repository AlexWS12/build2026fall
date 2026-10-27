"""Frame <-> time for any clip, including variable frame rate (VFR) ones.

Frames are numbered from 0 in the order they're shown. Times are in seconds from the first
frame. The frame shown at time t is the last one whose timestamp is at or before t.
"""

from fractions import Fraction
from pathlib import Path

import numpy as np

from vibe.video._media import open_media, video_stream
from vibe.video.errors import UnreadableMedia


class FrameIndex:
    """Every frame's timestamp, read once from the container's packets, with no decoding.

    The table is exact for constant-rate and VFR clips alike, so nothing assumes ``i / fps``.
    It also records which frames are keyframes, so a decoder knows where it can seek to.
    """

    def __init__(
        self,
        pts: np.ndarray,
        time_base: Fraction,
        last_duration: int,
        keyframe_pts: np.ndarray | None = None,
    ) -> None:
        """``pts`` are raw stream timestamps, in any order. Without ``keyframe_pts``, only the
        first frame counts as a keyframe."""
        if len(pts) == 0:
            raise ValueError("a FrameIndex needs at least one frame")
        pts = np.sort(np.asarray(pts, dtype=np.int64))
        self._raw_pts = pts
        self._pts = pts - pts[0]
        keyframe_pts = pts[:1] if keyframe_pts is None else np.asarray(keyframe_pts, np.int64)
        if not np.all(np.isin(keyframe_pts, pts)):
            raise ValueError("every keyframe timestamp must also be a frame timestamp")
        # Decoding always starts from the first frame, so treat it as a keyframe regardless.
        self._keyframes = np.union1d(np.searchsorted(pts, keyframe_pts), [0])
        self._time_base = time_base
        self._times = self._pts * float(time_base)
        self._times.flags.writeable = False
        self._end = float((int(self._pts[-1]) + last_duration) * time_base)
        # Containers round timestamps to their time base (e.g. 1 ms in WebM), so a time computed
        # as i / fps can land just before frame i's stored timestamp. Half a tick absorbs that.
        self._tolerance = float(time_base) / 2

    @classmethod
    def build(cls, path: str | Path) -> "FrameIndex":
        """Read the frame timestamps of ``path``'s first video stream.

        Raises the same errors as ``probe``.
        """
        path = Path(path)
        with open_media(path) as container:
            stream = video_stream(container, path)
            time_base = stream.time_base
            if time_base is None:
                raise UnreadableMedia(f"{path}: video stream has no time base")
            pts: list[int] = []
            keyframe_pts: list[int] = []
            last_pts, last_duration = None, 0
            for packet in container.demux(stream):
                if packet.size == 0 or packet.pts is None or packet.is_discard:
                    continue  # flush packets and frames an edit list hides
                pts.append(packet.pts)
                if packet.is_keyframe:
                    keyframe_pts.append(packet.pts)
                # Packets come in decode order: the last frame shown isn't always the last read.
                if last_pts is None or packet.pts > last_pts:
                    last_pts, last_duration = packet.pts, packet.duration or 0
        if not pts:
            raise UnreadableMedia(f"{path}: video stream has no frames")
        return cls(np.array(pts), Fraction(time_base), last_duration, np.array(keyframe_pts))

    def __len__(self) -> int:
        return len(self._times)

    @property
    def times(self) -> np.ndarray:
        """Each frame's time in seconds (read-only)."""
        return self._times

    @property
    def end(self) -> float:
        """When the last frame stops being shown, in seconds."""
        return self._end

    @property
    def is_vfr(self) -> bool:
        """True when the gaps between frames aren't all the same."""
        return len(np.unique(np.diff(self._pts))) > 1

    @property
    def keyframes(self) -> np.ndarray:
        """Frame numbers of the keyframes, ascending. Frame 0 is always included."""
        return self._keyframes.copy()

    def keyframe_before(self, index: int) -> int:
        """The last keyframe at or before frame ``index``: where decoding it has to start."""
        self._check(index)
        return int(self._keyframes[np.searchsorted(self._keyframes, index, side="right") - 1])

    def pts_of(self, index: int) -> int:
        """Frame ``index``'s raw timestamp in the stream's time base, for seeking and matching."""
        self._check(index)
        return int(self._raw_pts[index])

    def time_of(self, index: int) -> float:
        """The time frame ``index`` is first shown, in seconds."""
        self._check(index)
        return float(self._times[index])

    def frame_at(self, time: float) -> int:
        """The frame shown at ``time`` seconds."""
        if not 0 <= time < self._end:
            raise ValueError(f"time {time} s out of range: the clip covers [0, {self._end}) s")
        return int(np.searchsorted(self._times, time + self._tolerance, side="right")) - 1

    def _check(self, index: int) -> None:
        if not 0 <= index < len(self):
            raise IndexError(f"frame {index} out of range: the clip has {len(self)} frames")
