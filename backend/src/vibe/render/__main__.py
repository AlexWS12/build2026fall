"""Render by hand: ``uv run python -m vibe.render export clip.mp4 --log timeline.jsonl -o out.mp4``.

``--tracks`` adds a tracks .json (the contract's format). ``--project <id>`` renders a project
in ``VIBE_DATA_DIR`` instead: its video and timeline, with the tracks cached for the video (F-10)
and the ones drawn by hand, into a new file in its ``exports/`` folder each time. ``--analysis
<key>`` says which cached tracks, when the video has several sets. Its denoise noise profile is
cached with the video, so the next export skips that pass. Prints what it wrote as JSON.

``--segmenter sam2`` (F-15) hides masked targets by their shape instead of their box: it first
segments the keyframes of the tracks the timeline masks (only those, and only what the analysis
cache doesn't have yet), then renders. If segmenting fails, it says why and renders with boxes.
"""

import argparse
import dataclasses
import json
import logging
import sys
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from vibe.contract import OpLog, TimelineState, Track, new_id
from vibe.modules.cache import ModuleError
from vibe.modules.denoise import cached_noise_profile
from vibe.modules.vision import cache
from vibe.modules.vision.detection import NotInstalled
from vibe.modules.vision.masks import MaskSet, ensure_masks
from vibe.modules.vision.segmenters import make_segmenter
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
    cmd.add_argument(
        "--segmenter",
        metavar="SPEC",
        help="hide masked targets by shape (F-15): sam2, sam2:model=facebook/sam2.1-hiera-small, "
        "or stub; masks are cached under VIBE_DATA_DIR",
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


def _masks(
    spec: str | None,
    storage: Storage,
    sha256: str,
    video: Path,
    state: TimelineState,
    tracks: list[Track],
) -> tuple[MaskSet | None, dict[str, object] | None]:
    """The masks for ``--segmenter``, and what to report about them. A bad spec is the user's
    mistake (an error); a segmenter that can't run means boxes, said out loud."""
    if spec is None:
        return None, None
    try:
        segmenter = make_segmenter(spec)
    except ValueError as exc:
        raise CliError(str(exc)) from None
    except NotInstalled as exc:
        print(f"warning: no masks, rendering boxes: {exc}", file=sys.stderr)
        return None, {"segmenter": spec, "error": str(exc)}
    try:
        masks, report = ensure_masks(storage, sha256, video, state, tracks, segmenter)
    except Exception as exc:  # e.g. the GPU ran out: boxes still hide everything
        logging.getLogger(__name__).exception("segmenting failed")
        print(f"warning: no masks, rendering boxes: {exc}", file=sys.stderr)
        return None, {"segmenter": segmenter.name, "error": str(exc)}
    info = dataclasses.asdict(report)
    info["device"] = str(getattr(segmenter, "device", "cpu"))
    if report.frames_segmented:
        info["seconds_per_frame_segmented"] = report.segment_seconds / report.frames_segmented
    return masks, info


def _run(args: argparse.Namespace) -> tuple[RenderResult, dict[str, object] | None]:
    tracks = _load_tracks(args.tracks) if args.tracks is not None else []
    if args.project is None:
        if args.analysis is not None:
            raise CliError("--analysis picks a project's cached tracks; use it with --project")
        if args.source is None or args.output is None:
            raise CliError("with --log, pass the clip to render and -o/--output")
        state = _load_state(args.log)
        masks, info = None, None
        if args.segmenter is not None:
            sha256 = cache.video_sha256(args.source)
            masks, info = _masks(args.segmenter, Storage(), sha256, args.source, state, tracks)
        return export(args.source, state, tracks, args.output, masks=masks), info

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
    video = project.info.video
    path = storage.video_path(video)
    masks, info = _masks(args.segmenter, storage, video.sha256, path, state, tracks)
    result = export(
        path,
        state,
        tracks,
        output,
        noise_profile=lambda rate, layout: cached_noise_profile(storage, video, rate, layout),
        masks=masks,
    )
    return result, info


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result, masks = _run(args)
    except (CliError, cache.CacheError, RenderError, VideoError, ModuleError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    summary = {
        "path": str(result.path),
        "frames": result.frames,
        "duration": result.duration,
        "issues": [dataclasses.asdict(issue) for issue in result.issues],
        "audit": result.audit.to_json(),
    }
    if masks is not None:
        summary["masks"] = masks
        summary["shape_use"] = {k: dataclasses.asdict(v) for k, v in result.shape_use.items()}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
