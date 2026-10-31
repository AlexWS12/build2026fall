"""CLI for the R-1 planner LLM benchmark. From ``backend/``:

uv run --group llm --env-file ../.env python -m vibe.agent.bench preflight
uv run --group llm --env-file ../.env python -m vibe.agent.bench run --candidate haiku-5-5
uv run python -m vibe.agent.bench report            # every saved run, no API calls
"""

import argparse
import sys
from pathlib import Path

from vibe.agent.bench.candidates import CANDIDATES
from vibe.agent.bench.cases import DEFAULT_SETS, load_cases
from vibe.agent.bench.report import load_records, report
from vibe.agent.bench.run import OUT_DIR, hardware, preflight, run_candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m vibe.agent.bench",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("preflight", help="check each model exists and accepts the schemas")
    check.add_argument(
        "--candidate",
        action="append",
        choices=list(CANDIDATES),
        help="repeatable; default: every candidate",
    )

    run = sub.add_parser("run", help="run one candidate on the request set")
    run.add_argument("--candidate", required=True, choices=list(CANDIDATES))
    run.add_argument("--runs", type=int, default=5)
    run.add_argument("--set", choices=("core", "extras", "all"), default="all")
    run.add_argument("--max-usd", type=float, default=None, help="stop once this much is spent")
    run.add_argument("--hardware", default=None, help="default: detected from this machine")
    run.add_argument("--network", default="not stated", help='e.g. "home Wi-Fi, Miami"')
    run.add_argument("--limit", type=int, default=None, help="only the first N requests")

    rep = sub.add_parser("report", help="score saved runs and print the tables")
    rep.add_argument("files", nargs="*", type=Path, help=f"default: every file in {OUT_DIR}")
    rep.add_argument("--detail", action="store_true", help="add a per-request table")

    for p in (run, rep):
        p.add_argument(
            "--requests",
            action="append",
            type=Path,
            help=f"repeatable; default: {', '.join(s.name for s in DEFAULT_SETS)}",
        )

    args = parser.parse_args(argv)
    if args.command == "preflight":
        return 0 if preflight(args.candidate or list(CANDIDATES)) else 1

    cases = load_cases(args.requests or DEFAULT_SETS)
    if args.command == "run":
        if args.set != "all":
            cases = [c for c in cases if c.tools == args.set]
        cases = cases[: args.limit]
        path = run_candidate(
            CANDIDATES[args.candidate],
            cases,
            args.runs,
            max_usd=args.max_usd,
            hardware_label=args.hardware or hardware(),
            network=args.network,
        )
        print(f"saved {path}")
        return 0

    files = args.files or sorted(OUT_DIR.glob("*.jsonl"))
    print(report(load_records(files), cases, detail=args.detail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
