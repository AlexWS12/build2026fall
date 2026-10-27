---
author: alex
date: 2026-10-26
branch: alex/storage
features: [R-2]
base: untagged @ 84a095d
head: 04a6473
status: ready for review
---

# R-2 decided and built: plain folders, videos by hash, a shared analysis cache

> **Heads-up:** this adds `data_dir()` to `backend/src/vibe/config.py` (a shared file) and a new shared layer, `backend/src/vibe/storage/`. It proposes **D-014**. Post in the team chat before merging.

## What changed
- **Decision (D-014, Proposed):** plain folders under `VIBE_DATA_DIR` (default `data/` at the repo root, gitignored); no database.
  - `videos/<sha256>.<ext>`: each video stored once;
  - `cache/<sha256>/<kind>/<key>.json`: the analysis cache, shared across projects;
  - `projects/<project_id>/`: `project.json`, `timeline.jsonl`, `tracks.user.json`, `exports/`.
  
  The full layout and reasoning are under "Storage" in `docs/ARCHITECTURE.md`.
- **`backend/src/vibe/storage/`:**
  - `Storage`: `add_video` (hash, store once), `create_project`, `open_project`, `list_projects`, `cache_path(sha256, kind, key)`.
  - `Project`:
    - `log()` / `state()`;
    - `append(op)`, which validates against the whole log with the contract before writing a line;
    - `user_tracks()` / `save_user_tracks()`;
    - `exports_dir`.
  - JSON files are written atomically (temp file + rename). An incomplete last line of the log, left by a crash, is dropped with a warning. Project ids and cache names are checked so they can't escape their folder.
- `vibe/config.py`: `data_dir()` reads `VIBE_DATA_DIR`.
- Tests: `backend/tests/test_storage.py` has 21 tests, and `storage` is added to the smoke test's layer list.
- Docs: ARCHITECTURE has a new "Storage" section, a `storage/` line in "Code layout", D-014, and the storage open question removed. HANDOFF.md's shared files now list `vibe/storage/`. ROADMAP: R-2 is claimed.

## How to check it
```bash
cd backend
uv run pytest -q          # 74 passed
uv run ruff check && uv run ruff format --check
```
Verified on 2026-10-26 on Windows 11 (Python 3.12.12). CI runs on the PR. The tests use a temporary data folder and small fake video files, so no real video is needed.

## Decisions made
Alex made these on 2026-10-26; they're proposed as D-014:
- **The analysis cache belongs to the video, not the project.** It's keyed by sha256, so projects reuse it, and sharing results computed on the 5070 is copying `cache/<sha256>/` (D-008).
- **Videos are content-addressed,** so there are no duplicates and the hash matches the cache key.
- **Hand-drawn tracks live in the project** (they're edits), not in the cache.
- **The code is in, not just the decision,** so F-06, F-08 and the API have something real to call.
- **Left to F-08:** what each cache `kind` contains, and the exact key format. The suggestion is `<model>@<version>-<settings hash>`, so changing a model never serves stale results.

## Next steps
- Review and merge after the heads-up. Flip D-014 to Accepted if the team agrees; R-2 gets marked `done` at sprint close.
- **F-08:** use `Storage.cache_path()` for the `stub` / `local` / `remote` backends. The stub backend can replay a cached file.
- **API routes** (not in the backlog yet; propose a card): upload → `create_project`, request → `project.append(...)`, `GET` state. **The API has to apply one request at a time per project**, because storage doesn't lock and the contract needs a request's ops to be contiguous.

## Gotchas
- `data_dir()` defaults to `<repo>/data`, found from the package's location. That's right for the normal `uv sync` (editable) install. If the package is ever installed non-editable, set `VIBE_DATA_DIR`.
- `Project.append` re-reads and re-validates the whole log on every append. That's fine at the expected size (hundreds of ops).
- Hashing a large video reads the whole file once (seconds for a GB). If uploads get slow, hash while receiving the upload instead.
