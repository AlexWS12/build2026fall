"""Errors raised by the video layer. Catch ``VideoError`` to handle all of them."""


class VideoError(Exception):
    """Base class for every video I/O error."""


class UnreadableMedia(VideoError):
    """The file exists but isn't a media file ffmpeg can read (corrupt, truncated, wrong type)."""


class NoVideoStream(VideoError):
    """The file is readable media but has no video stream (e.g. an audio-only file)."""


class NoAudioStream(VideoError):
    """Audio was asked for, but the file has no audio stream."""
