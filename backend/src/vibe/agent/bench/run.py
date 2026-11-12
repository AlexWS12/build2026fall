"""Run candidates on the request set and save every reply; check them first with ``preflight``."""

import hashlib
import json
import platform
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, perf_counter, sleep
from typing import Any

from vibe.agent.bench.candidates import CANDIDATES, Candidate, cost
from vibe.agent.bench.cases import Case, ToolSet, registry_for
from vibe.agent.bench.extras import EXTRAS
from vibe.agent.llm import SchemaPlanner, reply_schema, system_prompt, user_message
from vibe.agent.plan import REF_KEY, Clarify, Plan
from vibe.agent.planner import plan_with_llm
from vibe.agent.providers import RateLimited
from vibe.toolbox import TOOLS, ToolSpec

OUT_DIR = Path(__file__).resolve().parents[5] / "outputs" / "r1-planner"
"""Repo-root ``outputs/`` is gitignored: raw replies are never committed."""

WIRE_REF_KEY = REF_KEY
"""The ref key every candidate sees on the wire. Switch all of them to "ref" if any provider
rejects a schema property named "$ref" (preflight shows it), so the comparison stays fair."""

REGISTRIES: Mapping[ToolSet, Mapping[str, ToolSpec]] = {"core": TOOLS, "extras": EXTRAS}


def prompt_hash(tools: ToolSet) -> str:
    """What a candidate was shown for ``tools``: the system prompt, the schema and the ref key.
    Results with another hash came from a different prompt and aren't compared with these."""
    registry = REGISTRIES[tools]
    shown = json.dumps(
        [system_prompt(registry, WIRE_REF_KEY), reply_schema(registry, WIRE_REF_KEY)],
        sort_keys=True,
    )
    return hashlib.sha256(shown.encode()).hexdigest()[:12]


class PaidCandidate(ValueError):
    """A paid candidate was asked to run without ``allow_paid`` (R-1b has no budget)."""


def check_allowed(candidate: Candidate, allow_paid: bool) -> None:
    if candidate.access == "paid" and not allow_paid:
        raise PaidCandidate(
            f"{candidate.name} is a paid API and R-1b has no budget; pass --allow-paid to run it"
        )


class _Stop(Exception):
    """Not an ``LlmError``, so ``plan_with_llm`` lets it through instead of asking the user."""


class _StopOnRateLimit:
    """The planner, except a rate limit ends the run instead of becoming a scored failure."""

    def __init__(self, planner: SchemaPlanner) -> None:
        self.planner = planner

    @property
    def attempts(self) -> list[Any]:
        return self.planner.attempts

    def plan(
        self, request: str, tools: Mapping[str, ToolSpec], error: str | None
    ) -> Mapping[str, Any]:
        try:
            return self.planner.plan(request, tools, error)
        except RateLimited as exc:
            raise _Stop(str(exc)) from exc


def saved_pairs(candidate: str, out_dir: Path = OUT_DIR) -> set[tuple[str, int]]:
    """(case id, run) pairs already saved for ``candidate`` with today's prompt, for --resume."""
    hashes = {tools: prompt_hash(tools) for tools in REGISTRIES}
    done = set()
    for path in sorted(out_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                if r["candidate"] == candidate and r.get("prompt_hash") == hashes[r["tools"]]:
                    done.add((r["case"], r["run"]))
    return done


def hardware() -> str:
    """This machine, for every number's label. Pass ``--hardware`` to say it better."""
    gpu = "no NVIDIA GPU found"
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            gpu = out.stdout.strip().replace("\n", "; ")
        except (OSError, subprocess.SubprocessError):
            pass
    return f"{platform.system()} {platform.release()} ({platform.machine()}), GPU: {gpu}"


def result_record(result: Plan | Clarify) -> dict[str, Any]:
    if isinstance(result, Plan):
        return {"type": "plan", "calls": [c.model_dump() for c in result.calls]}
    return {"type": "clarify", "question": result.question, "reason": result.reason}


def preflight(names: Sequence[str], out_dir: Path = OUT_DIR, allow_paid: bool = False) -> bool:
    """For each candidate: does the model exist, and does it accept both reply schemas?

    The output is also saved: its call times are the cold ones (Claude compiles a new schema
    and caches it for 24 hours; a local model loads into VRAM), so later runs start warm.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"preflight-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.txt"
    ok = True
    with path.open("w", encoding="utf-8") as log:

        def say(line: str) -> None:
            print(line)
            log.write(line + "\n")

        say(f"preflight {datetime.now(UTC).isoformat()} on {hardware()}, ref key {WIRE_REF_KEY!r}")
        for name in names:
            say(f"## {name}")
            try:
                check_allowed(CANDIDATES[name], allow_paid)
                provider = CANDIDATES[name].build()
                say(f"  model: {json.dumps(provider.describe(), default=str)[:600]}")
                for label, registry in (("core", TOOLS), ("extras", EXTRAS)):
                    reply = provider.complete(
                        system_prompt(registry, WIRE_REF_KEY),
                        user_message("blur faces for the first half", None, None),
                        reply_schema(registry, WIRE_REF_KEY),
                    )
                    say(
                        f"  {label} schema: accepted, {reply.seconds:.2f}s (cold), "
                        f"stop={reply.stop_reason}, usage={asdict(reply.usage)}, "
                        f"${cost(name, [asdict(reply)]):.5f}, meta={dict(reply.meta)}"
                    )
                    say(f"    reply: {reply.text[:300]}")
            except Exception as exc:  # report every candidate, then fail
                ok = False
                say(f"  FAILED: {type(exc).__name__}: {str(exc)[:800]}")
    print(f"saved {path}")
    return ok


def run_candidate(
    candidate: Candidate,
    cases: Sequence[Case],
    runs: int,
    *,
    max_usd: float | None,
    hardware_label: str,
    network: str,
    out_dir: Path = OUT_DIR,
    rpm: float | None = None,
    resume: bool = False,
    allow_paid: bool = False,
) -> Path:
    """Run every case ``runs`` times, in order, one request at a time. Returns the JSONL path.

    ``rpm`` paces the calls (a retry counts as a call). A rate limit stops the run and keeps
    what's saved; with ``resume``, a later run skips the (case, run) pairs already saved with
    the same prompt, so a free tier's daily cap only spreads a pass over several days.
    """
    check_allowed(candidate, allow_paid)
    provider = candidate.build()
    planner = _StopOnRateLimit(SchemaPlanner(provider, ref_key=WIRE_REF_KEY))
    out_dir.mkdir(parents=True, exist_ok=True)
    done = saved_pairs(candidate.name, out_dir) if resume else set()
    hashes = {tools: prompt_hash(tools) for tools in REGISTRIES}
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{candidate.name}-{stamp}.jsonl"
    interval = 60.0 / rpm if rpm else 0.0
    next_call = monotonic()
    spent, warm = 0.0, set()  # tool sets whose schema and prompt this session has sent
    with path.open("w", encoding="utf-8") as out:
        for run in range(runs):
            for i, case in enumerate(cases):
                if (case.id, run) in done:
                    continue
                if (wait := next_call - monotonic()) > 0:
                    sleep(wait)
                started = datetime.now(UTC).isoformat()
                start = perf_counter()
                try:
                    result = plan_with_llm(case.request, registry_for(case), planner)
                except _Stop as exc:
                    print(f"stopped: rate limited ({exc}). Run again later with --resume.")
                    return path
                seconds = perf_counter() - start  # a failed call saves no attempt, only this
                attempts = [asdict(a) for a in planner.attempts]
                next_call = monotonic() + interval * max(1, len(attempts)) - seconds
                record = {
                    "candidate": candidate.name,
                    "provider": provider.name,
                    "settings": provider.settings,
                    "ref_key": WIRE_REF_KEY,
                    "prompt_hash": hashes[case.tools],
                    "case": case.id,
                    "request": case.request,
                    "tools": case.tools,
                    "run": run,
                    "first_call": case.tools not in warm,
                    "result": result_record(result),
                    "seconds": seconds,
                    "attempts": attempts,
                    "started_at": started,
                    "hardware": hardware_label,
                    "network": network,
                }
                out.write(json.dumps(record, default=str) + "\n")
                out.flush()
                warm.add(case.tools)
                spent += cost(candidate.name, attempts)
                outcome = record["result"].get("reason", "plan")
                print(f"  run {run + 1}/{runs} case {i + 1}/{len(cases)} ${spent:.4f} {outcome}")
                if max_usd is not None and spent > max_usd:
                    print(f"stopped: spent ${spent:.4f}, over --max-usd {max_usd}")
                    return path
    return path
