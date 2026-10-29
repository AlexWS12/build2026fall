"""F-05: plan validation against the tool registry (the draft one until F-06 lands)."""

from vibe.agent import Plan, ToolCall, validate_plan
from vibe.agent.tools_draft import DRAFT_REGISTRY


def plan(*calls: tuple[str, str, dict]) -> Plan:
    return Plan(calls=tuple(ToolCall(step_id=s, tool=t, args=a) for s, t, a in calls))


FIND_FACES = ("s1", "find_objects", {"label": "face"})


def test_a_valid_plan_has_no_errors() -> None:
    p = plan(
        FIND_FACES,
        ("s2", "resolve_time", {"phrase": "first half"}),
        (
            "s3",
            "add_mask",
            {
                "targets": {"$ref": "s1"},
                "effect": {"type": "blur"},
                "time_range": {"$ref": "s2"},
            },
        ),
    )

    assert validate_plan(p, DRAFT_REGISTRY) == []


def test_unknown_tool_is_an_error() -> None:
    errors = validate_plan(plan(("s1", "explode", {})), DRAFT_REGISTRY)

    assert any("unknown tool 'explode'" in e for e in errors)


def test_bad_arguments_are_an_error() -> None:
    errors = validate_plan(
        plan(("s1", "cut_range", {"time_range": {"start": 5, "end": 1}})), DRAFT_REGISTRY
    )

    assert any("s1 (cut_range)" in e for e in errors)


def test_unexpected_argument_is_an_error() -> None:
    errors = validate_plan(plan(("s1", "mute", {"time_range": None, "volume": 0})), DRAFT_REGISTRY)

    assert any("volume" in e for e in errors)


def test_ref_to_a_later_or_missing_step_is_an_error() -> None:
    errors = validate_plan(
        plan(
            ("s1", "add_mask", {"targets": {"$ref": "s2"}, "effect": {"type": "blur"}}),
            ("s2", "find_objects", {"label": "face"}),
            ("s3", "cut_range", {"time_range": {"$ref": "nope"}}),
        ),
        DRAFT_REGISTRY,
    )

    assert any("s1" in e and "'s2'" in e for e in errors)
    assert any("s3" in e and "'nope'" in e for e in errors)


def test_ref_of_the_wrong_type_is_an_error() -> None:
    errors = validate_plan(
        plan(FIND_FACES, ("s2", "cut_range", {"time_range": {"$ref": "s1"}})), DRAFT_REGISTRY
    )

    assert any("track_ids" in e and "time_range" in e for e in errors)


def test_ref_on_an_argument_that_takes_no_refs_is_an_error() -> None:
    errors = validate_plan(
        plan(FIND_FACES, ("s2", "find_objects", {"label": {"$ref": "s1"}})), DRAFT_REGISTRY
    )

    assert any("label" in e for e in errors)


def test_duplicate_step_ids_are_an_error() -> None:
    errors = validate_plan(plan(FIND_FACES, ("s1", "mute", {})), DRAFT_REGISTRY)

    assert any("duplicate step_id 's1'" in e for e in errors)


def test_a_plan_with_no_edit_call_is_an_error() -> None:
    errors = validate_plan(plan(FIND_FACES), DRAFT_REGISTRY)

    assert any("no edit" in e for e in errors)


def test_literal_mask_targets_are_an_error() -> None:
    errors = validate_plan(
        plan(("s1", "add_mask", {"targets": ["t1"], "effect": {"type": "blur"}})), DRAFT_REGISTRY
    )

    assert any("'targets' must be a $ref" in e for e in errors)
