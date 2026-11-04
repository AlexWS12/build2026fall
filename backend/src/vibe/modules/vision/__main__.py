"""Find and track targets in a video and cache them: ``uv run python -m vibe.modules.vision``.

    detectors                     list every detector, what it finds and its license
    analyze VIDEO -d SPEC ...     detect + track, write detections and tracks to the cache
    retrack VIDEO --from KEY      new tracks from cached detections (other tracker or settings)
    list VIDEO                    the cached detection and track keys for a video

Every cached track set is a column in ``python -m vibe.benchmark report``, which is how models
are compared. Times printed here are wall time on this machine, for orientation only.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from vibe.modules.vision import cache
from vibe.modules.vision.analyze import analyze_video, retrack
from vibe.modules.vision.detection import NotInstalled
from vibe.modules.vision.detectors import DETECTORS, make_detector
from vibe.modules.vision.tracking import TRACKERS, Tracker, TrackerSettings
from vibe.storage import Storage, StorageError
from vibe.video import VideoError


def _tracker_args(parser: argparse.ArgumentParser) -> None:
    defaults = TrackerSettings()
    parser.add_argument("--tracker", choices=sorted(TRACKERS), default="iou")
    for name, field in TrackerSettings.model_fields.items():
        parser.add_argument(
            f"--{name.replace('_', '-')}",
            type=float,
            default=getattr(defaults, name),
            help=field.description,
        )


def _tracker(args: argparse.Namespace) -> Tracker:
    settings = TrackerSettings(
        **{name: getattr(args, name) for name in TrackerSettings.model_fields}
    )
    return TRACKERS[args.tracker](settings)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m vibe.modules.vision",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--data-dir", type=Path, default=None, help="Default: VIBE_DATA_DIR, else <repo>/data."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("detectors", help="list every detector and its license")

    analyze = commands.add_parser("analyze", help="detect and track, write the cache")
    analyze.add_argument("video", type=Path)
    analyze.add_argument(
        "-d",
        "--detector",
        action="append",
        required=True,
        metavar="SPEC",
        help="name or name:option=value,... (repeat for several, e.g. a person and a face model)",
    )
    analyze.add_argument("--every", type=int, default=1, help="detect on every Nth frame")
    _tracker_args(analyze)

    again = commands.add_parser("retrack", help="re-run tracking on cached detections")
    again.add_argument("video", type=Path)
    again.add_argument("--from", dest="key", required=True, help="a detections cache key")
    _tracker_args(again)

    listing = commands.add_parser("list", help="cached keys for a video")
    listing.add_argument("video", type=Path)
    return parser


def _summary(tracks: list, seconds: dict[str, float] | None, frames: int) -> None:
    labels = Counter(t.label for t in tracks)
    keyframes = sum(len(t.keyframes) for t in tracks)
    unsure = sum(k.state == "uncertain" for t in tracks for k in t.keyframes)
    found = ", ".join(f"{n} {label}" for label, n in sorted(labels.items())) or "nothing"
    print(f"tracks:  {len(tracks)} ({found}); {keyframes} keyframes, {unsure} uncertain")
    if seconds:
        stages = "  ".join(f"{k} {1000 * v / max(frames, 1):.1f}" for k, v in seconds.items())
        print(f"ms per sampled frame ({frames} frames, this machine): {stages}")


def _detectors() -> int:
    print(f"{'name':16} {'license':34} {'needs':20} finds")
    for name, info in DETECTORS.items():
        print(f"{name:16} {info.license:34} {info.needs:20} {info.finds}")
    return 0


def _analyze(storage: Storage, args: argparse.Namespace) -> int:
    ref = storage.add_video(args.video)
    detectors = [make_detector(spec) for spec in args.detector]
    for spec, det in zip(args.detector, detectors, strict=True):
        info = DETECTORS[spec.partition(":")[0].strip()]
        if not info.permissive:
            print(f"warning: {det.name} is {info.license}", file=sys.stderr)
        print(f"detector {det.name} on {getattr(det, 'device', 'cpu')}")
    tracker = _tracker(args)
    analysis = analyze_video(storage.video_path(ref), detectors, tracker, every=args.every)
    det_key = cache.detections_key(detectors, args.every)
    trk_key = cache.tracks_key(det_key, tracker.name, tracker.settings)
    cache.save_detections(storage, ref.sha256, det_key, analysis.detections)
    tracks = cache.save_tracks(storage, ref.sha256, trk_key, analysis.tracks)
    print(f"video:   {ref.original_name} ({ref.sha256[:12]})")
    print(f"detections key: {det_key}")
    print(f"tracks key:     {trk_key}")
    _summary(tracks, analysis.seconds, analysis.frames)
    return 0


def _retrack(storage: Storage, args: argparse.Namespace) -> int:
    ref = storage.add_video(args.video)
    detections = cache.load_detections(storage, ref.sha256, args.key)
    tracker = _tracker(args)
    tracks = retrack(detections, tracker)
    trk_key = cache.tracks_key(args.key, tracker.name, tracker.settings)
    tracks = cache.save_tracks(storage, ref.sha256, trk_key, tracks)
    print(f"tracks key: {trk_key}")
    _summary(tracks, None, len(detections.frames))
    return 0


def _list(storage: Storage, args: argparse.Namespace) -> int:
    ref = storage.add_video(args.video)
    for kind, keys in (
        ("detections", cache.detection_keys(storage, ref.sha256)),
        ("tracks", cache.track_keys(storage, ref.sha256)),
    ):
        print(f"{kind}:")
        for key in keys or ["(none)"]:
            print(f"  {key}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "detectors":
        return _detectors()
    storage = Storage(args.data_dir)
    run = {"analyze": _analyze, "retrack": _retrack, "list": _list}[args.command]
    try:
        return run(storage, args)
    except (NotInstalled, StorageError, VideoError, ValidationError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
