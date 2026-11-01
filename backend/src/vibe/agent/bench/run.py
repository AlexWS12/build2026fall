"""Run candidates on the request set and save every reply; check them first with ``preflight``."""

import json
import platform
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from vibe.agent.bench.candidates import CANDIDATES, Candidate, cost
from vibe.agent.bench.cases import Case, registry_for
from vibe.agent.bench.extras import EXTRAS
from vibe.agent.llm import SchemaPlanner, reply_schema, system_prompt, user_message
from vibe.agent.plan import REF_KEY, Clarify, Plan
from vibe.agent.planner import plan_with_llm
from vibe.toolbox import TOOLS

OUT_DIR = Path(__file__).resolve().parents[5] / "outputs" / "r1-planner"
"""Repo-root ``outputs/`` is gitignored: raw replies are never committed."""

WIRE_REF_KEY = REF_KEY
"""The ref key every candidate sees on the wire. Switch all of them to "ref" if any provider
rejects a schema property named "$ref" (preflight shows it), so the comparison stays fair."""


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


def preflight(names: Sequence[str], out_dir: Path = OUT_DIR) -> bool:
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
) -> Path:
    """Run every case ``runs`` times, in order, one request at a time. Returns the JSONL path."""
    provider = candidate.build()
    planner = SchemaPlanner(provider, ref_key=WIRE_REF_KEY)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{candidate.name}-{stamp}.jsonl"
    spent, warm = 0.0, set()  # tool sets whose schema and prompt this session has sent
    with path.open("w", encoding="utf-8") as out:
        for run in range(runs):
            for i, case in enumerate(cases):
                started = datetime.now(UTC).isoformat()
                start = perf_counter()
                result = plan_with_llm(case.request, registry_for(case), planner)
                seconds = perf_counter() - start  # a failed call saves no attempt, only this
                attempts = [asdict(a) for a in planner.attempts]
                record = {
                    "candidate": candidate.name,
                    "provider": provider.name,
                    "settings": provider.settings,
                    "ref_key": WIRE_REF_KEY,
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
