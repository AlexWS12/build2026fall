# Changelog

All notable changes, one section per version. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [SemVer](https://semver.org/) as described in [HANDOFF.md](HANDOFF.md#versions).

This file is written at **sprint close** (`/b-release`), from that sprint's handoff entries. During a sprint, current work lives in [docs/handoffs/](docs/handoffs/).

## [Unreleased]

## [0.1.0] - 2026-10-28
Sprint 1: foundation + stack decisions.

### Added
- Project docs: README, CLAUDE.md, HANDOFF.md (branches, handoffs, versions), docs/ARCHITECTURE.md (layers, contract, live mode, compute tiers, storage, decisions D-001 to D-014), docs/ROADMAP.md (backlog F-01…F-63, weekly sprints up to demo day on 2026-12-04, claiming a card).
- Team Claude Code skills: `/b-sync`, `/b-handoff`, `/b-release`.
- Repo skeleton (F-01): `backend/` uv project with the `vibe` package, `VIBE_BACKEND` = stub/local/remote (default stub), FastAPI `GET /api/health`, pytest and Ruff, root `npm run dev` / `test` / `lint`, backend CI that needs no GPU.
- Edit-timeline contract v1 (F-02): one Pydantic model per op kind, `fold` to timeline state with undo/redo, tracks with normalized boxes, append-only JSONL `OpLog` with `schema_version`, generated JSON schemas and example timelines.
- Project storage (R-2): plain folders under `VIBE_DATA_DIR`, videos stored once by sha256, shared analysis cache (`Storage.cache_path`), per-project timeline and user tracks, atomic writes.
- Video I/O (F-03): `probe`, a frame index with frame↔time mapping (including VFR), `VideoReader`, audio extraction and chunking, a `python -m vibe.video probe` CLI. Tests build their clips with ffmpeg; CI installs it.
- Web UI shell (F-07): React + Vite + TypeScript with upload, chat, player and read-only timeline against a mock API by default (`VITE_API_MODE=http` for the real backend), plus frontend CI.
- Generated API types (F-07): committed OpenAPI spec (`python -m vibe.api`), `npm run gen:api` to TypeScript, CI fails on a stale spec or stale types.
