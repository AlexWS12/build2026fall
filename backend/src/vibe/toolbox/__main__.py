"""Run any tool from the command line: ``uv run python -m vibe.toolbox <tool> --args '{...}'``.

Edit tools append their op to a timeline: a file (``--log``, the contract's JSONL format) or a
project in ``VIBE_DATA_DIR`` (``--project``). Query tools print what they found.
``python -m vibe.toolbox <tool> --help`` lists a tool's arguments.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from vibe.contract import OP_ADAPTER, Op, OpLog, Track, new_id
from vibe.storage import Storage, StorageError
from vibe.toolbox.context import OpSink, ToolContext
from vibe.toolbox.registry import TOOLS, call_tool
from vibe.toolbox.spec import ToolError, ToolSpec

_TRACKS = TypeAdapter(list[Track])

EXAMPLES = {
    "find_objects": '{"label": "face"}',
    "resolve_time": '{"phrase": "first half"}',
    "add_mask": '{"targets": ["trk_1"], "effect": {"type": "blur"}, '
    '"time_range": {"start": 0, "end": 5}}',
    "cut_range": '{"time_range": {"start": 10, "end": 20}}',
    "mute": '{"time_range": {"start": 10, "end": 20}}',
}


class CliError(Exception):
    """Bad input to the CLI itself (a file it can't read, args that aren't a JSON object)."""


def _type_name(annotation: object) -> str:
    """``list[str]``, ``TimeRange | None``: readable, without module paths."""
    if isinstance(annotation, type):
        return annotation.__name__
    return re.sub(r"\b(?:\w+\.)+(\w+)", r"\1", str(annotation))


def _arguments_help(spec: ToolSpec) -> str:
    lines = ["arguments (pass them as one JSON object with --args):"]
    for name, field in spec.args.model_fields.items():
        need = "required" if field.is_required() else f"default {field.default!r}"
        lines.append(f"  {name}: {_type_name(field.annotation)} ({need})")
        if field.description:
            lines.append(f"      {field.description}")
    if spec.name in EXAMPLES:
        lines.append(f"\nexample:\n  --args '{EXAMPLES[spec.name]}'")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m vibe.toolbox", description=__doc__)
    commands = parser.add_subparsers(dest="tool", required=True, metavar="<tool>")
    for spec in TOOLS.values():
        cmd = commands.add_parser(
            spec.name,
            help=f"[{spec.kind}] {spec.description}",
            description=spec.description,
            epilog=_arguments_help(spec),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        cmd.add_argument("--args", default="{}", help="the tool's arguments as a JSON object")
        where = cmd.add_mutually_exclusive_group()
        where.add_argument("--log", type=Path, help="timeline .jsonl file to append to")
        where.add_argument("--project", help="project id in VIBE_DATA_DIR to write to")
        cmd.add_argument(
            "--tracks", type=Path, help="tracks .json to search (find_objects; required)"
        )
        cmd.add_argument("--duration", type=float, help="video length in seconds (resolve_time)")
        cmd.add_argument("--request-id", help="request id for the ops (default: a new one)")
    return parser


def _load_args(text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliError(f"--args isn't valid JSON: {exc}") from None
    if not isinstance(value, dict):
        raise CliError('--args must be a JSON object, e.g. \'{"label": "face"}\'')
    return value


class _TimelineFile:
    """A timeline .jsonl outside any project (e.g. a copy of a contract example).

    Appends one line per op and syncs it, like ``storage.Project.append``; it never rewrites
    the file, so a concurrent writer's ops can't be lost. For a project's timeline, use
    ``--project``, which goes through storage itself.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, op: Op) -> Op:
        text = self.path.read_text(encoding="utf-8")
        if text and not text.endswith("\n"):
            # Appending after a half-written line would glue the op onto it and make the
            # whole file unreadable. Refuse; the person removes the broken line first.
            raise CliError(f"{self.path} ends with an incomplete line; remove it first")
        OpLog.from_jsonl(text).append(op)  # validates against the whole log; raises first
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(OP_ADAPTER.dump_json(op, exclude_none=True).decode() + "\n")
            f.flush()
            os.fsync(f.fileno())
        return op


def _open_timeline(args: argparse.Namespace) -> OpSink:
    if args.project is not None:
        try:
            return Storage().open_project(args.project)
        except StorageError as exc:
            raise CliError(str(exc)) from None
    if not args.log.is_file():
        raise CliError(f"no timeline file at {args.log}")
    return _TimelineFile(args.log)


def _load_tracks(path: Path) -> list[Track]:
    try:
        return _TRACKS.validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise CliError(f"can't read tracks {path}: {exc}") from None


def _run(args: argparse.Namespace) -> str:
    spec = TOOLS[args.tool]
    if spec.kind == "edit" and args.log is None and args.project is None:
        raise CliError(
            f"{spec.name} is an edit tool: pass --log <timeline.jsonl> or --project <id>"
        )
    tool_args = _load_args(args.args)
    ctx = ToolContext(
        request_id=args.request_id or new_id("req"),
        source="user",
        log=_open_timeline(args) if spec.kind == "edit" else None,  # query tools don't write
        duration=args.duration,
        tracks=_load_tracks(args.tracks) if args.tracks is not None else None,
    )
    return call_tool(spec.name, tool_args, ctx).model_dump_json(indent=2)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        output = _run(args)
    except (CliError, ToolError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
