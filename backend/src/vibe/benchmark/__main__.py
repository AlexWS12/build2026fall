"""``python -m vibe.benchmark``: the benchmark harness from the command line (F-09).

    report [--verify] [--json] [--out DIR]   check clips and labels, score cached tracks
    add-clip FILE [--id ID --source TEXT --consent TEXT] [--cloud-ok] [--tag TAG] ...
                                             register a consented clip, or store a teammate's
    new-labels CLIP_ID                       start an empty labels file for a clip
    label-frames CLIP_ID [--label face]      write a page to draw boxes on (under data/)
    import-labels CLIP_ID FILE               put the boxes saved from that page in the labels
    offline --set SPECS [--every N] ...      run detector sets on the clips, time and cache them

Exit status 1 means something needs fixing; the output says what.
"""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from vibe.benchmark import labels as labels_mod
from vibe.benchmark import labeltool, offline
from vibe.benchmark import manifest as manifest_mod
from vibe.benchmark.labels import write_labels_template
from vibe.benchmark.manifest import (
    BenchmarkError,
    ClipEntry,
    add_clip,
    atomic_write,
    load_manifest,
)
from vibe.benchmark.report import build_report, render_markdown, render_text
from vibe.modules.vision.stitch import HeadSettings, StitchSettings
from vibe.storage import Storage, StorageError
from vibe.video import VideoError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m vibe.benchmark", description=__doc__.split("\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)

    report = commands.add_parser("report", help="check clips and labels, score cached tracks")
    report.add_argument(
        "--verify", action="store_true", help="re-hash and re-probe every clip (slow)"
    )
    report.add_argument("--json", action="store_true", help="print the report as JSON")
    report.add_argument("--out", type=Path, help="also write report.json and report.md here")

    add = commands.add_parser("add-clip", help="register a consented clip, or store a teammate's")
    add.add_argument("file", type=Path)
    add.add_argument("--id", dest="clip_id", help='short name, e.g. "street-walk" (new clips)')
    add.add_argument("--source", help="who filmed it, where and when (new clips)")
    add.add_argument("--consent", help="how the people in it agreed, and where that's recorded")
    add.add_argument(
        "--cloud-ok",
        action="store_true",
        help="their consent also covers third-party cloud processing (R-4). Default: no",
    )
    add.add_argument("--license", default="own footage")
    add.add_argument("--tag", action="append", default=[], help="repeatable, e.g. --tag faces")
    add.add_argument("--notes", default="")

    new = commands.add_parser("new-labels", help="start an empty labels file for a clip")
    new.add_argument("clip_id")

    frames = commands.add_parser(
        "label-frames", help="write a page to draw ground-truth boxes on, about one frame a second"
    )
    frames.add_argument("clip_id")
    frames.add_argument("--label", default="face", help="what the boxes are (default: face)")
    frames.add_argument("--step", type=float, default=1.0, help="seconds between frames")
    frames.add_argument("--out", type=Path, help="folder for label.html (default: under data/)")

    imported = commands.add_parser(
        "import-labels", help="put the boxes saved from label-frames into the clip's labels"
    )
    imported.add_argument("clip_id")
    imported.add_argument("file", type=Path)

    off = commands.add_parser(
        "offline", help="run detector sets over the clips, time them and cache their tracks"
    )
    off.add_argument(
        "--set",
        dest="sets",
        action="append",
        required=True,
        help='";"-separated detector specs run together, e.g. "rfdetr-n;yunet:weights=@yunet"',
    )
    off.add_argument("--every", action="append", type=int, help="stride; repeatable (default 1)")
    off.add_argument("--tracker", default="iou")
    off.add_argument("--clip", action="append", help="clip id; repeatable (default: all)")
    off.add_argument("--out", type=Path, help="also write offline.json and offline.md here")
    off.add_argument("--stitch", action="store_true", help="join track fragments (F-10b)")
    off.add_argument("--heads", action="store_true", help="head boxes for missed faces (F-10b)")

    args = parser.parse_args(argv)
    try:
        if args.command == "report":
            return _report(args)
        if args.command == "add-clip":
            return _add_clip(args)
        if args.command == "label-frames":
            return _label_frames(args)
        if args.command == "import-labels":
            return _import_labels(args)
        if args.command == "offline":
            return _offline(args)
        return _new_labels(args)
    except (BenchmarkError, VideoError, FileNotFoundError, ValidationError, StorageError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _report(args: argparse.Namespace) -> int:
    # Paths are read here, not at import, so tests can point them at a temp dir.
    report = build_report(
        manifest_path=manifest_mod.MANIFEST_PATH,
        labels_dir=labels_mod.LABELS_DIR,
        verify=args.verify,
    )
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(render_text(report), end="")
    if args.out:
        atomic_write(args.out / "report.json", report.model_dump_json(indent=2) + "\n")
        atomic_write(args.out / "report.md", render_markdown(report))
        print(f"wrote {args.out / 'report.json'} and {args.out / 'report.md'}", file=sys.stderr)
    return 1 if report.problems else 0


def _add_clip(args: argparse.Namespace) -> int:
    entry, added = add_clip(
        args.file,
        manifest_path=manifest_mod.MANIFEST_PATH,
        clip_id=args.clip_id,
        source=args.source,
        consent=args.consent,
        consent_cloud=args.cloud_ok,
        license=args.license,
        tags=args.tag,
        notes=args.notes,
    )
    if added:
        print(f"added {entry.clip_id} to the manifest and stored the video. Commit clips.json.")
    else:
        print(f"stored {entry.clip_id} (already in the manifest)")
    return 0


def _new_labels(args: argparse.Namespace) -> int:
    entry = load_manifest(manifest_mod.MANIFEST_PATH).get(args.clip_id)
    if entry is None:
        raise BenchmarkError(f"no clip {args.clip_id!r} in the manifest; add-clip it first")
    print(f"wrote {write_labels_template(entry, labels_mod.LABELS_DIR)}")
    return 0


def _entry(clip_id: str) -> ClipEntry:
    entry = load_manifest(manifest_mod.MANIFEST_PATH).get(clip_id)
    if entry is None:
        raise BenchmarkError(f"no clip {clip_id!r} in the manifest; add-clip it first")
    return entry


def _label_frames(args: argparse.Namespace) -> int:
    entry = _entry(args.clip_id)
    storage = Storage()
    video = storage.video_path(entry.video)
    if not video.is_file():
        raise BenchmarkError(f"{entry.clip_id}: the video isn't stored here; add-clip it first")
    frames = labeltool.sample_frames(video, entry.duration, args.step)
    labels = labels_mod.load_labels(entry.clip_id, labels_mod.LABELS_DIR)
    data = labeltool.page_data(entry, frames, args.label, labels)
    out = args.out or storage.root / "labels" / entry.clip_id
    page = labeltool.write_label_page(data, out)
    drawn = sum(len(b) for b in data["boxes"])
    print(f"wrote {page} ({len(frames)} frames, {drawn} existing {args.label} boxes)")
    print(
        "open it in a browser, draw, Save, then: python -m vibe.benchmark import-labels "
        f"{entry.clip_id} <saved file>"
    )
    return 0


def _import_labels(args: argparse.Namespace) -> int:
    entry = _entry(args.clip_id)
    try:
        saved = json.loads(args.file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"{args.file.name}: not valid JSON: {exc}") from exc
    labels = labels_mod.load_labels(entry.clip_id, labels_mod.LABELS_DIR)
    merged = labeltool.import_label_file(saved, entry, labels)
    path = labels_mod.labels_path(entry.clip_id, labels_mod.LABELS_DIR)
    atomic_write(path, merged.model_dump_json(indent=2, exclude_none=True) + "\n")
    label = saved["label"]
    tracks = [t for t in merged.tracks if t.label == label]
    keyframes = sum(len(t.keyframes) for t in tracks)
    print(f"wrote {path}: {len(tracks)} {label} tracks, {keyframes} keyframes. Commit it.")
    return 0


def _offline(args: argparse.Namespace) -> int:
    manifest = load_manifest(manifest_mod.MANIFEST_PATH)
    ids = args.clip or [e.clip_id for e in manifest.clips]
    missing = [i for i in ids if manifest.get(i) is None]
    if missing:
        raise BenchmarkError(f"not in the manifest: {', '.join(missing)}")
    entries = [manifest.get(i) for i in ids]
    sets = [[s.strip() for s in group.split(";") if s.strip()] for group in args.sets]
    report = offline.run_offline(
        entries,
        Storage(),
        sets,
        args.every or [1],
        args.tracker,
        progress=True,
        stitch=StitchSettings() if args.stitch else None,
        heads=HeadSettings() if args.heads else None,
    )
    labels = {
        e.clip_id: lab
        for e in entries
        if (lab := labels_mod.load_labels(e.clip_id, labels_mod.LABELS_DIR)) is not None
    }
    text = offline.render_markdown(report, labels)
    print(text, end="")
    if args.out:
        atomic_write(args.out / "offline.json", report.model_dump_json(indent=2) + "\n")
        atomic_write(args.out / "offline.md", text)
        print(f"wrote {args.out / 'offline.json'} and {args.out / 'offline.md'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
