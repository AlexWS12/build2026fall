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
    OBJECT_MIN_SEEN,
    ColorJudge,
    DescribeError,
    Judge,
    VideoDescriber,
    color_fraction,
    combine,
    describe_tracks,
    make_judge,
    object_labels,
    project_describer,
    sample_keyframes,
    split_criteria,
    with_object,
)
from vibe.storage import Storage
from vibe.toolbox import ToolContext, ToolError, call_tool


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


def twice(crop: np.ndarray) -> list[np.ndarray]:
    """Two looks at the same thing: the fewest the stub gives a sure answer for."""
    return [crop, crop]


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
        ("with a red shirt", "red"),
        ("with a black", None),  # half of "with a black and white dog": a thing, not clothes
        ("in red #ff0000", None),  # something it can't read was said
        ("in a red shirt no. 10", None),
    ],
)
def test_the_stub_only_reads_one_clothing_color(description: str, color: str | None) -> None:
    assert ColorJudge.color_in(description) == color


def test_the_stub_reads_a_things_own_color_but_not_its_clothes() -> None:
    assert ColorJudge.color_in("red", "car") == "red"
    assert ColorJudge.color_in("the red one", "car") == "red"
    # "faces in red" is about the clothes of whoever the face belongs to, which it can't see.
    assert ColorJudge.color_in("in red", "face") is None
    assert ColorJudge.color_in("wearing a red shirt", "face") is None


def test_the_stub_says_yes_no_or_unsure_from_the_pixels() -> None:
    judge = ColorJudge()
    mixed = solid(RED)
    mixed[:, :7] = BLUE  # of the middle half (columns 5..14), 2 in 10 are blue

    assert judge.judge(twice(solid(RED)), "thing", "red") == "yes"
    assert judge.judge(twice(solid(BLUE)), "thing", "red") == "no"
    assert judge.judge(twice(mixed), "thing", "blue") == "unsure"


def test_the_stub_needs_every_crop_to_agree() -> None:
    judge = ColorJudge()

    # Red in two crops and blue in one: maybe two people under one id. Not a yes, and not a no.
    assert judge.judge([solid(RED), solid(RED), solid(BLUE)], "thing", "red") == "unsure"
    assert judge.judge([solid(BLUE), solid(BLUE), solid(RED)], "thing", "red") == "unsure"
    assert judge.judge([solid(RED), solid(RED), solid(RED)], "thing", "red") == "yes"
    assert judge.judge([solid(BLUE), solid(BLUE)], "thing", "red") == "no"


def test_one_look_is_never_enough_for_a_sure_answer() -> None:
    judge = ColorJudge()

    assert judge.judge([solid(RED)], "person", "in red") == "unsure"
    assert judge.judge([solid(BLUE)], "person", "in red") == "unsure"


@pytest.mark.parametrize(
    ("rgb", "description"),
    [
        ((20, 25, 60), "in blue"),  # navy: too dark to surely be blue, too blue to surely not be
        ((55, 10, 20), "in red"),  # dark burgundy
        ((200, 215, 240), "in blue"),  # pastel blue
        ((0, 130, 130), "in green"),  # teal sits between green and blue
        ((0, 130, 130), "in blue"),
    ],
)
def test_the_stub_is_unsure_about_dark_pale_and_in_between_colors(rgb, description) -> None:
    assert ColorJudge().judge(twice(solid(rgb)), "person", description) == "unsure"


@pytest.mark.parametrize(
    ("rgb", "description"),
    [((150, 80, 60), "in red"), ((198, 134, 66), "in orange"), ((224, 172, 105), "in orange")],
)
def test_skin_is_not_a_red_or_orange_shirt(rgb, description) -> None:
    assert color_fraction(solid(rgb), description.split()[-1]) == 0.0
    assert ColorJudge().judge(twice(solid(rgb)), "person", description) == "unsure"


def test_the_stub_is_unsure_about_what_it_cant_check() -> None:
    judge = ColorJudge()

    assert judge.judge([solid(RED)], "person", "wearing a hat") == "unsure"
    assert judge.judge([], "person", "in red") == "unsure"


def test_the_stub_looks_at_a_persons_chest_not_the_whole_box() -> None:
    person = np.zeros((100, 40, 3), dtype=np.uint8)
    person[20:60] = RED  # red top, black everything else

    assert ColorJudge().judge(twice(person), "person", "in red") == "yes"
    # Black isn't on the chest, but it is in the box (trousers): not a yes, and not a sure no.
    assert ColorJudge().judge(twice(person), "person", "in black") == "unsure"
    assert ColorJudge().judge(twice(person), "person", "in blue") == "no"


def test_a_color_only_outside_the_chest_is_not_a_sure_no() -> None:
    person = np.full((100, 40, 3), 250, dtype=np.uint8)
    person[60:] = RED  # white shirt, red trousers

    assert ColorJudge().judge(twice(person), "person", "in red") == "unsure"


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
    verdicts = describe_tracks(square_clip, [red_track(), blue_track()], "red", ColorJudge())

    assert verdicts == {"trk_red": "yes", "trk_blue": "no"}


def test_a_track_too_small_to_crop_is_unsure(square_clip: Path) -> None:
    key = Keyframe(t=0, box=(0.5, 0.5, 0.51, 0.51), conf=0.9, state="detected")
    speck = Track(track_id="trk_speck", label="thing", source="detector", keyframes=[key])

    assert describe_tracks(square_clip, [speck], "in red", ColorJudge()) == {"trk_speck": "unsure"}


def test_keyframes_past_the_end_of_the_video_show_nothing(square_clip: Path) -> None:
    keys = [Keyframe(t=t, box=box(76, 44, 88, 56), conf=0.9, state="detected") for t in (98, 99)]
    late = Track(track_id="trk_late", label="thing", source="detector", keyframes=keys)

    # The last frame does show blue there, but not at a time this track covers.
    assert describe_tracks(square_clip, [late], "blue", ColorJudge()) == {"trk_late": "unsure"}


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

    verdicts = project_describer(store, project, ColorJudge()).ask([red_track()], "red")

    assert verdicts == {"trk_red": "yes"}


def test_a_cache_file_for_another_question_is_not_trusted(describer: VideoDescriber) -> None:
    describer.ask([red_track()], "in red")
    path = describer.storage.cache_path(describer.sha256, KIND, describer.key("in red"))
    data = json.loads(path.read_text())
    path.write_text(json.dumps({**data, "description": "in blue"}))

    describer.ask([red_track()], "in red")

    assert len(describer.judge.asked) == 2


def test_a_cache_that_cant_be_read_is_an_error_not_a_no(describer: VideoDescriber) -> None:
    describer.ask([red_track()], "in red")
    path = describer.storage.cache_path(describer.sha256, KIND, describer.key("in red"))
    path.write_text("{not json")

    with pytest.raises(DescribeError):
        describer.ask([red_track()], "in red")


# --- several criteria ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "parts"),
    [
        ("in red", ["in red"]),
        ("in a grey t-shirt with a bag", ["in a grey t-shirt", "with a bag"]),
        ("In a grey t-shirt, and carrying a bag.", ["in a grey t-shirt", "carrying a bag"]),
        ("wearing a red shirt and a hat", ["wearing a red shirt", "a hat"]),
        ("dressed in red", ["dressed in red"]),
    ],
)
def test_a_description_splits_into_criteria(description: str, parts: list[str]) -> None:
    assert split_criteria(description) == parts


def test_criteria_combine_without_ever_guessing() -> None:
    assert combine(["yes", "yes"]) == "yes"
    assert combine(["yes", "no"]) == "no"
    assert combine(["unsure", "no"]) == "no"
    assert combine(["yes", "unsure"]) == "unsure"
    assert combine([]) == "unsure"


def still(track_id: str, label: str, where: tuple[int, int, int, int], n: int) -> Track:
    keys = [Keyframe(t=i / FPS, box=box(*where), conf=0.9, state="detected") for i in range(n)]
    return Track(track_id=track_id, label=label, source="detector", keyframes=keys)


def test_object_words_map_to_what_the_detector_found() -> None:
    labels = {"person", "handbag", "dog", "cell phone"}

    assert object_labels("with a bag", labels) == ("handbag", "backpack", "suitcase")
    assert object_labels("carrying bags", labels) == ("handbag", "backpack", "suitcase")
    assert object_labels("with a dog", labels) == ("dog",)
    assert object_labels("holding a phone", labels) == ("cell phone",)
    assert object_labels("with a red bag", labels) is None  # needs eyes: for the judge
    assert object_labels("with a giraffe", labels) is None
    assert object_labels("in red", labels) is None


def test_with_an_object_is_answered_from_the_tracks() -> None:
    long = OBJECT_MIN_SEEN
    carrier = still("trk_carrier", "person", (10, 10, 30, 50), long)
    alone = still("trk_alone", "person", (60, 10, 80, 50), long)
    glimpse = still("trk_glimpse", "person", (60, 10, 80, 50), long - 1)
    bag = still("trk_bag", "handbag", (20, 30, 28, 40), long)

    assert with_object(carrier, [bag]) == "yes"
    assert with_object(alone, [bag]) == "no"
    # Seen too briefly to say the detector didn't just miss the bag.
    assert with_object(glimpse, [bag]) == "unsure"


def test_an_object_seen_only_part_of_the_time_is_unsure() -> None:
    carrier = still("trk_carrier", "person", (10, 10, 30, 50), 20)
    bag = still("trk_bag", "handbag", (20, 30, 28, 40), 3)  # there for 3 of 20 keyframes

    assert with_object(carrier, [bag]) == "unsure"


def test_the_describer_combines_a_judged_part_and_an_object_part(square_clip: Path, tmp_path):
    store = Storage(tmp_path / "data")
    ref = store.add_video(square_clip)
    describer = VideoDescriber(store, ref.sha256, square_clip, ColorJudge())
    red = red_track("person")
    blue = blue_track("person")
    bag = red.model_copy(update={"track_id": "trk_bag", "label": "handbag"})
    scene = [red, blue, bag]

    each = describer.ask_each([red, blue], "in red with a bag", scene)

    assert each == {
        "in red": {"trk_red": "yes", "trk_blue": "no"},
        "with a bag": {"trk_red": "yes", "trk_blue": "no"},
    }
    assert describer.ask([red, blue], "in red with a bag", scene) == {
        "trk_red": "yes",
        "trk_blue": "no",
    }
    assert describer.ask([red, blue], "in blue with a bag", scene) == {
        "trk_red": "no",
        "trk_blue": "no",
    }


def test_an_object_nobody_looked_for_is_unsure_for_everyone(square_clip: Path, tmp_path) -> None:
    store = Storage(tmp_path / "data")
    ref = store.add_video(square_clip)
    describer = VideoDescriber(store, ref.sha256, square_clip, ColorJudge())
    people = [red_track("person"), blue_track("person")]

    # No bag track anywhere: maybe the detector never looked for bags. Not a "no".
    assert describer.ask(people, "with a bag", people) == {
        "trk_red": "unsure",
        "trk_blue": "unsure",
    }
    assert describer.ask(people, "with a bag") == {"trk_red": "unsure", "trk_blue": "unsure"}


def test_a_judge_that_reads_sentences_gets_the_whole_description(describer: VideoDescriber):
    describer.judge.__class__.compound = True
    try:
        describer.ask([red_track()], "in red with a bag", [red_track()])
    finally:
        describer.judge.__class__.compound = False

    assert describer.judge.asked == ["in red with a bag"]


def test_the_tool_and_the_real_describer_pick_the_red_person(square_clip: Path, tmp_path) -> None:
    store = Storage(tmp_path / "data")
    ref = store.add_video(square_clip)
    tracks = [red_track("person"), blue_track("person")]
    context = ToolContext(
        request_id="req_1",
        source="agent",
        tracks=tracks,
        describer=VideoDescriber(store, ref.sha256, square_clip, ColorJudge()),
    )

    found = call_tool("find_by_description", {"label": "person", "description": "in red"}, context)
    others = call_tool(
        "find_by_description",
        {"label": "person", "description": "in red", "exclude": True},
        context,
    )

    assert found.track_ids == ["trk_red"] and found.unsure == [] and found.left_out == 1
    assert others.track_ids == ["trk_blue"]
    with pytest.raises(ToolError, match="couldn't check"):
        missing = VideoDescriber(store, ref.sha256, tmp_path / "gone.mp4", ColorJudge())
        call_tool(
            "find_by_description",
            {"label": "person", "description": "in green"},
            ToolContext(request_id="req_2", source="agent", tracks=tracks, describer=missing),
        )


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
    ask = ["--data-dir", data, "describe", str(square_clip), "red", "--label", "thing"]

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
