# Changelog

All notable changes, one section per version. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [SemVer](https://semver.org/) as described in [HANDOFF.md](HANDOFF.md#versions).

This file is written at **sprint close** (`/b-release`), from that sprint's handoff entries. During a sprint, current work lives in [docs/handoffs/](docs/handoffs/).

## [Unreleased]

## [0.4.0] - 2026-11-17
Sprint 4: the app really edits + human in the loop.

### Added
- Plan executor: a chat request is applied to the timeline. Every step is dry-run on a copy first, then written as one batch; a refused step writes nothing and replies `not_applied` with the reason.
- Plain-words replies ("Muted 0:00–0:05.") with the tool steps behind a disclosure (F-40).
- Undo / redo per request, as tools, chat requests and routes, plus a History list in the header (F-43).
- API routes: project list and info, source video (with Range), history, undo/redo, tracks, analysis, exports and the stored conversation.
- UI: export button (stale once the timeline changes), live preview of edits in the player with a Preview / Original switch, a timeline with playhead and named mask lanes, a landing page with recent projects, reload reopens the project, and a new look. The UI session is one state machine.
- Review queue for low-confidence mask stretches (lost, no track, uncertain, gap) with Confirm and Cut, and a new `confirm_mask` op (F-41, D-021 Proposed).
- Targeting by description: `find_by_description` ("the person in red", "everyone except the person in red"), a colour-judge stub and a cache of verdicts. Unsure tracks are always masked (F-13).
- Transcription with word timestamps (faster-whisper on CPU) through F-08's `run_analysis`, a transcript panel, and `find_speech` for "mute / cut where he says X" (F-31, F-21).
- `keep_range` and "keep only …" requests; a request's ops land in one batch (`append_many`) (F-20).
- `resolve_range` for halves, thirds, first/last N, before/after/between, with past-the-end checks (F-12).
- Mask audit in `RenderResult`, matching the renderer frame by frame; `--project` export uses the video's cached F-10 tracks, with `--analysis KEY` (F-11).
- Benchmark: 9 licensed Pexels clips in the manifest, person labels for 5 of them (F-09b).

### Changed
- Decision D-020 (Proposed): Qwen3.5-4B (local) / 9B (remote) as the VLM for F-13.
- Contract: new op kind `confirm_mask` and an optional `TimelineState.reviewed`; `schema_version` stays 1.

## [0.3.0] - 2026-11-10
Sprint 3: first real edits. Tagged late, on 2026-11-17, at `751159b` (the last merge of 2026-11-10). F-11, F-12, F-20 and F-31 merged after that, so they're in 0.4.0.

### Added
- API routes: upload, chat request (planned, not applied yet) and timeline state, one request at a time per project (F-64).
- Renderer: per-frame path, MP4 export, blur / pixelate / black box, cuts and mutes; fails closed when a track is missing (F-04).
- Detect-and-track pipeline: detector interface and adapters, IoU and ByteTrack trackers, per-video cache, `retrack` from cached detections (F-10).
- Compute backends `stub` / `local` / `remote` over the shared cache, a model server, `doctor`, `download`, PyTorch extras and the team GPU survey (F-08).
- Mute a time range with edge fades, an `unmute` tool and `TimelineState.mutes_at` (F-30).
- `denoise` tool and templates with a stub spectral gate; the renderer doesn't apply it yet (F-33).

### Fixed
- "mute it" / "silence it" no longer mute the whole clip; the planner asks instead (F-30).

F-64 and F-04 are listed in Sprint 2's "Closed" line, but they merged on 2026-11-04 and 11-05, after the Sprint 2 cut, so they're in this version.

## [0.2.0] - 2026-11-03
Sprint 2: first end-to-end path. Tagged late, on 2026-11-17, at `b5e3c66` (the last merge of 2026-11-03).

### Added
- Toolbox: five typed tools, registry, CLI; `find_objects` fails closed without analysis, `cut_range` refuses to cut the whole video (F-06).
- Planner, part 1: template parser, time phrases, plan validation and clarify replies, with a slot for the LLM (F-05).
- Benchmark harness: clip manifest, label format, detection and exposure scoring, `report` with the hardware stated (F-09).
- Research: R-3 model shortlist (D-015 Proposed), R-4 data policy and target shape (D-017, D-018 Proposed), R-1 planner-LLM doc (no model picked yet).

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
