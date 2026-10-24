"""FastAPI app. Every route lives under ``/api`` so the Vite dev server can proxy it."""

from fastapi import FastAPI

from vibe import config

app = FastAPI(title="Vibe Editing Agent")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "backend": config.model_backend()}
