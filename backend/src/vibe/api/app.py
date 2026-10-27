"""FastAPI app. Every route lives under ``/api`` so the Vite dev server can proxy it.

The OpenAPI spec is committed as ``openapi.json`` and the UI's types are generated from it:
after changing a route or a response model, run ``uv run python -m vibe.api`` (a test checks it).
"""

from fastapi import FastAPI
from pydantic import BaseModel

from vibe import config

app = FastAPI(title="Vibe Editing Agent")


class Health(BaseModel):
    status: str
    backend: config.Backend


@app.get("/api/health")
def health() -> Health:
    return Health(status="ok", backend=config.model_backend())
