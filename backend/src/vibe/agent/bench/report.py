"""Re-score saved runs and print the R-1 tables (markdown) from them. No API calls."""

import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from vibe.agent.bench.candidates import CANDIDATES, PRICES_AS_OF, cost
from vibe.agent.bench.cases import Case
from vibe.agent.bench.run import result_record
from vibe.agent.bench.score import OUTCOMES, Score, score, template_claims
from vibe.agent.planner import plan_request

Record = Mapping[str, Any]


def load_records(paths: Iterable[Path]) -> list[dict[str, Any]]:
    records = []
    for path in paths:
        with path.open(encoding="utf-8") as f:
            records += [json.loads(line) for line in f if line.strip()]
    return records


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[round(q * 100) - 1]


def _s(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _scored(records: Sequence[Record], cases: Mapping[str, Case]) -> list[tuple[Record, Score]]:
    out = []
    for r in records:
        case = cases.get(r["case"])
        if case is not None:  # a request since removed from the set
            out.append((r, score(case, r["result"], r["attempts"])))
    return out


def template_scores(cases: Iterable[Case]) -> dict[str, tuple[bool, Score]]:
    """Candidate zero: the template parser alone, per case id -> (claims it, score)."""
    out = {}
    for case in cases:
        result = result_record(plan_request(case.request))
        out[case.id] = (template_claims(result), score(case, result))
    return out


def candidate_row(name: str, scored: Sequence[tuple[Record, Score]]) -> list[str]:
    n = len(scored)
    outcomes = Counter(s.outcome for _, s in scored)
    first_try = sum(
        1
        for r, s in scored
        if len(r["attempts"]) == 1 and s.outcome not in ("provider_error", "refusal")
    )
    valid = n - outcomes["invalid"] - outcomes["provider_error"] - outcomes["refusal"]
    plans = [s for r, s in scored if r["result"]["type"] == "plan"]
    executable = sum(1 for s in plans if s.outcome not in ("unknown_label", "unresolvable_time"))
    seconds = [r["seconds"] for r, _ in scored if not r["first_call"]]
    first = [r["seconds"] for r, _ in scored if r["first_call"]]
    usd = [cost(name, r["attempts"]) for r, _ in scored]
    usd_uncached = [cost(name, r["attempts"], uncached=True) for r, _ in scored]
    tokens_in = [
        sum(
            a["usage"]["input"] + a["usage"]["cache_read"] + a["usage"]["cache_write"]
            for a in r["attempts"]
        )
        for r, _ in scored
    ]
    tokens_out = [sum(a["usage"]["output"] for a in r["attempts"]) for r, _ in scored]
    by_case: dict[str, set[str]] = defaultdict(set)
    runs_per_case: Counter[str] = Counter()
    for r, s in scored:
        by_case[r["case"]].add(s.key)
        runs_per_case[r["case"]] += 1
    # Core only: on extras the key holds the model's free-text question, whose wording varies.
    repeated = [c for c, k in runs_per_case.items() if k > 1 and c.startswith("core:")]
    consistent = _pct(sum(1 for c in repeated if len(by_case[c]) == 1), len(repeated))
    price = CANDIDATES[name].price if name in CANDIDATES else None
    return [
        name,
        f"{len(runs_per_case)} x {max(runs_per_case.values(), default=0)}",
        _pct(first_try, n),
        _pct(valid, n),
        _pct(executable, len(plans)),
        _pct(outcomes["correct"], n),
        str(outcomes["unsafe"]),
        str(outcomes["over_asked"]),
        _s(_quantile(seconds, 0.50)),
        _s(_quantile(seconds, 0.95)),
        _s(statistics.mean(first) if first else None),
        "local" if price is None else f"${statistics.mean(usd):.5f}",
        "local" if price is None else f"${statistics.mean(usd_uncached):.5f}",
        f"{statistics.mean(tokens_in):.0f} / {statistics.mean(tokens_out):.0f}" if n else "n/a",
        consistent,
    ]


MAIN_HEADER = [
    "Candidate",
    "Requests x runs",
    "Valid 1st try",
    "Valid after retry",
    "Executable plans",
    "Correct",
    "Unsafe",
    "Over-asked",
    "p50 s",
    "p95 s",
    "First call s",
    "$/request",
    "$/request uncached",
    "Tokens in / out",
    "Consistent",
]


def _table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def _local_row(name: str, records: Sequence[Record]) -> list[str] | None:
    metas = [a.get("meta", {}) for r in records for a in r["attempts"]]
    speeds = [m["tokens_per_second"] for m in metas if m.get("tokens_per_second")]
    if not speeds:
        return None
    vram = max((m.get("vram_bytes") or 0 for m in metas), default=0)
    loads = [
        m.get("load_seconds", 0)
        for r in records
        if r["first_call"]
        for m in (a.get("meta", {}) for a in r["attempts"])
    ]
    return [
        name,
        f"{statistics.median(speeds):.0f}",
        f"{vram / 2**30:.1f}" if vram else "n/a",
        f"{max(loads):.1f}" if loads else "n/a",
    ]


CEILING = "opus-5-5"
GATE_POINTS = 5.0
"""Core correctness must be within this many percentage points of the ceiling."""
GATE_P95_SECONDS = 5.0


def _core_stats(name: str, scored: Sequence[tuple[Record, Score]]) -> dict[str, Any] | None:
    core = [(r, s) for r, s in scored if r["tools"] == "core"]
    if not core:
        return None
    seconds = [r["seconds"] for r, _ in core if not r["first_call"]]
    return {
        "runs": Counter(r["case"] for r, _ in core),
        "unsafe": sum(1 for _, s in core if s.outcome == "unsafe"),
        "correct": 100 * sum(1 for _, s in core if s.outcome == "correct") / len(core),
        "p95": _quantile(seconds, 0.95),
        "usd": statistics.mean(cost(name, r["attempts"]) for r, _ in core),
    }


def decision(scored_by: Mapping[str, Sequence[tuple[Record, Score]]]) -> list[str]:
    """Apply the decision rule fixed in ``vibe.agent.bench`` before any run."""
    stats = {
        name: st
        for name, scored in scored_by.items()
        if (st := _core_stats(name, scored)) is not None
        and "quality-only" not in getattr(CANDIDATES.get(name), "tags", ())
    }
    if CEILING not in stats:
        return [f"Needs the {CEILING} ceiling run first."]
    ceiling = stats[CEILING]["correct"]
    ceiling_runs = stats[CEILING]["runs"]
    if more := [n for n, st in stats.items() if st["runs"].total() > ceiling_runs.total()]:
        names = ", ".join(more)
        return [f"The {CEILING} ceiling ran less than {names}: run it the same way first."]
    # Only runs of the same requests can be compared: a partial run has fewer chances to fail.
    same_runs = {n for n, st in stats.items() if st["runs"] == ceiling_runs}
    fewest_unsafe = min(stats[n]["unsafe"] for n in same_runs)
    rows, passing, local = [], [], []
    for name, st in stats.items():
        gates = [
            st["unsafe"] == fewest_unsafe,
            st["correct"] >= ceiling - GATE_POINTS,
            st["p95"] is not None and st["p95"] < GATE_P95_SECONDS,
        ]
        candidate = CANDIDATES.get(name)
        is_local = candidate is not None and candidate.price is None
        if name in same_runs and is_local:
            local.append((name, st, sum(gates)))
        elif name in same_runs and all(gates):
            passing.append((name, st, sum(gates)))
        rows.append(
            [
                name,
                str(st["unsafe"]),
                f"{st['correct']:.0f}%",
                f"{st['correct'] - ceiling:+.0f}",
                _s(st["p95"]),
                "local" if is_local else f"${st['usd']:.5f}",
                " ".join("pass" if g else "FAIL" for g in gates)
                if name in same_runs
                else f"not compared: {st['runs'].total()} core results, "
                f"{ceiling_runs.total()} on the ceiling",
            ]
        )
    header = [
        "Candidate",
        "Core unsafe",
        "Core correct",
        "vs ceiling",
        "p95 s",
        "$/request",
        f"Gates: unsafe (fewest = {fewest_unsafe}), correct, p95 < {GATE_P95_SECONDS:g} s",
    ]
    lines = [_table(header, rows), ""]
    if passing:
        name, st, _ = min(passing, key=lambda p: p[1]["usd"])
        lines.append(f"**Winner: {name}** (cheapest hosted candidate passing every gate).")
    else:
        lines.append("**No hosted candidate passes every gate.**")
    if local:
        name, st, gates = max(local, key=lambda p: (p[2], p[1]["correct"]))
        lines.append(f"**Offline fallback: {name}** ({gates} of 3 gates).")
    return lines


def report(records: Sequence[Record], cases: Sequence[Case], detail: bool = False) -> str:
    by_id = {c.id: c for c in cases}
    groups: dict[str, list[Record]] = defaultdict(list)
    for r in records:
        groups[r["candidate"]].append(r)
    template = template_scores(cases)
    out = [f"Prices as of {PRICES_AS_OF}. Unsafe and over-asked are counts; the rest are shares."]

    scored_by = {name: _scored(rs, by_id) for name, rs in groups.items()}
    out += [
        "",
        "### All requests",
        _table(MAIN_HEADER, (candidate_row(n, s) for n, s in scored_by.items())),
    ]

    split_header = [
        "Candidate",
        "Core, template takes it",
        "Core, LLM only",
        "Extras (eval-only tools)",
        "Routed (core)",
    ]
    rows = []
    for name, scored in scored_by.items():
        buckets: dict[str, list[Score]] = defaultdict(list)
        routed: list[Score] = []
        for r, s in scored:
            case = by_id[r["case"]]
            if case.tools == "extras":
                buckets["extras"].append(s)
                continue
            claimed, template_score = template[case.id]
            buckets["template" if claimed else "llm"].append(s)
            routed.append(template_score if claimed else s)

        def share(items: list[Score]) -> str:
            return _pct(sum(1 for s in items if s.outcome == "correct"), len(items))

        rows.append(
            [
                name,
                share(buckets["template"]),
                share(buckets["llm"]),
                share(buckets["extras"]),
                share(routed),
            ]
        )
    out += ["", "### Correct, by split", _table(split_header, rows)]
    out += ["", "### Decision rule (vibe/agent/bench/__init__.py)", *decision(scored_by)]

    core = [c for c in cases if c.tools == "core"]
    claimed = [c for c in core if template[c.id][0]]
    wrong = [c for c in claimed if template[c.id][1].outcome != "correct"]
    out += [
        "",
        "### Candidate zero: the template parser",
        f"Takes {len(claimed)} of {len(core)} core requests ({_pct(len(claimed), len(core))}); "
        f"wrong on {len(wrong)} of them.",
    ]
    out += [
        f"- wrong: {c.request!r} -> {template[c.id][1].outcome} {template[c.id][1].detail}"
        for c in wrong
    ]

    local = [row for name, rs in groups.items() if (row := _local_row(name, rs))]
    if local:
        out += [
            "",
            "### Local models",
            _table(["Candidate", "Decode tokens/s", "VRAM GB", "Load s"], local),
        ]

    out += ["", "### Where the numbers come from"]
    for name, rs in groups.items():
        hardware = sorted({r["hardware"] for r in rs})
        network = sorted({r["network"] for r in rs})
        out.append(
            f"- {name}: {rs[0]['provider']}, settings {json.dumps(rs[0]['settings'])}, "
            f"hardware {'; '.join(hardware)}, network {'; '.join(network)}, "
            f"{len(rs)} requests from {min(r['started_at'] for r in rs)[:16]} "
            f"to {max(r['started_at'] for r in rs)[:16]} UTC"
        )

    if detail:
        out += ["", "### Per request (outcome counts over runs)"]
        header = ["Request", "Expect"] + list(scored_by)
        rows = []
        for case in cases:
            row = [f"{case.tools}: {case.request}", case.expect]
            for scored in scored_by.values():
                counts = Counter(s.outcome for r, s in scored if r["case"] == case.id)
                details = sorted({s.detail for r, s in scored if r["case"] == case.id and s.detail})
                cell = ", ".join(f"{o} {counts[o]}" for o in OUTCOMES if counts[o])
                row.append(cell + (f" ({'; '.join(details)[:160]})" if details else ""))
            rows.append(row)
        out.append(_table(header, rows))
    return "\n".join(out)
