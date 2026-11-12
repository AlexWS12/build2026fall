"""R-1 planner LLM benchmark: score candidate LLMs (and the template parser) on the request set.

Run ``uv run --group llm --env-file ../.env python -m vibe.agent.bench --help`` from
``backend/``. Every number in the R-1 write-up comes from here, with the hardware stated.
Raw replies go to ``outputs/r1-planner/`` (gitignored), so re-scoring never costs an API call.

**Decision rule** (R-1b, fixed by paul on 2026-11-12, before any candidate was scored; ``report``
applies it). It replaces R-1's rule from 2026-10-31, whose second gate was "within 5 points of
the Opus 5.5 ceiling": R-1b has no budget, so no paid model runs, ceiling included.

Over the core requests the template parser doesn't take (the ones that reach the LLM in
production), ``DECISION_RUNS`` runs each:

1. zero ``unsafe`` results, or, if no candidate has zero, the fewest;
2. at least ``GATE_CORRECT_PCT`` correct;
3. p95 latency under ``GATE_P95_SECONDS``, on the hardware and network the run states.

Only free and local candidates are decided between, and only ones that ran every one of those
requests ``DECISION_RUNS`` times. Of the free hosted candidates that pass every gate, the one
with the most correct wins, then the lowest p95 (all of them cost nothing per call). The local
model that passes, or passes the most gates, becomes the offline fallback. Quality-only
candidates (an extra proxy hop) can't pass the latency gate. Extras (eval-only tools) are
reported but don't decide anything.
"""

DECISION_RUNS = 5
GATE_CORRECT_PCT = 80.0
GATE_P95_SECONDS = 5.0
