"""F-11 at export level: the mask audit on a real export, what the masks do to the exported
frames, and ``--project`` with the tracks cached for its video. Clips come from PyAV, so none of
this needs ffmpeg.

Keyframes sit off the 10 fps frame grid, so no frame lands on a stretch boundary, where the audit
and the renderer differ by one frame (see ``test_mask_report.py``).
"""

import dataclasses
import json
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from mask_helpers import HEIGHT, WIDTH, texture, write_clip

from vibe.contract import AddMask, AddRule, Cut, Effect, Keyframe, OpLog, Rule, TimeRange, Track
from vibe.modules.vision import cache
from vibe.render import MaskAudit, UnsupportedEdit, audit_masks, export, render_frame
from vibe.render.__main__ import main
from vibe.render.geometry import HOLD_SECONDS, MAX_GAP_SECONDS, UNCERTAIN_PADDING, Box, to_pixels
from vibe.storage import Project, Storage
from vibe.video import FrameIndex, VideoInfo, VideoReader, probe

FPS, FRAMES = 10, 40
END = FRAMES / FPS
BOX = (0.1, 0.1, 0.5, 0.5)
COVERING = [i / 2 for i in range(int(END * 2) + 1)]
"""Keyframe times close enough together, from the start to the end, that nothing is held or lost."""

MIDDLE = 1.55
"""A moment in the middle of the clip, while the frame from 1.5 s is on screen."""

# Parts of the 64 × 48 picture, as (rows, columns).
INSIDE = (slice(8, 20), slice(10, 28))  # well inside BOX
CLEAR = (slice(30, 48), slice(0, 30))  # the bottom left: outside BOX and its padding, and DRAWN
LOSSY = 6.0  # mean per-pixel difference H.264 at CRF 18 may add to an untouched region
BLACK = Effect(type="box", padding=0)  # shows exactly where a mask is


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_clip(tmp_path_factory.mktemp("masks") / "clip.mp4", FRAMES, FPS)


@pytest.fixture(scope="module")
def textured_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Detail everywhere and nothing dark, so every effect shows on it."""
    return write_clip(tmp_path_factory.mktemp("masks") / "textured.mp4", FRAMES, FPS, texture())


def keyframe(t: float, box: Box = BOX, state: str = "detected") -> Keyframe:
    return Keyframe(t=t, box=box, conf=0.9, state=state)


def track_of(*keyframes: Keyframe, track_id: str = "trk_1", source: str = "detector") -> Track:
    return Track(track_id=track_id, label="face", source=source, keyframes=list(keyframes))


def track(*times: float, track_id: str = "trk_1") -> Track:
    return track_of(*(keyframe(t) for t in times), track_id=track_id)


def mask(target: str = "trk_1", effect: Effect | None = None, request_id: str = "r_1") -> AddMask:
    effect = effect or Effect(type="blur")
    return AddMask(request_id=request_id, source="agent", targets=[target], effect=effect)


def masked(*ops: object, effect: Effect | None = None) -> OpLog:
    log = OpLog()
    log.append(mask(effect=effect))
    for op in ops:
        log.append(op)
    return log


def cut(start: float, end: float) -> Cut:
    return Cut(request_id="r_2", source="agent", time_range=TimeRange(start=start, end=end))


def kinds(audit: MaskAudit) -> list[tuple[str, float, float]]:
    return [(s.kind, s.start, s.end) for span in audit.spans for s in span.stretches]


def frame_at(path: Path, t: float) -> np.ndarray:
    with VideoReader(path) as reader:
        return reader.read_at(t).image


def frame_times(path: Path) -> list[float]:
    index = FrameIndex.build(path)
    return [index.time_of(i) for i in range(len(index))]


def frames_where(path: Path, test: Callable[[np.ndarray], bool]) -> list[float]:
    """The time of each frame of the clip that passes ``test``."""
    with VideoReader(path) as reader:
        return [frame.time for frame in reader.iter_frames() if test(frame.image)]


def difference(a: np.ndarray, b: np.ndarray) -> float:
    """Mean per-pixel difference between two pictures."""
    return float(np.abs(a.astype(np.float64) - b.astype(np.float64)).mean())


def detail(image: np.ndarray) -> float:
    """Mean difference between neighbouring pixels: high for the texture, low once it's hidden."""
    a = image.astype(np.float64)
    return float(np.abs(np.diff(a, axis=0)).mean() + np.abs(np.diff(a, axis=1)).mean())


def black(image: np.ndarray) -> bool:
    return bool(image.mean() < 5)  # give or take the encoder; the texture is 64 or brighter


def test_the_result_holds_what_audit_masks_gives_on_its_own(clip: Path, tmp_path: Path) -> None:
    state, tracks = masked().state(), [track(0.03, 0.53)]
    result = export(clip, state, tracks, tmp_path / "out.mp4")
    assert result.audit == audit_masks(state, tracks, duration=FrameIndex.build(clip).end)
    assert result.audit.spans


def test_tracks_given_as_a_generator_reach_the_audit(clip: Path, tmp_path: Path) -> None:
    state, tracks = masked().state(), [track(0.03, 0.53)]
    from_list = export(clip, state, tracks, tmp_path / "list.mp4")
    from_generator = export(clip, state, (t for t in tracks), tmp_path / "generator.mp4")
    assert from_generator.audit == from_list.audit
    assert from_generator.audit.totals["no_track"] == 0


def test_the_audit_ends_where_the_frames_end_not_where_the_header_says(
    clip: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def wrong_header(path: Path) -> VideoInfo:
        return dataclasses.replace(probe(path), duration=99.0)

    # sys.modules, because ``vibe.render.export`` as an attribute is the function.
    monkeypatch.setattr(sys.modules["vibe.render.export"], "probe", wrong_header)
    result = export(clip, masked().state(), [track(0.03, 0.53)], tmp_path / "out.mp4")
    assert kinds(result.audit)[-1][2] == END


def test_a_lost_target_across_a_cut_is_two_stretches_and_one_issue(
    clip: Path, tmp_path: Path
) -> None:
    state = masked(cut(2, 2.5)).state()
    result = export(clip, state, [track(0.03, 0.53)], tmp_path / "out.mp4")

    lost = [(start, end) for kind, start, end in kinds(result.audit) if kind == "lost"]
    assert lost == [(pytest.approx(1.03), 2), (2.5, END)]
    [issue] = result.issues
    assert issue.track_id == "trk_1"

    shown = [i / FPS for i in range(FRAMES) if not state.is_cut(i / FPS)]
    by_issue = [t for t in shown if issue.first_seen <= t <= issue.last_seen]
    by_audit = [t for t in shown if any(start <= t < end for start, end in lost)]
    assert by_audit == by_issue
    assert by_audit


def test_a_mask_with_no_track_is_no_track_for_the_whole_clip(clip: Path, tmp_path: Path) -> None:
    result = export(clip, masked().state(), [], tmp_path / "out.mp4")
    assert kinds(result.audit) == [("no_track", 0, END)]
    assert not result.audit.clean
    assert [i.track_id for i in result.issues] == ["trk_1"]


def test_a_covered_track_is_clean_with_no_issues(clip: Path, tmp_path: Path) -> None:
    times = [i / 2 for i in range(int(END * 2) + 1)]
    result = export(clip, masked().state(), [track(*times)], tmp_path / "out.mp4")
    assert kinds(result.audit) == [("covered", 0, END)]
    assert result.audit.clean
    assert result.issues == ()


def test_a_standing_rule_is_still_refused_and_writes_nothing(clip: Path, tmp_path: Path) -> None:
    rule = Rule(rule_id="rule_1", category="person", effect=Effect(type="blur"))
    log = masked(AddRule(request_id="r_2", source="agent", rule=rule))
    with pytest.raises(UnsupportedEdit):
        export(clip, log.state(), [track(0, 1)], tmp_path / "out.mp4")
    assert list(tmp_path.iterdir()) == []


def test_the_cli_prints_the_audit(
    capsys: pytest.CaptureFixture[str], clip: Path, tmp_path: Path
) -> None:
    timeline = tmp_path / "timeline.jsonl"
    timeline.write_text(masked().to_jsonl(), encoding="utf-8")
    tracks = tmp_path / "tracks.json"
    tracks.write_text(f"[{track(0.03, 0.53).model_dump_json()}]", encoding="utf-8")

    code = main(
        ["export", str(clip), "--log", str(timeline), "--tracks", str(tracks),
         "-o", str(tmp_path / "out.mp4")]
    )  # fmt: skip
    out, err = capsys.readouterr()
    assert code == 0, err
    audit = json.loads(out)["audit"]
    assert audit["clean"] is False
    assert audit["totals"]["lost"] == pytest.approx(END - 1.03)
    assert audit["spans"][0]["effect"] == {"type": "blur", "padding": 0.15, "strength": None}
    assert audit["spans"][0]["stretches"][0] == {"kind": "held", "start": 0, "end": 0.03}


def test_to_json_survives_json_dumps_with_rules_and_no_video() -> None:
    rule = Rule(rule_id="rule_1", category="person", effect=Effect(type="box"))
    log = masked(AddRule(request_id="r_2", source="agent", rule=rule))
    audit = audit_masks(log.state(), [track(0, 0.5, 1)], duration=1)
    data = json.loads(json.dumps(audit.to_json()))
    assert data["rules_not_audited"] == ["rule_1"]
    assert data["clean"] is False  # the rule went unchecked
    assert data["totals"]["covered"] == 1
    assert data["spans"][0]["op_id"] == audit.spans[0].op_id


# --- What the masks do to the exported frames ----------------------------------------------


def test_pixelate_in_an_export_is_the_per_frame_paths_pixelate(
    textured_clip: Path, tmp_path: Path
) -> None:
    state, tracks = masked(effect=Effect(type="pixelate")).state(), [track(*COVERING)]
    result = export(textured_clip, state, tracks, tmp_path / "out.mp4")
    out, src = frame_at(result.path, MIDDLE), frame_at(textured_clip, MIDDLE)

    pixelated = render_frame(src, MIDDLE, state, tracks)
    blurred = render_frame(src, MIDDLE, masked().state(), tracks)
    assert difference(out, pixelated) < LOSSY  # the frame the per-frame path makes...
    assert difference(out[INSIDE], blurred[INSIDE]) > LOSSY  # ...in blocks, which a blur isn't
    assert detail(out[INSIDE]) < 0.5 * detail(src[INSIDE])  # the detail is gone under the mask
    assert difference(out[CLEAR], src[CLEAR]) < LOSSY  # and the rest is untouched
    assert [span.effect.type for span in result.audit.spans] == ["pixelate"]
    assert result.audit.clean
    assert result.issues == ()


LEFT, RIGHT = (0.05, 0.3, 0.25, 0.6), (0.75, 0.3, 0.95, 0.6)
BETWEEN = (slice(18, 26), slice(22, 42))  # in neither, however much a held box grows
BELOW = (slice(38, 48), slice(0, WIDTH))  # under both, and under a box that covers both


def test_a_long_gap_is_masked_from_one_keyframe_to_the_other_and_lost_in_between(
    textured_clip: Path, tmp_path: Path
) -> None:
    first, second = 0.03, 3.53  # each held to its end of the clip, so only the gap is lost
    assert second - first > MAX_GAP_SECONDS
    target = track_of(keyframe(first, LEFT), keyframe(second, RIGHT))
    result = export(textured_clip, masked(effect=BLACK).state(), [target], tmp_path / "out.mp4")

    lost_from, lost_to = first + HOLD_SECONDS, second - HOLD_SECONDS
    assert kinds(result.audit) == [
        ("held", 0, first),
        ("gap", first, pytest.approx(lost_from)),
        ("lost", pytest.approx(lost_from), pytest.approx(lost_to)),
        ("gap", pytest.approx(lost_to), second),
        ("held", second, END),
    ]
    assert not result.audit.clean

    # Nothing saw the target between the two keyframes, so every frame there hides both boxes
    # and the space between them. A box moving in a straight line would still be at LEFT just
    # after the first keyframe, with that space showing.
    shown = frame_times(textured_clip)
    hidden = frames_where(result.path, lambda image: black(image[BETWEEN]))
    assert hidden == pytest.approx([t for t in shown if first <= t < second])
    out, src = frame_at(result.path, MIDDLE), frame_at(textured_clip, MIDDLE)
    assert difference(out[BELOW], src[BELOW]) < LOSSY  # but not the whole frame

    [issue] = result.issues  # the renderer reports the lost stretch on the same frames
    by_issue = [t for t in shown if issue.first_seen <= t <= issue.last_seen]
    assert by_issue == [t for t in shown if lost_from <= t < lost_to]
    assert by_issue


WIDE = (0.2, 0.2, 0.8, 0.8)  # big enough that its extra padding is a few pixels on every side


def test_an_uncertain_keyframe_widens_the_mask_next_to_it_and_is_audited_not_reported(
    textured_clip: Path, tmp_path: Path
) -> None:
    times = [0.03 + i / 2 for i in range(8)]  # 0.03, 0.53 ... 3.53
    before, unsure, after = times[2:5]
    target = track_of(
        *(keyframe(t, WIDE, "uncertain" if t == unsure else "detected") for t in times)
    )
    result = export(textured_clip, masked(effect=BLACK).state(), [target], tmp_path / "out.mp4")

    assert kinds(result.audit) == [
        ("held", 0, times[0]),
        ("covered", times[0], before),
        ("uncertain", before, after),
        ("covered", after, times[-1]),
        ("held", times[-1], END),
    ]
    # Listed for review, but the target is still inside its box: nothing for the renderer to
    # work around.
    assert result.audit.totals["uncertain"] == pytest.approx(after - before)
    assert result.audit.clean
    assert result.issues == ()

    # The extra padding is a ring around the box: masked on the frames next to the uncertain
    # keyframe, and showing the picture on every other frame.
    x1, y1, x2, y2 = to_pixels(WIDE, 0, WIDTH, HEIGHT)
    px1, py1, px2, py2 = to_pixels(WIDE, UNCERTAIN_PADDING, WIDTH, HEIGHT)
    box = np.zeros((HEIGHT, WIDTH), dtype=bool)
    box[y1:y2, x1:x2] = True
    ring = np.zeros((HEIGHT, WIDTH), dtype=bool)
    ring[py1:py2, px1:px2] = True
    ring &= ~box
    assert ring.sum() > 400  # a real band of pixels, not a rounding sliver

    shown = frame_times(textured_clip)
    widened = frames_where(result.path, lambda image: black(image[ring]))
    assert widened == pytest.approx([t for t in shown if before <= t < after])
    assert frames_where(result.path, lambda image: black(image[box])) == pytest.approx(shown)
    out, src = frame_at(result.path, 2.55), frame_at(textured_clip, 2.55)  # a covered frame
    assert difference(out[ring], src[ring]) < LOSSY


# --- ``--project``: the tracks cached for the project's video (F-10) ------------------------

KEY, OTHER = "stub-k1-aaaa1111", "stub-k2-bbbb2222"  # two analyses of one video
DRAWN = (0.6, 0.5, 0.9, 0.9)
DRAWN_INSIDE = (slice(28, 40), slice(42, 54))  # well inside DRAWN


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Storage:
    """An empty data folder, which the CLI finds through ``VIBE_DATA_DIR``."""
    monkeypatch.setenv("VIBE_DATA_DIR", str(tmp_path / "data"))
    return Storage(tmp_path / "data")


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def analyse(storage: Storage, project: Project, key: str) -> str:
    """Cache one track covering BOX for the whole clip, the way an analysis under ``key`` would.
    Returns the track's id, which carries the key's hash."""
    [cached] = cache.save_tracks(storage, project.info.video.sha256, key, [track(*COVERING)])
    return cached.track_id


def draw(project: Project) -> str:
    """Save one hand-drawn track covering DRAWN for the whole clip. Returns its id."""
    drawn = track_of(
        *(keyframe(t, DRAWN, "user") for t in COVERING), track_id="trk_drawn", source="user"
    )
    project.save_user_tracks([drawn])
    return drawn.track_id


def rendered(capsys: pytest.CaptureFixture[str], project: Project, *argv: str) -> dict:
    """Export the project and return the CLI's summary."""
    code, out, err = run(capsys, "export", "--project", project.id, *argv)
    assert code == 0, err
    return json.loads(out)


def refused(capsys: pytest.CaptureFixture[str], project: Project, *argv: str) -> str:
    """Try an export that must fail with a message, not a traceback, and write nothing.
    Returns the message."""
    code, out, err = run(capsys, "export", "--project", project.id, *argv)
    assert code == 1
    assert err.startswith("error: ")
    assert out == ""
    assert list(project.exports_dir.iterdir()) == []
    return err


def test_a_project_export_masks_the_cached_tracks_box_not_the_whole_frame(
    capsys: pytest.CaptureFixture[str], storage: Storage, textured_clip: Path
) -> None:
    project = storage.create_project(textured_clip)
    project.append(mask(analyse(storage, project, KEY), BLACK))

    summary = rendered(capsys, project)
    assert summary["issues"] == []
    assert summary["audit"]["totals"]["no_track"] == 0
    assert summary["audit"]["totals"]["covered"] == END
    assert summary["audit"]["clean"] is True
    out, src = frame_at(Path(summary["path"]), MIDDLE), frame_at(textured_clip, MIDDLE)
    assert black(out[INSIDE])  # the detector's box is hidden...
    # ...and nothing else: without the cached tracks the mask found no box and hid everything.
    assert difference(out[CLEAR], src[CLEAR]) < LOSSY


def test_a_projects_hand_drawn_tracks_are_used_along_with_the_cached_ones(
    capsys: pytest.CaptureFixture[str], storage: Storage, textured_clip: Path
) -> None:
    project = storage.create_project(textured_clip)
    project.append(mask(analyse(storage, project, KEY), BLACK))
    project.append(mask(draw(project), BLACK, request_id="r_2"))

    summary = rendered(capsys, project)
    assert summary["audit"]["clean"] is True
    out, src = frame_at(Path(summary["path"]), MIDDLE), frame_at(textured_clip, MIDDLE)
    assert black(out[INSIDE])
    assert black(out[DRAWN_INSIDE])
    assert difference(out[CLEAR], src[CLEAR]) < LOSSY


def test_an_unanalysed_project_renders_with_its_hand_drawn_tracks(
    capsys: pytest.CaptureFixture[str], storage: Storage, textured_clip: Path
) -> None:
    project = storage.create_project(textured_clip)
    project.append(mask(draw(project), BLACK))

    summary = rendered(capsys, project)
    assert summary["issues"] == []
    assert summary["audit"]["clean"] is True
    out, src = frame_at(Path(summary["path"]), MIDDLE), frame_at(textured_clip, MIDDLE)
    assert black(out[DRAWN_INSIDE])
    assert difference(out[CLEAR], src[CLEAR]) < LOSSY


def test_an_unanalysed_project_hides_the_whole_frame_for_a_mask_with_no_track(
    capsys: pytest.CaptureFixture[str], storage: Storage, textured_clip: Path
) -> None:
    project = storage.create_project(textured_clip)
    draw(project)  # boxes drawn by hand don't stand in for the analysis
    project.append(mask("trk_aaaa1111_1", BLACK))

    summary = rendered(capsys, project)  # it still renders: privacy fails closed, not open
    assert [issue["track_id"] for issue in summary["issues"]] == ["trk_aaaa1111_1"]
    assert summary["audit"]["totals"]["no_track"] == END
    assert summary["audit"]["clean"] is False
    assert black(frame_at(Path(summary["path"]), MIDDLE))


def test_several_cached_track_sets_and_no_default_is_an_error_that_names_them(
    capsys: pytest.CaptureFixture[str], storage: Storage, textured_clip: Path
) -> None:
    project = storage.create_project(textured_clip)
    project.append(mask(analyse(storage, project, KEY), BLACK))
    analyse(storage, project, OTHER)

    message = refused(capsys, project)
    assert KEY in message
    assert OTHER in message


@pytest.mark.parametrize(("key", "no_track"), [(KEY, 0), (OTHER, END)])
def test_analysis_picks_one_of_several_cached_track_sets(
    capsys: pytest.CaptureFixture[str],
    storage: Storage,
    textured_clip: Path,
    key: str,
    no_track: float,
) -> None:
    project = storage.create_project(textured_clip)
    project.append(mask(analyse(storage, project, KEY), BLACK))  # the mask names KEY's track
    analyse(storage, project, OTHER)

    summary = rendered(capsys, project, "--analysis", key)
    assert summary["audit"]["totals"]["no_track"] == no_track
    out, src = frame_at(Path(summary["path"]), MIDDLE), frame_at(textured_clip, MIDDLE)
    assert black(out[INSIDE])
    if key == KEY:
        assert difference(out[CLEAR], src[CLEAR]) < LOSSY
    else:
        # The other analysis has no track with that id, so the target could be anywhere.
        assert black(out)


def test_the_videos_default_track_set_is_used_without_asking_for_it(
    capsys: pytest.CaptureFixture[str], storage: Storage, textured_clip: Path
) -> None:
    project = storage.create_project(textured_clip)
    project.append(mask(analyse(storage, project, KEY), BLACK))
    analyse(storage, project, OTHER)
    cache.set_default_tracks(storage, project.info.video.sha256, KEY)

    summary = rendered(capsys, project)
    assert summary["audit"]["totals"]["no_track"] == 0
    out, src = frame_at(Path(summary["path"]), MIDDLE), frame_at(textured_clip, MIDDLE)
    assert black(out[INSIDE])
    assert difference(out[CLEAR], src[CLEAR]) < LOSSY


def test_an_analysis_key_that_isnt_cached_is_an_error_that_lists_what_is(
    capsys: pytest.CaptureFixture[str], storage: Storage, textured_clip: Path
) -> None:
    project = storage.create_project(textured_clip)
    assert "isn't analysed" in refused(capsys, project, "--analysis", KEY)

    analyse(storage, project, KEY)
    message = refused(capsys, project, "--analysis", OTHER)
    assert f"no cached tracks {OTHER!r}" in message
    assert KEY in message


def test_an_analysis_key_that_cant_be_a_cache_key_is_an_error(
    capsys: pytest.CaptureFixture[str], storage: Storage, textured_clip: Path
) -> None:
    project = storage.create_project(textured_clip)
    assert "invalid cache key" in refused(capsys, project, "--analysis", "../tracks")


def test_a_default_whose_tracks_are_gone_is_an_error_not_an_unanalysed_video(
    capsys: pytest.CaptureFixture[str], storage: Storage, textured_clip: Path
) -> None:
    project = storage.create_project(textured_clip)
    sha256 = project.info.video.sha256
    project.append(mask(analyse(storage, project, KEY), BLACK))
    cache.set_default_tracks(storage, sha256, KEY)
    storage.cache_path(sha256, "tracks", KEY).unlink()

    assert "no longer cached" in refused(capsys, project)


def test_analysis_without_a_project_is_an_error(
    capsys: pytest.CaptureFixture[str], textured_clip: Path, tmp_path: Path
) -> None:
    timeline = tmp_path / "timeline.jsonl"
    timeline.write_text(masked().to_jsonl(), encoding="utf-8")
    out_path = tmp_path / "out.mp4"

    code, out, err = run(
        capsys, "export", str(textured_clip), "--log", str(timeline), "--analysis", KEY,
        "-o", str(out_path),
    )  # fmt: skip
    assert code == 1
    assert err.startswith("error: ") and "--project" in err
    assert out == ""
    assert not out_path.exists()
