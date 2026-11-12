"""FastAPI app. Every route lives under ``/api`` so the Vite dev server can proxy it.

The OpenAPI spec is committed as ``openapi.json`` and the UI's types are generated from it:
after changing a route or a response model, run ``uv run python -m vibe.api`` (a test checks it).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from vibe import config
from vibe.agent.llm import planner_from_env
from vibe.api import projects


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # A VIBE_PLANNER_LLM the app can't use (unknown, no SDK, no key) stops the server here,
    # instead of every chat request failing, or planning with templates only, unnoticed.
    planner_from_env()
    yield


app = FastAPI(title="Vibe Editing Agent", lifespan=lifespan)
app.include_router(projects.router)


class Health(BaseModel):
    status: str
    backend: config.Backend


@app.get("/api/health")
def health() -> Health:
    return Health(status="ok", backend=config.model_backend())
