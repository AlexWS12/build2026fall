"""F-02: the edit timeline contract. Runs without a GPU, video files or network."""

import json

import pytest
from pydantic import ValidationError

from vibe.contract import (
    OP_ADAPTER,
    SCHEMA_VERSION,
    AddAudioFilter,
    AddMask,
    AddRule,
    ContractError,
    Cut,
    Effect,
    Mute,
    OpLog,
    Redo,
    RemoveAudioFilter,
    RemoveMask,
    RemoveRule,
    Rule,
    SetEffect,
    TimelineState,
    TimeRange,
    Track,
    Uncut,
    Undo,
    Unmute,
    fold,
    history,
)
from vibe.contract.schema import example_logs, example_tracks, stale_files

BLUR = Effect(type="blur")
PIXELATE = Effect(type="pixelate")


def tr(start: float, end: float | None = None) -> TimeRange:
    return TimeRange(start=start, end=end)


def mask(rid: str, targets: list[str], effect: Effect = BLUR, r: TimeRange | None = None):
    return AddMask(request_id=rid, source="agent", targets=targets, effect=effect, time_range=r)


def spans(state: TimelineState, track: str) -> list[tuple[float, float | None, str]]:
    return [(s.start, s.end, s.effect.type) for s in state.masks.get(track, [])]


# --- Validation: malformed ops fail loudly -----------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"start": 5, "end": 5},
        {"start": 5, "end": 2},
        {"start": -1, "end": 2},
    ],
)
def test_time_range_rejects_empty_or_negative(bad: dict) -> None:
    with pytest.raises(ValidationError):
        TimeRange(**bad)


def test_time_range_end_null_means_until_the_end() -> None:
    assert TimeRange(start=30).end is None


@pytest.mark.parametrize(
    "op",
    [
        {
            "kind": "add_mask",
            "request_id": "r",
            "source": "agent",
            "targets": ["t"],
            "effect": {"type": "blurr"},
        },  # typo in the effect
        {
            "kind": "add_mask",
            "request_id": "r",
            "source": "agent",
            "targets": ["t"],
            "effect": {"type": "blur"},
            "colour": "red",
        },  # unknown field
        {
            "kind": "add_mask",
            "request_id": "r",
            "source": "agent",
            "targets": [],
            "effect": {"type": "blur"},
        },  # no targets
        {"kind": "explode", "request_id": "r", "source": "agent"},  # unknown kind
        {"kind": "cut", "request_id": "r", "source": "agent"},  # cut needs a time range
        {"kind": "cut", "request_id": "r", "source": "robot", "time_range": {"start": 0}},
    ],
)
def test_malformed_ops_are_rejected(op: dict) -> None:
    with pytest.raises(ValidationError):
        OP_ADAPTER.validate_python(op)


def test_ops_are_immutable() -> None:
    op = mask("r_1", ["t"])
    with pytest.raises(ValidationError):
        op.targets = ["other"]  # type: ignore[misc]


# --- Masks: later ops win over their own range --------------------------------------------


def test_mask_without_time_range_covers_the_whole_clip() -> None:
    assert spans(fold([mask("r_1", ["t"])]), "t") == [(0.0, None, "blur")]


def test_later_mask_wins_over_its_range() -> None:
    state = fold([mask("r_1", ["t"], BLUR, tr(0, 30)), mask("r_2", ["t"], PIXELATE, tr(10, 20))])
    assert spans(state, "t") == [(0, 10, "blur"), (10, 20, "pixelate"), (20, 30, "blur")]


def test_remove_mask_cuts_a_hole() -> None:
    ops = [
        mask("r_1", ["t"]),
        RemoveMask(request_id="r_2", source="user", targets=["t"], time_range=tr(5, 8)),
    ]
    assert spans(fold(ops), "t") == [(0, 5, "blur"), (8, None, "blur")]


def test_remove_mask_everywhere_drops_the_target() -> None:
    ops = [mask("r_1", ["t"]), RemoveMask(request_id="r_2", source="user", targets=["t"])]
    assert fold(ops).masks == {}


def test_set_effect_only_changes_masked_parts() -> None:
    ops = [
        mask("r_1", ["t"], BLUR, tr(0, 10)),
        SetEffect(request_id="r_2", source="user", targets=["t"], effect=PIXELATE),
    ]
    assert spans(fold(ops), "t") == [(0, 10, "pixelate")]


def test_spans_remember_which_op_made_them() -> None:
    first = mask("r_1", ["t"], BLUR, tr(0, 30))
    second = mask("r_2", ["t"], PIXELATE, tr(10, 20))
    middle = fold([first, second]).masks["t"][1]
    assert (middle.op_id, middle.request_id) == (second.op_id, "r_2")


def test_masks_at_answers_per_frame() -> None:
    state = fold([mask("r_1", ["a"], BLUR, tr(0, 10)), mask("r_2", ["b"], BLUR, tr(5))])
    assert set(state.masks_at(7)) == {"a", "b"}
    assert set(state.masks_at(10)) == {"b"}  # end is excluded
    assert set(state.masks_at(2)) == {"a"}


# --- Cuts and the source <-> output time mapping ------------------------------------------


def test_cuts_merge_and_uncut_restores() -> None:
    ops = [
        Cut(request_id="r_1", source="agent", time_range=tr(0, 5)),
        Cut(request_id="r_2", source="agent", time_range=tr(3, 8)),
        Uncut(request_id="r_3", source="user", time_range=tr(6, 7)),
    ]
    assert [(c.start, c.end) for c in fold(ops).cuts] == [(0, 6), (7, 8)]


def test_time_mapping_with_cuts() -> None:
    state = fold(
        [
            Cut(request_id="r_1", source="agent", time_range=tr(0, 10)),
            Cut(request_id="r_2", source="agent", time_range=tr(20, 25)),
        ]
    )
    assert state.source_to_output(15) == 5
    assert state.source_to_output(30) == 15
    assert state.source_to_output(22) == 10  # inside a cut: lands at the cut point
    assert state.output_to_source(5) == 15
    assert state.output_to_source(10) == 25  # at a cut point: the frame after the cut
    assert state.output_to_source(0) == 10
    assert state.is_cut(22) and not state.is_cut(25)


def test_open_ended_cut_ends_the_output() -> None:
    state = fold([Cut(request_id="r_1", source="agent", time_range=tr(50))])
    assert state.source_to_output(70) == 50
    with pytest.raises(ValueError, match="past the end"):
        state.output_to_source(50)


def test_cuts_never_shift_other_ops() -> None:
    state = fold(
        [
            Cut(request_id="r_1", source="agent", time_range=tr(0, 10)),
            mask("r_2", ["t"], BLUR, tr(30, 40)),
        ]
    )
    assert spans(state, "t") == [(30, 40, "blur")]  # still source time


# --- Audio --------------------------------------------------------------------------------


def test_speaker_mutes_are_separate_from_all_audio_mutes() -> None:
    ops = [
        Mute(request_id="r_1", source="agent", speaker="spk_1", time_range=tr(0, 10)),
        Mute(request_id="r_2", source="agent", time_range=tr(5, 6)),
        Unmute(request_id="r_3", source="user", time_range=tr(0, 100)),
    ]
    assert [(m.speaker, m.start, m.end) for m in fold(ops).mutes] == [("spk_1", 0, 10)]


def test_audio_filter_add_and_remove() -> None:
    ops = [
        AddAudioFilter(request_id="r_1", source="agent", filter="denoise", strength=0.5),
        RemoveAudioFilter(request_id="r_2", source="user", filter="denoise", time_range=tr(0, 3)),
    ]
    [span] = fold(ops).audio_filters
    assert (span.start, span.end, span.strength) == (3, None, 0.5)


# --- Standing rules -----------------------------------------------------------------------


def only_me(rid: str = "r_1", rule_id: str = "rule_me") -> AddRule:
    rule = Rule(rule_id=rule_id, category="person", except_identities=["id_me"], effect=BLUR)
    return AddRule(request_id=rid, source="agent", rule=rule)


def test_rules_add_and_remove() -> None:
    assert [r.rule_id for r in fold([only_me()]).rules] == ["rule_me"]
    removed = fold([only_me(), RemoveRule(request_id="r_2", source="user", rule_id="rule_me")])
    assert removed.rules == []


def test_rule_ids_must_be_unique_and_exist() -> None:
    with pytest.raises(ContractError, match="already exists"):
        fold([only_me("r_1"), only_me("r_2")])
    with pytest.raises(ContractError, match="no active rule"):
        fold([RemoveRule(request_id="r_1", source="user", rule_id="nope")])


def test_rules_at_respects_time_range() -> None:
    rule = Rule(category="face", effect=BLUR, time_range=tr(10, 20))
    state = fold([AddRule(request_id="r_1", source="agent", rule=rule)])
    assert state.rules_at(15) and not state.rules_at(25)


# --- Undo and redo ------------------------------------------------------------------------


def test_undo_reverts_a_whole_request() -> None:
    log = OpLog(
        [
            mask("r_1", ["a"]),
            mask("r_2", ["b"]),
            Cut(request_id="r_2", source="agent", time_range=tr(0, 5)),
        ]
    )
    log.undo("r_3")
    state = log.state()
    assert set(state.masks) == {"a"} and state.cuts == []


def test_redo_brings_it_back_and_a_new_edit_clears_redo() -> None:
    log = OpLog([mask("r_1", ["a"])])
    log.undo("r_2")
    assert log.state().masks == {}
    log.redo("r_3")
    assert set(log.state().masks) == {"a"}

    log.undo("r_4")
    log.append(mask("r_5", ["b"]))
    assert log.history().can_redo is None
    with pytest.raises(ContractError, match="nothing to redo"):
        log.redo("r_6")


def test_undo_is_linear() -> None:
    ops = [mask("r_1", ["a"]), mask("r_2", ["b"])]
    with pytest.raises(ContractError, match="latest request 'r_2'"):
        fold([*ops, Undo(request_id="r_3", source="user", undo_request_id="r_1")])
    with pytest.raises(ContractError, match="nothing to undo"):
        fold([Undo(request_id="r_1", source="user", undo_request_id="r_0")])


def test_redo_must_name_the_last_undone_request() -> None:
    ops = [
        mask("r_1", ["a"]),
        mask("r_2", ["b"]),
        Undo(request_id="r_3", source="user", undo_request_id="r_2"),
        Undo(request_id="r_4", source="user", undo_request_id="r_1"),
    ]
    assert history(ops).redoable == ("r_2", "r_1")
    with pytest.raises(ContractError, match="can only redo 'r_1'"):
        fold([*ops, Redo(request_id="r_5", source="user", redo_request_id="r_2")])


def test_undone_or_interleaved_requests_are_rejected() -> None:
    undone = [mask("r_1", ["a"]), Undo(request_id="r_2", source="user", undo_request_id="r_1")]
    with pytest.raises(ContractError, match="was undone"):
        fold([*undone, mask("r_1", ["b"])])
    with pytest.raises(ContractError, match="contiguous"):
        fold([mask("r_1", ["a"]), mask("r_2", ["b"]), mask("r_1", ["c"])])


def test_log_refuses_a_bad_op_and_stays_unchanged() -> None:
    log = OpLog([mask("r_1", ["a"])])
    with pytest.raises(ContractError):
        log.append(Undo(request_id="r_2", source="user", undo_request_id="r_9"))
    assert len(log) == 1


def test_duplicate_op_ids_are_rejected() -> None:
    first = mask("r_1", ["a"])
    with pytest.raises(ContractError, match="duplicate op_id"):
        fold([first, first.model_copy(update={"request_id": "r_2"})])


# --- Purity and serialization -------------------------------------------------------------


def test_fold_is_deterministic() -> None:
    ops = list(example_logs()["edit_session"])
    assert fold(ops) == fold(ops)


def test_jsonl_round_trip() -> None:
    log = example_logs()["edit_session"]
    again = OpLog.from_jsonl(log.to_jsonl())
    assert again.ops == log.ops and again.state() == log.state()


def test_jsonl_header_is_checked() -> None:
    with pytest.raises(ContractError, match="empty"):
        OpLog.from_jsonl("")
    with pytest.raises(ContractError, match="schema_version"):
        OpLog.from_jsonl(json.dumps({"schema_version": SCHEMA_VERSION + 1}) + "\n")


def test_state_round_trips_through_json() -> None:
    state = fold([Mute(request_id="r_1", source="agent"), mask("r_2", ["t"])])
    assert TimelineState.model_validate_json(state.model_dump_json()) == state


# --- Tracks -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "keyframes",
    [
        [{"t": 0, "box": [0.5, 0.1, 0.4, 0.2], "conf": 1, "state": "detected"}],  # x2 < x1
        [{"t": 0, "box": [0.1, 0.1, 1.5, 0.2], "conf": 1, "state": "detected"}],  # not 0..1
        [
            {"t": 1, "box": [0.1, 0.1, 0.2, 0.2], "conf": 1, "state": "detected"},
            {"t": 0, "box": [0.1, 0.1, 0.2, 0.2], "conf": 1, "state": "detected"},
        ],  # unsorted
    ],
)
def test_bad_tracks_are_rejected(keyframes: list[dict]) -> None:
    with pytest.raises(ValidationError):
        Track(track_id="t", label="person", source="detector", keyframes=keyframes)


# --- Examples and generated files ---------------------------------------------------------


def test_examples_validate_and_fold() -> None:
    logs = example_logs()
    assert {"blur_first_half", "edit_session", "live_only_me"} <= set(logs)
    assert example_tracks()["street"]
    session = logs["edit_session"].state()
    assert session.schema_version == SCHEMA_VERSION
    assert [(c.start, c.end) for c in session.cuts] == [(0, 5), (50, 55)]


def test_generated_files_are_up_to_date() -> None:
    stale = [p.name for p in stale_files()]
    assert not stale, f"run `uv run python -m vibe.contract` and commit: {stale}"
