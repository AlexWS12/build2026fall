"""F-21 / F-31: ``find_speech`` finds when words are said, from a transcript. No model needed."""

import json
from pathlib import Path

import pytest

from vibe.modules.transcribe import Segment, Transcript, Word
from vibe.toolbox import ToolContext, ToolError, call_tool
from vibe.toolbox.__main__ import main
from vibe.toolbox.tools import SPEECH_PAD


def words(*timed: tuple[str, float, float]) -> list[Word]:
    return [Word(start=s, end=e, text=t, probability=0.9) for t, s, e in timed]


TRANSCRIPT = Transcript(
    model="test",
    language="es",
    segments=[
        Segment(
            start=0.05,
            end=1.0,
            text="¿Qué tal?",
            words=words(("¿Qué", 0.05, 0.4), ("tal?", 0.4, 1.0)),
        ),
        Segment(
            start=7.0,
            end=8.0,
            text="Hola, amigo",
            words=words(("Hola,", 7.0, 7.5), ("amigo", 7.5, 8.0)),
        ),
        Segment(start=9.0, end=9.9, text="Hola.", words=words(("Hola.", 9.0, 9.9))),
    ],
)


def find(text: str, transcript: Transcript | None = TRANSCRIPT, duration: float | None = 9.95):
    ctx = ToolContext("req_1", "agent", duration=duration, transcript=transcript)
    return call_tool("find_speech", {"text": text}, ctx).time_range


def test_one_match_is_padded_and_clamped_at_zero() -> None:
    r = find("que")  # no accent, no "¿": still found
    assert (r.start, r.end) == (0.0, pytest.approx(0.4 + SPEECH_PAD))


def test_a_phrase_across_two_segments_spans_its_first_and_last_word() -> None:
    r = find("TAL ... hola amigo")  # "tal" ends segment 1; "hola amigo" starts segment 2
    assert r.start == pytest.approx(0.4 - SPEECH_PAD)
    assert r.end == pytest.approx(8.0 + SPEECH_PAD)


def test_the_end_is_clamped_to_the_duration() -> None:
    assert find("amigo hola").end == 9.95


def test_said_twice_asks_which_one() -> None:
    with pytest.raises(ToolError, match=r"said 2 times \(at 7.00s, 9.00s\)") as e:
        find("hola")
    assert "by its time" in str(e.value)  # find_speech itself takes no time range


CONTRACTION = Transcript(
    model="test",
    language="en",
    segments=[
        Segment(
            start=1.0,
            end=2.0,
            text="Don't go, Don.",
            words=words(("Don't", 1.0, 1.4), ("go,", 1.4, 1.6), ("Don.", 1.6, 2.0)),
        )
    ],
)


def test_only_whole_words_match() -> None:
    assert find("don", CONTRACTION).start == pytest.approx(1.6 - SPEECH_PAD)  # not "don't"
    assert find("don't go", CONTRACTION).start == pytest.approx(1.0 - SPEECH_PAD)
    with pytest.raises(ToolError, match="isn't said"):
        find("t", CONTRACTION)


@pytest.mark.parametrize("text", ["adios", "hola amigo hola amigo", "¿?"])
def test_not_said_or_no_words_is_an_error(text: str) -> None:
    with pytest.raises(ToolError):
        find(text)


def test_no_transcript_says_to_transcribe_first() -> None:
    with pytest.raises(ToolError, match="no transcript yet"):
        find("hola", transcript=None)


def test_cli_reads_a_transcript_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "transcript.json"
    path.write_text(TRANSCRIPT.model_dump_json(), encoding="utf-8")

    code = main(["find_speech", "--transcript", str(path), "--args", '{"text": "amigo"}'])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["time_range"]["start"] == 7.5 - SPEECH_PAD
