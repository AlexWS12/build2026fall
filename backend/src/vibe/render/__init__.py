"""Renderer: export path and per-frame path (F-04). The only code that writes video.

``FrameRenderer`` / ``render_frame`` apply a timeline's masks to one frame (preview, live).
``render_audio`` silences the mutes and removes the cuts from a clip's samples.
``export`` renders a whole clip into an MP4.
``audit_masks`` says how far to trust each mask's box, with or without a render.
"""

from vibe.render.audio import apply_audio_edits, check_audio, render_audio
from vibe.render.errors import NothingToRender, RenderError, UnsupportedEdit
from vibe.render.export import RenderResult, export
from vibe.render.frame import FrameRenderer, RenderIssue, check_video, render_frame
from vibe.render.mask_report import MaskAudit, audit_masks

__all__ = [
    "FrameRenderer",
    "NothingToRender",
    "MaskAudit",
    "RenderError",
    "RenderIssue",
    "RenderResult",
    "UnsupportedEdit",
    "apply_audio_edits",
    "audit_masks",
    "check_audio",
    "check_video",
    "export",
    "render_audio",
    "render_frame",
]
