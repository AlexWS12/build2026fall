"""R-1 planner LLM benchmark: score candidate LLMs (and the template parser) on the request set.

Run ``uv run --group llm --env-file ../.env python -m vibe.agent.bench --help`` from
``backend/``. Every number in the R-1 write-up comes from here, with the hardware stated.
Raw replies go to ``outputs/r1-planner/`` (gitignored), so re-scoring never costs an API call.
"""
