"""F-13 / D-020: the measurement script. No GPU, no network, no real clips: a synthetic clip is
registered in a temporary manifest, and fake judges say what the test needs them to."""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from vision_helpers import FPS, HEIGHT, WIDTH

from vibe.benchmark.labels import ClipLabels, labels_path
from vibe.benchmark.manifest import (
    BenchmarkError,
    ClipManifest,
    add_clip,
    load_manifest,
    save_manifest,
)
from vibe.contract import Keyframe, Track
from vibe.modules.vision.describe import MIN_CROPS, ColorJudge, Judge
from vibe.modules.vision.measure import (
    TimedJudge,
    check_where,
    format_report,
    load_truth,
    main,
    measure,
    resolve,
)
from vibe.modules.vision.vlm import VlmJudge
from vibe.storage import Storage

# --- a tiny world: one clip, two labelled people, a truth file ------------------------------


def person(track_id: str, box: tuple[int, int, int, int], drift: int = 0) -> Track:
    keys = [
        Keyframe(
            t=i / FPS,
            box=(
                (box[0] + drift * i) / WIDTH,
                box[1] / HEIGHT,
                (box[2] + drift * i) / WIDTH,
                box[3] / HEIGHT,
            ),
            conf=1.0,
            state="user",
        )
        for i in range(10)
    ]
    return Track(track_id=track_id, label="person", source="user", keyframes=keys)


@pytest.fixture
def make_world(tmp_path: Path, square_clip: Path):
    """``make_world(consent_cloud=False)``: storage, manifest and labels for the square clip,
    where ``red`` follows the red square and ``blue`` is the still blue one."""

    def build(consent_cloud: bool = False) -> SimpleNamespace:
        storage = Storage(tmp_path / "data")
        manifest_path = tmp_path / "clips.json"
        save_manifest(ClipManifest(), manifest_path)
        entry, _ = add_clip(
            square_clip,
            manifest_path=manifest_path,
            storage=storage,
            clip_id="squares",
            source="made by the tests",
            consent="synthetic",
            consent_cloud=consent_cloud,
        )
        labels_dir = tmp_path / "labels"
        labels_dir.mkdir(exist_ok=True)
        labels = ClipLabels(
            clip_id="squares",
            sha256=entry.video.sha256,
            tracks=[person("red", (4, 10, 24, 30), drift=4), person("blue", (76, 44, 88, 56))],
        )
        labels_path("squares", labels_dir).write_text(labels.model_dump_json(), encoding="utf-8")
        return SimpleNamespace(
            storage=storage,
            manifest=load_manifest(manifest_path),
            labels_dir=labels_dir,
            tmp=tmp_path,
        )

    return build


def write_truth(world, cases: list[dict], name: str = "truth.json") -> Path:
    path = world.tmp / name
    path.write_text(json.dumps({"truth_version": 1, "cases": cases}), encoding="utf-8")
    return path


def case(description: str = "in red", matches=("red",), ambiguous=(), clip_id="squares") -> dict:
    return {
        "clip_id": clip_id,
        "description": description,
        "matches": list(matches),
        "ambiguous": list(ambiguous),
    }


class Says(Judge):
    """Gives the same verdict to everything and counts how often it was asked."""

    name = "says"

    def __init__(self, verdict: str) -> None:
        super().__init__(verdict=verdict)
        self.verdict, self.calls = verdict, 0

    def judge(self, crops, label, description):
        self.calls += 1
        return self.verdict


def run(world, cases, judge, runs_on="this-machine", samples=3) -> dict:
    resolved = resolve(
        load_truth(write_truth(world, cases)), world.manifest, world.storage, world.labels_dir
    )
    check_where(resolved, judge, runs_on)
    return measure(resolved, judge, samples=samples)


# --- counting the three outcomes apart ---------------------------------------------------


def test_a_judge_that_is_right_has_no_dangerous_errors(make_world) -> None:
    data = run(make_world(), [case()], ColorJudge())  # the colour stub: red square, blue square

    totals = data["totals"]
    assert (totals["right"], totals["wrong_no"], totals["wrong_yes"]) == (2, 0, 0)
    assert totals["yes_yes"] == 1 and totals["no_no"] == 1
    assert data["leaks"] == []


def test_a_no_on_someone_who_matches_is_named(make_world) -> None:
    data = run(make_world(), [case()], Says("no"))

    assert data["totals"]["wrong_no"] == 1 and data["totals"]["no_no"] == 1
    assert data["leaks"] == [
        {"kind": "wrong_no", "clip": "squares", "description": "in red", "track": "red"}
    ]
    assert "WRONG NO .... " in format_report(data)


def test_a_yes_on_someone_who_doesnt_match_is_named(make_world) -> None:
    data = run(make_world(), [case()], Says("yes"))

    assert data["totals"]["wrong_yes"] == 1 and data["totals"]["yes_yes"] == 1
    assert data["leaks"] == [
        {"kind": "wrong_yes", "clip": "squares", "description": "in red", "track": "blue"}
    ]


def test_unsure_is_its_own_count_and_never_a_leak(make_world) -> None:
    data = run(make_world(), [case()], Says("unsure"))

    totals = data["totals"]
    assert (totals["unsure_on_match"], totals["unsure_on_non_match"]) == (1, 1)
    assert (totals["right"], totals["wrong_no"], totals["wrong_yes"]) == (0, 0, 0)
    assert data["leaks"] == []


def test_ambiguous_tracks_are_run_but_not_scored(make_world) -> None:
    judge = Says("no")
    data = run(make_world(), [case(ambiguous=["blue"])], judge)

    assert judge.calls == 2  # both were asked
    assert data["totals"]["scored"] == 1 and data["totals"]["ambiguous"] == 1
    assert data["totals"]["no_no"] == 0  # the ambiguous no isn't counted as right


def test_every_case_has_its_own_counts(make_world) -> None:
    data = run(make_world(), [case("in red"), case("in blue", matches=["blue"])], Says("yes"))

    assert [c["description"] for c in data["cases"]] == ["in red", "in blue"]
    assert [c["totals"]["wrong_yes"] for c in data["cases"]] == [1, 1]
    assert data["totals"]["wrong_yes"] == 2


# --- time -----------------------------------------------------------------------------------


def test_only_calls_that_really_ask_are_timed() -> None:
    timed = TimedJudge(Says("yes"))
    crop = np.zeros((8, 8, 3), dtype=np.uint8)

    timed.judge([crop] * (MIN_CROPS - 1), "person", "in red")
    timed.judge([crop] * MIN_CROPS, "person", "in red")

    assert timed.seconds[0] is None and timed.seconds[1] >= 0


def test_the_report_gives_seconds_over_the_asked_tracks(make_world) -> None:
    data = run(make_world(), [case()], Says("yes"))

    seconds = data["totals"]["seconds_per_track"]
    assert seconds["asked"] == 2 and seconds["max"] >= seconds["median"] >= 0


# --- where the crops may go -----------------------------------------------------------------


def resolved_for(world):
    return resolve(
        load_truth(write_truth(world, [case()])), world.manifest, world.storage, world.labels_dir
    )


def test_a_third_party_never_gets_crops_from_a_clip_without_cloud_consent(make_world) -> None:
    world = make_world(consent_cloud=False)
    judge = Says("yes")

    with pytest.raises(BenchmarkError, match="consent_cloud.*squares"):
        check_where(resolved_for(world), judge, "third-party")
    assert judge.calls == 0


def test_a_third_party_may_get_crops_when_the_consent_says_so(make_world) -> None:
    world = make_world(consent_cloud=True)

    check_where(resolved_for(world), Says("yes"), "third-party")  # no error


def test_the_team_s_own_machines_need_no_cloud_consent(make_world) -> None:
    world = make_world(consent_cloud=False)

    check_where(resolved_for(world), Says("yes"), "team-machine")
    check_where(resolved_for(world), Says("yes"), "this-machine")


def test_a_university_cluster_the_team_approved_is_not_gated_but_is_recorded(
    make_world, capsys
) -> None:
    """HiPerGator: the team's approval is theirs to give, so no consent_cloud check; but the
    report says where the crops went, never "team-machine" or "this-machine"."""
    world = make_world(consent_cloud=False)
    judge = Says("yes")

    check_where(resolved_for(world), judge, "university")  # no error
    code = main(
        argv(world, runs_on="university"),
        storage=world.storage,
        manifest=world.manifest,
        labels_dir=world.labels_dir,
        judge=judge,
    )

    assert code == 0 and judge.calls == 2
    assert "runs on: university" in capsys.readouterr().out


def test_the_colour_stub_sends_nothing_anywhere(make_world) -> None:
    check_where(resolved_for(make_world(consent_cloud=False)), ColorJudge(), "third-party")


def test_this_machine_means_a_loopback_judge(make_world) -> None:
    resolved = resolved_for(make_world())

    check_where(resolved, VlmJudge("m", "http://localhost:11434"), "this-machine")
    with pytest.raises(BenchmarkError, match="alex-5070"):
        check_where(resolved, VlmJudge("m", "http://alex-5070:11434"), "this-machine")


# --- problems with the inputs, all at once ----------------------------------------------------


def test_every_problem_in_the_truth_file_is_reported_together(make_world) -> None:
    world = make_world()
    cases = [
        case(clip_id="nope"),
        case("in red", matches=["ghost"]),
        case("in blue", matches=["blue"], ambiguous=["blue"]),
    ]
    truth = load_truth(write_truth(world, cases))

    with pytest.raises(BenchmarkError) as error:
        resolve(truth, world.manifest, world.storage, world.labels_dir)

    message = str(error.value)
    assert (
        "no such clip" in message and "ghost" in message and "both a match and ambiguous" in message
    )


def test_a_clip_without_labels_is_refused(make_world) -> None:
    world = make_world()
    labels_path("squares", world.labels_dir).unlink()
    truth = load_truth(write_truth(world, [case()]))

    with pytest.raises(BenchmarkError, match="no labels"):
        resolve(truth, world.manifest, world.storage, world.labels_dir)


def test_a_missing_video_is_refused_with_what_to_do(make_world) -> None:
    world = make_world()
    video = world.storage.video_path(world.manifest.clips[0].video)
    video.unlink()
    truth = load_truth(write_truth(world, [case()]))

    with pytest.raises(BenchmarkError, match="missing"):
        resolve(truth, world.manifest, world.storage, world.labels_dir)


@pytest.mark.parametrize("content", ["not json", '{"truth_version": 9, "cases": []}', "[]"])
def test_a_truth_file_that_cant_be_read_is_refused(tmp_path: Path, content: str) -> None:
    path = tmp_path / "t.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(BenchmarkError):
        load_truth(path)


# --- the command line -----------------------------------------------------------------------


def argv(world, *extra: str, runs_on: str = "this-machine") -> list[str]:
    truth = write_truth(world, [case()])
    return [str(truth), "--hardware", "a test box", "--runs-on", runs_on, *extra]


def test_the_command_prints_the_report_and_writes_the_data(make_world, capsys) -> None:
    world = make_world()
    out = world.tmp / "out.json"

    code = main(
        argv(world, "--json", str(out)),
        storage=world.storage,
        manifest=world.manifest,
        labels_dir=world.labels_dir,
        judge=ColorJudge(),
    )

    shown = capsys.readouterr()
    assert code == 0
    assert "WRONG NO" in shown.out and "a test box" in shown.out and "this-machine" in shown.out
    assert "[1/1] squares | in red" in shown.err  # progress goes to stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["hardware"] == "a test box" and data["runs_on"] == "this-machine"
    assert data["totals"]["right"] == 2


def test_the_command_refuses_before_asking_when_consent_doesnt_cover_it(make_world, capsys) -> None:
    world = make_world(consent_cloud=False)
    judge = Says("yes")

    code = main(
        argv(world, runs_on="third-party"),
        storage=world.storage,
        manifest=world.manifest,
        labels_dir=world.labels_dir,
        judge=judge,
    )

    assert code == 2 and judge.calls == 0
    assert "consent_cloud" in capsys.readouterr().err


def test_a_judge_that_cant_answer_stops_the_run_with_an_error(make_world, capsys) -> None:
    from vibe.modules.vision.describe import DescribeError

    class Down(Judge):
        name = "down"

        def judge(self, crops, label, description):
            raise DescribeError("the server is down")

    world = make_world()
    code = main(
        argv(world),
        storage=world.storage,
        manifest=world.manifest,
        labels_dir=world.labels_dir,
        judge=Down(),
    )

    shown = capsys.readouterr()
    assert code == 2 and "the server is down" in shown.err
    assert "TOTAL" not in shown.out  # an error is never reported as a result


@pytest.mark.parametrize("missing", ["--hardware", "--runs-on"])
def test_hardware_and_where_it_runs_are_required(make_world, missing: str) -> None:
    world = make_world()
    args = argv(world)
    at = args.index(missing)
    del args[at : at + 2]

    with pytest.raises(SystemExit) as exit_:
        main(args, storage=world.storage, manifest=world.manifest, labels_dir=world.labels_dir)

    assert exit_.value.code == 2
