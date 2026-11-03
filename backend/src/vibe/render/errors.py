"""Errors raised by the renderer. Catch ``RenderError`` to handle all of them."""


class RenderError(Exception):
    """A render that can't give a correct output (e.g. the cuts leave nothing to render)."""


class UnsupportedEdit(RenderError):
    """The timeline has an edit this renderer can't apply yet. Raised before anything is
    written, so a privacy edit is never silently missing from an output."""
