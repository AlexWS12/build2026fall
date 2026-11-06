"""F-11: the mask audit on a real export. Clips come from PyAV, so none of this needs ffmpeg.

Keyframes sit off the 10 fps frame grid, so no frame lands on a stretch boundary, where the audit
and the renderer differ by one frame (see ``test_mask_report.py``).
"""

import dataclasses
import json
import sys
from pathlib import Path

import pytest
from mask_helpers import write_clip

from vibe.contract import AddMask, AddRule, Cut, Effect, Keyframe, OpLog, Rule, TimeRange, Track
from vibe.render import MaskAudit, UnsupportedEdit, audit_masks, export
from vibe.render.__main__ import main
from vibe.video import FrameIndex, VideoInfo, probe

FPS, FRAMES = 10, 40
END = FRAMES / FPS
BOX = (0.1, 0.1, 0.5, 0.5)


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_clip(tmp_path_factory.mktemp("masks") / "clip.mp4", FRAMES, FPS)


def track(*times: float, track_id: str = "trk_1") -> Track:
    keyframes = [Keyframe(t=t, box=BOX, conf=0.9, state="detected") for t in times]
    return Track(track_id=track_id, label="face", source="detector", keyframes=keyframes)


def masked(*ops: object) -> OpLog:
    log = OpLog()
    log.append(
        AddMask(request_id="r_1", source="agent", targets=["trk_1"], effect=Effect(type="blur"))
    )
    for op in ops:
        log.append(op)
    return log


def cut(start: float, end: float) -> Cut:
    return Cut(request_id="r_2", source="agent", time_range=TimeRange(start=start, end=end))


def kinds(audit: MaskAudit) -> list[tuple[str, float, float]]:
    return [(s.kind, s.start, s.end) for span in audit.spans for s in span.stretches]


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
