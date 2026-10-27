"""The committed OpenAPI spec matches the app, so the UI's generated types match the routes."""

from vibe.api.openapi import is_stale


def test_openapi_spec_is_up_to_date() -> None:
    assert not is_stale(), (
        "run `uv run python -m vibe.api` and `npm --prefix frontend run gen:api`, then commit"
    )
