"""R-1 planner LLM benchmark: score candidate LLMs (and the template parser) on the request set.

Run ``uv run --group llm --env-file ../.env python -m vibe.agent.bench --help`` from
``backend/``. Every number in the R-1 write-up comes from here, with the hardware stated.
Raw replies go to ``outputs/r1-planner/`` (gitignored), so re-scoring never costs an API call.

**Decision rule** (fixed by paul on 2026-10-31, before any candidate was run; ``report``
applies it). Over the core requests (the five toolbox tools), all runs:

1. zero ``unsafe`` results, or, if no candidate has zero, the fewest;
2. correct within 5 points of the Opus 5.5 ceiling;
3. p95 latency under 5 s.

Of the hosted candidates that pass, the lowest $/request wins. The local model that passes,
or comes closest, becomes the offline fallback. Extras (eval-only tools) are reported but
don't decide anything.
"""
