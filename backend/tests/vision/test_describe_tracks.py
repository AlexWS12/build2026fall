"""F-13: which tracks match a description. No GPU, no network; clips are made on the fly."""

import json
from pathlib import Path

import numpy as np
import pytest
from vision_helpers import BLUE, FPS, HEIGHT, RED, WIDTH

from vibe.contract import Keyframe, Track
from vibe.modules.vision import cache
from vibe.modules.vision.__main__ import main
from vibe.modules.vision.describe import (
    KIND,
    ColorJudge,
    DescribeError,
    Judge,
    VideoDescriber,
    color_fraction,
    describe_tracks,
    make_judge,
    project_describer,
    sample_keyframes,
)
from vibe.storage import Storage


def box(x1: int, y1: int, x2: int, y2: int) -> tuple[float, float, float, float]:
    return (x1 / WIDTH, y1 / HEIGHT, x2 / WIDTH, y2 / HEIGHT)


def red_track(label: str = "thing") -> Track:
    """Follows the red square in ``square_frames``."""
    keys = [
        Keyframe(t=i / FPS, box=box(4 + 4 * i, 10, 24 + 4 * i, 30), conf=0.9, state="detected")
        for i in range(10)
    ]
    return Track(track_id="trk_red", label=label, source="detector", keyframes=keys)


def blue_track(label: str = "thing") -> Track:
    keys = [
        Keyframe(t=i / FPS, box=box(76, 44, 88, 56), conf=0.9, state="detected") for i in range(10)
    ]
    return Track(track_id="trk_blue", label=label, source="detector", keyframes=keys)


def solid(color: tuple[int, int, int], size: int = 20) -> np.ndarray:
    return np.full((size, size, 3), color, dtype=np.uint8)


# --- the colour stub ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rgb", "color"),
    [
        ((255, 0, 0), "red"),
        ((230, 20, 60), "red"),
        ((255, 140, 0), "orange"),
        ((250, 230, 20), "yellow"),
        ((20, 160, 40), "green"),
        ((0, 0, 255), "blue"),
        ((130, 40, 200), "purple"),
        ((255, 60, 180), "pink"),
        ((250, 250, 250), "white"),
        ((10, 10, 10), "black"),
        ((128, 128, 128), "grey"),
    ],
)
def test_color_fraction_names_plain_colors(rgb: tuple[int, int, int], color: str) -> None:
    assert color_fraction(solid(rgb), color) == 1.0
    other = "blue" if color != "blue" else "red"
    assert color_fraction(solid(rgb), other) == 0.0


@pytest.mark.parametrize(
    ("description", "color"),
    [
        ("in red", "red"),
        ("wearing a blue shirt", "blue"),
        ("in a gray hoodie", "grey"),
        ("dressed in RED", "red"),
        ("in red and white", None),  # two colours: which one?
        ("wearing a red hat", None),  # the stub looks at the chest, not at hats
        ("the tall one", None),
        ("with a backpack", None),
    ],
)
def test_the_stub_only_reads_one_clothing_color(description: str, color: str | None) -> None:
    assert ColorJudge.color_in(description) == color


def test_the_stub_says_yes_no_or_unsure_from_the_pixels() -> None:
    judge = ColorJudge()
    mixed = solid(RED)
    mixed[:, :7] = BLUE  # of the middle half (columns 5..14), 2 in 10 are blue

    assert judge.judge([solid(RED)], "thing", "in red") == "yes"
    assert judge.judge([solid(BLUE)], "thing", "in red") == "no"
    assert judge.judge([mixed], "thing", "in blue") == "unsure"


def test_the_stub_needs_every_crop_to_agree() -> None:
    judge = ColorJudge()

    # Red in two crops and blue in one: maybe two people under one id. Not a yes, and not a no.
    assert judge.judge([solid(RED), solid(RED), solid(BLUE)], "thing", "in red") == "unsure"
    assert judge.judge([solid(BLUE), solid(BLUE), solid(RED)], "thing", "in red") == "unsure"
    assert judge.judge([solid(RED), solid(RED), solid(RED)], "thing", "in red") == "yes"
    assert judge.judge([solid(BLUE), solid(BLUE)], "thing", "in red") == "no"


def test_the_stub_is_unsure_about_what_it_cant_check() -> None:
    judge = ColorJudge()

    assert judge.judge([solid(RED)], "person", "wearing a hat") == "unsure"
    assert judge.judge([], "person", "in red") == "unsure"


def test_the_stub_looks_at_a_persons_chest_not_the_whole_box() -> None:
    person = np.zeros((100, 40, 3), dtype=np.uint8)
    person[20:60] = RED  # red top, black everything else

    assert ColorJudge().judge([person], "person", "in red") == "yes"
    assert ColorJudge().judge([person], "person", "in black") == "no"


def test_the_stub_thresholds_must_leave_a_gap() -> None:
    with pytest.raises(ValueError):
        ColorJudge(yes_at=0.1, no_at=0.2)


def test_only_the_stub_backend_has_a_judge_so_far() -> None:
    assert isinstance(make_judge("stub"), ColorJudge)
    for backend in ("local", "remote"):
        with pytest.raises(DescribeError, match="isn't built yet"):
            make_judge(backend)


# --- crops --------------------------------------------------------------------------------


def test_sampled_keyframes_are_spread_and_skip_the_ends() -> None:
    assert sample_keyframes(red_track(), 3) == [1, 5, 8]
    assert sample_keyframes(red_track(), 20) == list(range(10))


def test_sampling_prefers_keyframes_a_model_saw() -> None:
    track = red_track()
    keys = [k.model_copy(update={"state": "uncertain"}) for k in track.keyframes]
    keys[3] = track.keyframes[3]
    mostly_unsure = track.model_copy(update={"keyframes": keys})
    all_unsure = track.model_copy(update={"keyframes": [keys[0], keys[1]]})

    assert sample_keyframes(mostly_unsure, 3) == [3]
    assert sample_keyframes(all_unsure, 3) == [0, 1]


def test_describe_tracks_reads_the_crops_from_the_video(square_clip: Path) -> None:
    verdicts = describe_tracks(square_clip, [red_track(), blue_track()], "in red", ColorJudge())

    assert verdicts == {"trk_red": "yes", "trk_blue": "no"}


def test_a_track_too_small_to_crop_is_unsure(square_clip: Path) -> None:
    key = Keyframe(t=0, box=(0.5, 0.5, 0.51, 0.51), conf=0.9, state="detected")
    speck = Track(track_id="trk_speck", label="thing", source="detector", keyframes=[key])

    assert describe_tracks(square_clip, [speck], "in red", ColorJudge()) == {"trk_speck": "unsure"}


def test_keyframes_past_the_end_of_the_video_use_the_last_frame(square_clip: Path) -> None:
    key = Keyframe(t=99, box=box(76, 44, 88, 56), conf=0.9, state="detected")
    late = Track(track_id="trk_late", label="thing", source="detector", keyframes=[key])

    assert describe_tracks(square_clip, [late], "in blue", ColorJudge()) == {"trk_late": "yes"}


def test_an_unreadable_video_is_an_error_not_a_no(tmp_path: Path) -> None:
    with pytest.raises(DescribeError, match="can't read"):
        describe_tracks(tmp_path / "missing.mp4", [red_track()], "in red", ColorJudge())


# --- the cached describer -----------------------------------------------------------------


class Counting(Judge):
    """Says yes to everything and remembers what it was asked."""

    name = "counting"

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[str] = []

    def judge(self, crops, label, description):
        self.asked.append(description)
        return "yes"


@pytest.fixture
def describer(tmp_path: Path, square_clip: Path) -> VideoDescriber:
    store = Storage(tmp_path / "data")
    ref = store.add_video(square_clip)
    return VideoDescriber(store, ref.sha256, square_clip, Counting())


def test_the_describer_asks_the_judge_once_per_track(describer: VideoDescriber) -> None:
    tracks = [red_track(), blue_track()]

    assert describer.ask(tracks, "In  Red") == {"trk_red": "yes", "trk_blue": "yes"}
    assert describer.ask(tracks, "in red") == {"trk_red": "yes", "trk_blue": "yes"}
    assert describer.judge.asked == ["in red", "in red"]  # two tracks, once each


def test_a_new_track_is_asked_without_asking_the_others_again(describer: VideoDescriber) -> None:
    describer.ask([red_track()], "in red")
    describer.ask([red_track(), blue_track()], "in red")

    assert len(describer.judge.asked) == 2


def test_a_corrected_box_under_the_same_id_is_asked_again(describer: VideoDescriber) -> None:
    describer.ask([red_track()], "in red")
    moved = blue_track().model_copy(update={"track_id": "trk_red"})
    describer.ask([moved], "in red")

    assert len(describer.judge.asked) == 2


def test_another_description_is_its_own_cache_file(describer: VideoDescriber) -> None:
    describer.ask([red_track()], "in red")
    describer.ask([red_track()], "in blue")

    folder = describer.storage.cache_path(describer.sha256, KIND, "x").parent
    assert len(list(folder.glob("*.json"))) == 2
    assert describer.key("in red") != describer.key("in blue")


def test_an_empty_description_is_refused(describer: VideoDescriber) -> None:
    with pytest.raises(DescribeError):
        describer.ask([red_track()], "   ")


def test_the_cache_holds_verdicts_and_no_pixels(describer: VideoDescriber) -> None:
    describer.ask([red_track()], "in red")

    path = describer.storage.cache_path(describer.sha256, KIND, describer.key("in red"))
    data = json.loads(path.read_text())
    assert set(data) == {"judge", "description", "verdicts"}
    assert set(data["verdicts"]["trk_red"]) == {"signature", "verdict"}


def test_project_describer_reads_the_projects_stored_video(
    tmp_path: Path, square_clip: Path
) -> None:
    store = Storage(tmp_path / "data")
    project = store.create_project(square_clip)

    verdicts = project_describer(store, project, ColorJudge()).ask([red_track()], "in red")

    assert verdicts == {"trk_red": "yes"}


# --- CLI ----------------------------------------------------------------------------------


def test_describe_command_prints_a_verdict_per_track(capsys, tmp_path, square_clip) -> None:
    data = str(tmp_path / "data")
    detectors = ["-d", "stub:color=red,label=person", "-d", "stub:color=blue,label=face"]
    assert main(["--data-dir", data, "analyze", str(square_clip), *detectors]) == 0
    capsys.readouterr()

    code = main(["--data-dir", data, "describe", str(square_clip), "in red", "--all"])
    out, err = capsys.readouterr()

    assert code == 0, err
    assert "judge: color; 1 person tracks" in out
    [line] = [x for x in out.splitlines() if "trk_" in x]
    assert line.split()[0] in ("yes", "unsure")  # a 20 px "person": the chest region is tiny


def test_describe_command_hides_sure_nos_unless_asked(capsys, tmp_path, square_clip) -> None:
    data = str(tmp_path / "data")
    detector = "stub:color=blue,label=thing"
    assert main(["--data-dir", data, "analyze", str(square_clip), "-d", detector]) == 0
    capsys.readouterr()
    ask = ["--data-dir", data, "describe", str(square_clip), "in red", "--label", "thing"]

    assert main(ask) == 0
    hidden, _ = capsys.readouterr()
    assert main([*ask, "--all"]) == 0
    shown, _ = capsys.readouterr()

    assert "verdicts: 0 yes, 0 unsure, 1 no" in hidden
    assert "trk_" not in hidden and "1 tracks that are surely not a match" in hidden
    assert "no     trk_" in shown


def test_describe_command_needs_an_analysis_first(capsys, tmp_path, square_clip) -> None:
    code = main(["--data-dir", str(tmp_path / "data"), "describe", str(square_clip), "in red"])

    assert code == 1
    assert "run `analyze` first" in capsys.readouterr().err
    assert cache.track_keys(Storage(tmp_path / "data"), cache.video_sha256(square_clip)) == []
