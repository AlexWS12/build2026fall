"""``python -m vibe.contract [--check]``: regenerate (or check) the schemas and example states."""

import argparse
import sys

from vibe.contract.schema import HERE, stale_files, write_generated


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m vibe.contract", description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail if a generated file is out of date"
    )
    args = parser.parse_args()

    if args.check:
        stale = stale_files()
        for path in stale:
            print(f"out of date: {path.relative_to(HERE)}")
        if stale:
            print("run `uv run python -m vibe.contract` and commit the result")
        return 1 if stale else 0

    for path in write_generated():
        print(f"wrote {path.relative_to(HERE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
