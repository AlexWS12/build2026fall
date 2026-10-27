"""Command line for checking a clip by hand: ``uv run python -m vibe.video probe clip.mp4``."""

import argparse
import dataclasses
import json
import sys
from fractions import Fraction
from pathlib import Path

from vibe.video.errors import VideoError
from vibe.video.probe import probe


def _json_default(value: object) -> object:
    if isinstance(value, Fraction):
        return str(value)  # exact, e.g. "30000/1001"
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"can't serialize {type(value).__name__}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vibe.video", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    probe_cmd = commands.add_parser("probe", help="print a clip's metadata as JSON")
    probe_cmd.add_argument("path", type=Path)
    args = parser.parse_args(argv)

    try:
        info = probe(args.path)
    except (FileNotFoundError, VideoError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(dataclasses.asdict(info), default=_json_default, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
