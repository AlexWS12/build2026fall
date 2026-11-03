"""Renderer: export path and per-frame path (F-04). The only code that writes video.

``FrameRenderer`` / ``render_frame`` apply a timeline's masks to one frame (preview, live).
``render_audio`` silences the mutes and removes the cuts from a clip's samples.
"""

from vibe.render.audio import check_audio, render_audio
from vibe.render.errors import RenderError, UnsupportedEdit
from vibe.render.frame import FrameRenderer, RenderIssue, check_video, render_frame

__all__ = [
    "FrameRenderer",
    "RenderError",
    "RenderIssue",
    "UnsupportedEdit",
    "check_audio",
    "check_video",
    "render_audio",
    "render_frame",
]
