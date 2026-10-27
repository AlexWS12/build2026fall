"""Video I/O: probe, decode frames, frame<->time, audio extraction (F-03)."""

from vibe.video.audio import AudioChunk, extract_audio, iter_audio_chunks, write_wav
from vibe.video.decode import Frame, VideoReader
from vibe.video.errors import NoAudioStream, NoVideoStream, UnreadableMedia, VideoError
from vibe.video.probe import AudioInfo, VideoInfo, probe
from vibe.video.timebase import FrameIndex

__all__ = [
    "AudioChunk",
    "AudioInfo",
    "Frame",
    "FrameIndex",
    "NoAudioStream",
    "NoVideoStream",
    "UnreadableMedia",
    "VideoError",
    "VideoInfo",
    "VideoReader",
    "extract_audio",
    "iter_audio_chunks",
    "probe",
    "write_wav",
]
