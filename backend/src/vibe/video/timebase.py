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
    """

    def __init__(self, pts: np.ndarray, time_base: Fraction, last_duration: int) -> None:
        if len(pts) == 0:
            raise ValueError("a FrameIndex needs at least one frame")
        pts = np.sort(np.asarray(pts, dtype=np.int64))
        self._pts = pts - pts[0]
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
            last_pts, last_duration = None, 0
            for packet in container.demux(stream):
                if packet.size == 0 or packet.pts is None or packet.is_discard:
                    continue  # flush packets and frames an edit list hides
                pts.append(packet.pts)
                # Packets come in decode order: the last frame shown isn't always the last read.
                if last_pts is None or packet.pts > last_pts:
                    last_pts, last_duration = packet.pts, packet.duration or 0
        if not pts:
            raise UnreadableMedia(f"{path}: video stream has no frames")
        return cls(np.array(pts), Fraction(time_base), last_duration)

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

    def time_of(self, index: int) -> float:
        """The time frame ``index`` is first shown, in seconds."""
        if not 0 <= index < len(self):
            raise IndexError(f"frame {index} out of range: the clip has {len(self)} frames")
        return float(self._times[index])

    def frame_at(self, time: float) -> int:
        """The frame shown at ``time`` seconds."""
        if not 0 <= time < self._end:
            raise ValueError(f"time {time} s out of range: the clip covers [0, {self._end}) s")
        return int(np.searchsorted(self._times, time + self._tolerance, side="right")) - 1
