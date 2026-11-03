"""F-06: the tool registry. Tool names and arguments are a shared file: these tests guard them."""

import pytest

from vibe.toolbox import TOOLS, ToolSpec

SPECS = list(TOOLS.values())


def test_the_registry_has_the_planners_tools() -> None:
    assert set(TOOLS) == {"find_objects", "resolve_time", "add_mask", "cut_range", "mute"}


def test_registry_is_read_only() -> None:
    with pytest.raises(TypeError):
        TOOLS["explode"] = TOOLS["mute"]  # type: ignore[index]


@pytest.mark.parametrize("spec", SPECS, ids=[s.name for s in SPECS])
def test_each_spec_is_named_by_its_key_and_documented(spec: ToolSpec) -> None:
    assert TOOLS[spec.name] is spec
    assert spec.description


@pytest.mark.parametrize("spec", SPECS, ids=[s.name for s in SPECS])
def test_argument_models_reject_unknown_fields(spec: ToolSpec) -> None:
    assert spec.args.model_config.get("extra") == "forbid"


@pytest.mark.parametrize("spec", SPECS, ids=[s.name for s in SPECS])
def test_ref_arguments_are_real_fields(spec: ToolSpec) -> None:
    fields = set(spec.args.model_fields)

    assert set(spec.ref_args) <= fields
    assert spec.ref_required <= set(spec.ref_args)


def test_query_tools_return_something_a_later_step_can_use() -> None:
    assert {s.returns for s in SPECS if s.kind == "query"} <= {"track_ids", "time_range"}
    assert {s.returns for s in SPECS if s.kind == "edit"} == {"none"}
