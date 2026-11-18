"""Render by hand: ``uv run python -m vibe.render export clip.mp4 --log timeline.jsonl -o out.mp4``.

``--tracks`` adds a tracks .json (the contract's format). ``--project <id>`` renders a project
in ``VIBE_DATA_DIR`` instead: its video and timeline, with the tracks cached for the video (F-10)
and the ones drawn by hand, into a new file in its ``exports/`` folder each time. ``--analysis
<key>`` says which cached tracks, when the video has several sets. Prints what it wrote as JSON.
"""

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from vibe.contract import OpLog, TimelineState, Track, new_id
from vibe.modules.vision import cache
from vibe.render.errors import RenderError
from vibe.render.export import RenderResult, export
from vibe.storage import Project, Storage, StorageError
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
        "--analysis",
        metavar="KEY",
        help="with --project: which of the video's cached track sets to use (default: the one "
        "marked as its default, or its only one; `python -m vibe.modules.vision list VIDEO` "
        "shows them)",
    )
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


def _cached_tracks(storage: Storage, project: Project, key: str | None) -> list[Track]:
    """The detector tracks cached for the project's video (F-10): ``key``'s, or the default set.

    Read here, before the render starts, so the per-frame path never touches the cache. A video
    that was never analysed gives no tracks and still renders, with the tracks drawn by hand: a
    mask whose target has no track then hides the whole frame, and the audit lists it as
    ``no_track`` (privacy fails closed). ``cache.project_tracks`` can't be used for this, because
    it gives ``None`` for such a video even when boxes were drawn. A ``key`` that isn't cached is
    an error instead, since a key given by name was meant to exist.

    Raises ``cache.CacheError`` when the cache can't say which set to use: with no ``key``, several
    sets and no default it's ``AmbiguousAnalysis``, which names the keys to choose from.
    """
    sha256 = project.info.video.sha256
    tracks = cache.load_tracks(storage, sha256, key)
    if tracks is not None:
        return tracks
    if key is None:
        return []
    cached = ", ".join(cache.track_keys(storage, sha256)) or "none, the video isn't analysed"
    raise CliError(f"no cached tracks {key!r} for video {sha256[:12]} (cached: {cached})")


def _run(args: argparse.Namespace) -> RenderResult:
    tracks = _load_tracks(args.tracks) if args.tracks is not None else []
    if args.project is None:
        if args.analysis is not None:
            raise CliError("--analysis picks a project's cached tracks; use it with --project")
        if args.source is None or args.output is None:
            raise CliError("with --log, pass the clip to render and -o/--output")
        return export(args.source, _load_state(args.log), tracks, args.output)

    if args.source is not None:
        raise CliError("--project renders the project's own video; don't pass a clip as well")
    storage = Storage()
    try:
        project = storage.open_project(args.project)
        state = project.state()
        tracks = _cached_tracks(storage, project, args.analysis) + project.user_tracks() + tracks
    except (StorageError, ValueError) as exc:
        raise CliError(str(exc)) from None
    output = args.output or project.exports_dir / f"{new_id('export')}.mp4"
    return export(storage.video_path(project.info.video), state, tracks, output)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _run(args)
    except (CliError, cache.CacheError, RenderError, VideoError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    summary = {
        "path": str(result.path),
        "frames": result.frames,
        "duration": result.duration,
        "issues": [dataclasses.asdict(issue) for issue in result.issues],
        "audit": result.audit.to_json(),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
