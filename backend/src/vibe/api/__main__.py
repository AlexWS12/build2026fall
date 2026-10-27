"""``python -m vibe.api [--check]``: regenerate (or check) the committed OpenAPI spec."""

import argparse
import sys

from vibe.api.openapi import SPEC_PATH, is_stale, write_spec


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m vibe.api", description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the spec is out of date")
    args = parser.parse_args()

    if args.check:
        if is_stale():
            print(f"out of date: {SPEC_PATH.name}")
            print("run `uv run python -m vibe.api` and `npm --prefix frontend run gen:api`")
            return 1
        return 0

    if write_spec():
        print(f"wrote {SPEC_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
