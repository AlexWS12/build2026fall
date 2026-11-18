"""F-31: transcription. No GPU, no network, no model: faster-whisper is replaced by a fake."""

import re
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from vibe.modules import ModuleError, NoAnalysis, make_analysis, run_analysis
from vibe.modules.remote import RemoteClient, create_app
from vibe.modules.transcribe import (
    TranscribeError,
    TranscriptAnalysis,
    _whisper,
    cached_transcript,
    main,
    transcribe,
    transcribe_video,
)
from vibe.storage import Storage, VideoRef
from vibe.video import write_wav

RATE = 16000
KEY = TranscriptAnalysis().key


class FakeWhisper:
    """Stands in for ``faster_whisper.WhisperModel``; counts how often it transcribes."""

    calls = 0
    loads = 0

    def __init__(self, size: str, device: str, compute_type: str) -> None:
        assert (device, compute_type) == ("cpu", "int8")
        FakeWhisper.loads += 1

    def transcribe(self, audio: np.ndarray, word_timestamps: bool):
        assert word_timestamps and audio.dtype == np.float32 and audio.ndim == 1
        FakeWhisper.calls += 1
        words = [
            SimpleNamespace(start=0.1, end=0.4, word=" Hello", probability=0.9),
            SimpleNamespace(start=0.4, end=0.8, word=" there.", probability=0.7),
        ]
        segment = SimpleNamespace(start=0.1, end=0.8, text=" Hello there.", words=words)
        return (s for s in [segment]), SimpleNamespace(language="en")


@pytest.fixture(autouse=True)
def _fresh_model_cache() -> None:
    """``_whisper`` keeps loaded models; each test swaps in its own fake package."""
    _whisper.cache_clear()


@pytest.fixture
def whisper(monkeypatch: pytest.MonkeyPatch) -> type[FakeWhisper]:
    FakeWhisper.calls = FakeWhisper.loads = 0
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeWhisper))
    return FakeWhisper


@pytest.fixture
def stored(tmp_path: Path) -> tuple[Storage, VideoRef]:
    """A 1 s audio file in storage (``extract_audio`` reads it like a video's audio track)."""
    wav = tmp_path / "talk.wav"
    write_wav(wav, np.zeros(RATE, np.float32), RATE)
    storage = Storage(tmp_path / "data")
    return storage, storage.add_video(wav)


def test_local_keeps_segments_words_times_and_language(whisper: type[FakeWhisper]) -> None:
    t = transcribe(np.zeros(RATE, np.float32), RATE, backend="local")

    assert t.language == "en" and t.model == KEY
    [segment] = t.segments
    assert (segment.start, segment.end, segment.text) == (0.1, 0.8, "Hello there.")
    assert [(w.text, w.start, w.end) for w in segment.words] == [
        ("Hello", 0.1, 0.4),
        ("there.", 0.4, 0.8),
    ]


@pytest.mark.parametrize("backend", ["stub", "remote"])
def test_only_local_runs_a_model(whisper: type[FakeWhisper], backend: str) -> None:
    with pytest.raises(TranscribeError):
        transcribe(np.zeros(RATE, np.float32), RATE, backend=backend)
    assert whisper.calls == 0


def test_missing_package_says_how_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(TranscribeError, match="uv pip install faster-whisper"):
        transcribe(np.zeros(RATE, np.float32), RATE, backend="local")


def test_wrong_rate_is_refused(whisper: type[FakeWhisper]) -> None:
    with pytest.raises(ValueError, match="16000 Hz"):
        transcribe(np.zeros(44100, np.float32), 44100, backend="local")


def test_a_video_is_transcribed_once_then_read_from_the_cache(
    whisper: type[FakeWhisper], stored: tuple[Storage, VideoRef]
) -> None:
    storage, ref = stored

    first = transcribe_video(storage, ref, backend="local")
    again = transcribe_video(storage, ref, backend="local")

    assert storage.cache_path(ref.sha256, "transcript", KEY).is_file()
    assert again == first and whisper.calls == 1


def test_stub_replays_the_cache_and_never_invents_an_empty_transcript(
    whisper: type[FakeWhisper], stored: tuple[Storage, VideoRef]
) -> None:
    storage, ref = stored
    with pytest.raises(NoAnalysis, match="no cached transcript"):
        transcribe_video(storage, ref, backend="stub")

    made = transcribe_video(storage, ref, backend="local")

    assert transcribe_video(storage, ref, backend="stub") == made
    assert whisper.calls == 1


def test_the_model_is_loaded_once(whisper: type[FakeWhisper]) -> None:
    transcribe(np.zeros(RATE, np.float32), RATE, backend="local")
    transcribe(np.zeros(RATE, np.float32), RATE, backend="local")
    assert (whisper.calls, whisper.loads) == (2, 1)


@pytest.mark.parametrize("content", ["{not json", '{"old": "format"}'])
def test_an_unreadable_cache_is_an_error_naming_the_file_and_is_kept(
    whisper: type[FakeWhisper], stored: tuple[Storage, VideoRef], content: str
) -> None:
    # F-08 never replaces a cache file silently; the error says which one to delete.
    storage, ref = stored
    path = storage.cache_path(ref.sha256, "transcript", KEY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)

    with pytest.raises(ModuleError, match=f"{re.escape(path.name)}.*delete it"):
        cached_transcript(storage, ref)
    with pytest.raises(ModuleError, match=f"{re.escape(path.name)}.*delete it"):
        transcribe_video(storage, ref, backend="local")
    assert path.read_text() == content and whisper.calls == 0

    path.unlink()
    assert transcribe_video(storage, ref, backend="local") == cached_transcript(storage, ref)


def test_the_key_names_the_model_and_changes_with_it() -> None:
    assert KEY.startswith("faster-whisper-small-int8-")
    assert TranscriptAnalysis("tiny").key.startswith("faster-whisper-tiny-int8-")
    assert TranscriptAnalysis("tiny").key != KEY


def test_the_model_server_can_rebuild_it_from_its_settings() -> None:
    # The server gets only the module name and settings, and calls cls(**settings).
    analysis = TranscriptAnalysis("tiny")
    rebuilt = make_analysis(analysis.module, analysis.settings)
    assert isinstance(rebuilt, TranscriptAnalysis) and rebuilt.key == analysis.key


def test_remote_transcribes_on_the_model_server_and_caches_here(
    whisper: type[FakeWhisper],
    stored: tuple[Storage, VideoRef],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, ref = stored
    http = TestClient(create_app(Storage(tmp_path / "server")))

    def transport(method, url, headers, body, timeout):
        content = body.read_bytes() if isinstance(body, Path) else body
        r = http.request(
            method, url.removeprefix("http://gpu-box:8765"), headers=headers, content=content
        )
        return r.status_code, r.json()

    client = RemoteClient("http://gpu-box:8765", frozenset({"gpu-box"}), transport=transport)
    monkeypatch.setattr(RemoteClient, "from_env", classmethod(lambda cls: client))

    made = transcribe_video(storage, ref, backend="remote")

    assert made.segments[0].text == "Hello there." and whisper.calls == 1
    # Cached here, so this machine can now replay it with the stub.
    assert transcribe_video(storage, ref, backend="stub") == made
    assert run_analysis(storage, ref, TranscriptAnalysis(), "stub").source == "cache"


def test_cli_prints_the_cache_path_and_words(
    whisper: type[FakeWhisper],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("VIBE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VIBE_BACKEND", "local")
    wav = tmp_path / "talk.wav"
    write_wav(wav, np.zeros(RATE, np.float32), RATE)

    assert main([str(wav)]) == 0
    out = capsys.readouterr().out
    assert KEY in out and "Hello there." in out and "Hello@0.10" in out

    assert main([str(tmp_path / "missing.mp4")]) == 1
    assert capsys.readouterr().err.startswith("error:")
