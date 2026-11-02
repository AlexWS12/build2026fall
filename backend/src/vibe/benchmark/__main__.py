"""``python -m vibe.benchmark``: the benchmark harness from the command line (F-09).

    report [--verify] [--json] [--out DIR]   check clips and labels, score cached tracks
    add-clip FILE [--id ID --source TEXT --consent TEXT] [--cloud-ok] [--tag TAG] ...
                                             register a consented clip, or store a teammate's
    new-labels CLIP_ID                       start an empty labels file for a clip

Exit status 1 means something needs fixing; the output says what.
"""

import argparse
import sys
from pathlib import Path

from vibe.benchmark import labels as labels_mod
from vibe.benchmark import manifest as manifest_mod
from vibe.benchmark.labels import write_labels_template
from vibe.benchmark.manifest import BenchmarkError, add_clip, atomic_write, load_manifest
from vibe.benchmark.report import build_report, render_markdown, render_text
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

    args = parser.parse_args(argv)
    try:
        if args.command == "report":
            return _report(args)
        if args.command == "add-clip":
            return _add_clip(args)
        return _new_labels(args)
    except (BenchmarkError, VideoError, FileNotFoundError) as exc:
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


if __name__ == "__main__":
    sys.exit(main())
