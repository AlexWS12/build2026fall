"""Render by hand: ``uv run python -m vibe.render export clip.mp4 --log timeline.jsonl -o out.mp4``.

``--tracks`` adds a tracks .json (the contract's format). ``--project <id>`` renders a project
in ``VIBE_DATA_DIR`` instead: its video, timeline and hand-drawn tracks, into a new file in its
``exports/`` folder each time. Prints what it wrote as JSON.
"""

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from vibe.contract import OpLog, TimelineState, Track, new_id
from vibe.render.errors import RenderError
from vibe.render.export import RenderResult, export
from vibe.storage import Storage, StorageError
from vibe.video import VideoError

_TRACKS = TypeAdapter(list[Track])


class CliError(Exception):
    """Bad input to the CLI itself (a file it can't read, a missing argument)."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m vibe.render",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    cmd = commands.add_parser("export", help="render a clip with a timeline into an .mp4")
    cmd.add_argument("source", type=Path, nargs="?", help="the clip to render (not with --project)")
    where = cmd.add_mutually_exclusive_group(required=True)
    where.add_argument("--log", type=Path, help="timeline .jsonl file to render")
    where.add_argument("--project", help="project id in VIBE_DATA_DIR to render")
    cmd.add_argument("--tracks", type=Path, help="tracks .json (with --project: added to its own)")
    cmd.add_argument(
        "-o", "--output", type=Path, help="the .mp4 to write (default with --project: a new file)"
    )
    return parser


def _load_state(path: Path) -> TimelineState:
    try:
        return OpLog.from_jsonl(path.read_text(encoding="utf-8")).state()
    except (OSError, ValueError) as exc:  # ValueError covers ContractError and ValidationError
        raise CliError(f"can't read timeline {path}: {exc}") from None


def _load_tracks(path: Path) -> list[Track]:
    try:
        return _TRACKS.validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise CliError(f"can't read tracks {path}: {exc}") from None


def _run(args: argparse.Namespace) -> RenderResult:
    tracks = _load_tracks(args.tracks) if args.tracks is not None else []
    if args.project is None:
        if args.source is None or args.output is None:
            raise CliError("with --log, pass the clip to render and -o/--output")
        return export(args.source, _load_state(args.log), tracks, args.output)

    if args.source is not None:
        raise CliError("--project renders the project's own video; don't pass a clip as well")
    storage = Storage()
    try:
        project = storage.open_project(args.project)
        state = project.state()
        tracks = project.user_tracks() + tracks
    except (StorageError, ValueError) as exc:
        raise CliError(str(exc)) from None
    output = args.output or project.exports_dir / f"{new_id('export')}.mp4"
    return export(storage.video_path(project.info.video), state, tracks, output)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _run(args)
    except (CliError, RenderError, VideoError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    summary = dataclasses.asdict(result) | {
        "path": str(result.path),
        "audit": result.audit.to_json(),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
