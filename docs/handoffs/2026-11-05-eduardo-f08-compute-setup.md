---
author: eduardo
date: 2026-11-05
branch: eduardo/compute-setup
features: [F-08]
base: v0.1.0 @ d95ede3
head: uncommitted
status: ready for review
---

# F-08: stub / local / remote backends over the shared cache, model server, `doctor`, `download`, PyTorch extras, team GPU survey

> **Shared files changed. Post a heads-up in the team chat before merging.** `backend/pyproject.toml` and `uv.lock` get optional `cpu` / `cu128` PyTorch extras. A plain `uv sync` still installs no torch. `backend/src/vibe/config.py` gets new settings. `docs/ARCHITECTURE.md` "Compute" gets the survey table and a "Built so far" note, and the README gets setup steps per tier. **Luis (F-10, PR #22), Paul (F-31 transcribe) and Nickolas (F-33 denoise) are directly affected:** each of their modules does its own `VIBE_BACKEND` dispatch today. See "Next steps".

## What changed
- **`backend/src/vibe/modules/`** (CLI: `uv run python -m vibe.modules --help`):
  - `backend.py`: the module interface.
    - A module subclasses `Analysis`, setting `module` (registry name), `kind` (cache folder), `version` and `settings`, and implements `compute(video_path) -> JSON data`. `@register` lets the model server build it by name.
    - `run_analysis(storage, video, analysis, backend=None)` checks the cache under `analysis.key` first, for every backend. On a miss, `stub` raises `NoAnalysis` and names any results cached under other settings, `local` computes and caches, and `remote` calls the model server and caches the result here.
    - `replay(storage, sha256, kind, key=None)` returns "whatever is cached". With several cached results and no key it raises `AmbiguousAnalysis`; with none it raises `NoAnalysis`.
  - `cache.py`: `cache_key(name, version, settings)` gives `<name>-<8 hex>`, where the hex is sha256 of canonical JSON. Writes and reads are atomic JSON, plus `cached_keys` and `pick_key`. **It refuses biometric kinds** (`embeddings`, `identity`, `face_embeddings`, `biometric`), per D-017.
  - `remote.py`: `RemoteClient` plus `create_app()` for the model server (`serve`).
    - The client sends `{module, settings, video sha256}`. On `404 video_missing` it uploads the video once and retries.
    - It **rejects a result whose key differs from its own**, which happens when the server runs another version of the module.
    - It refuses any host not in `VIBE_REMOTE_HOSTS`, with no opt-in path for third-party hosts yet, so those fail closed.
    - There's an optional bearer token (`VIBE_REMOTE_TOKEN`).
    - The server runs one analysis at a time (models take turns in VRAM), checks the sha256 of uploads, and caches results on its side too.
    - It uses only the standard library's HTTP; no new dependency.
  - `doctor.py`: reports the GPU from `nvidia-smi` or PyTorch, the driver, memory and PyTorch build, then a tier with its reason and warnings: driver below 570, a CPU-only torch on a GPU machine, Blackwell without `sm_120` kernels. It works with nothing installed.
  - `download.py` + `models.json`: fetches weights into `VIBE_MODELS_DIR` (default `data/models/`) and checks their sha256.
    - Unpinned entries are refused unless you pass `--pin`, which prints the hash to pin.
    - Entries: `yunet`, `sface` (URL, **hashes not pinned yet**), `egoblur-face` / `egoblur-plate` (gated, manual), `rfdetr` (its package fetches its own weights).
  - `__main__.py`: `doctor [--json]`, `models`, `download NAME... [--pin]`, `cache SHA256 [KIND]`, `serve`.
- **`config.py`:** `models_dir()`, `remote_url()`, `remote_hosts()`, `remote_token()` (env `VIBE_MODELS_DIR`, `VIBE_REMOTE_URL`, `VIBE_REMOTE_HOSTS`, `VIBE_REMOTE_TOKEN`).
- **`pyproject.toml` / `uv.lock`:** extras `cpu` (`torch>=2.7`, `torchvision>=0.22` from the PyTorch CPU index; PyPI wheels on macOS) and `cu128` (the PyTorch `cu128` index). The two conflict, so pick one. The lock resolved `cu128` to torch 2.11.0+cu128 and `cpu` to 2.15.0.
- **Tests, `backend/tests/modules/`:** 42 tests, no GPU, no network. The exit check is `test_stub_replays_a_cached_result`. The others cover:
  - a stub miss fails closed, and `stub` is the default
  - `local` computes once, then hits the cache
  - key stability
  - `replay` refuses to guess
  - biometric kinds are refused
  - a corrupt cache file is an error, not a miss
  - `remote` round-trips through an in-process server (upload once, cached on both sides)
  - the allowlist, the token, the version-mismatch check and the upload hash check
  - the stub path imports no torch, cv2 or onnxruntime
  - tier rules on every survey machine
  - driver and `sm_120` warnings
  - `download` checks hashes and leaves no `.part` file
- **Docs:**
  - [`docs/research/f08-gpu-survey.md`](../research/f08-gpu-survey.md): every answer as reported, a tier per machine and what follows from it.
  - The survey table and "Built so far" in ARCHITECTURE "Compute".
  - "Running models: setup per compute tier" in the README.
  - My F-08 row in the roadmap is set to `in review`.

## How to check it
```bash
cd backend
uv sync --locked                                   # still no torch
uv run pytest tests/modules -q                     # 42 passed
uv run pytest -q                                   # 495 passed
uv run ruff check && uv run ruff format --check
uv run python -m vibe.modules doctor               # your machine's tier
uv run python -m vibe.modules models
```
Verified on 2026-11-05 on macOS 26.6.2 (Apple M2, 8 GB, no GPU used), Python 3.12, with ffmpeg on PATH: 495 passed, and Ruff is clean. **Not tested:**
- `uv sync --extra cpu` / `--extra cu128` actually installing torch (it's only locked).
- `doctor` on any NVIDIA machine (only on this Mac, plus unit tests on parsed `nvidia-smi` output).
- `serve` over a real network (only in-process via `TestClient`).
- `download` against the real YuNet / SFace URLs.

CI hasn't run.

## Decisions made
eduardo made these on 2026-11-05 for F-08. Say on the PR if you disagree. **The interface below should get a decisions-log row (D-019) once the team agrees;** it's not added yet.
- **Cache keys are `<readable name>-<8 hex>`,** with the hash over name, version and settings. This is the same shape as Luis's F-10 keys, so his fit as they are.
- **The cache stores JSON the module chooses, one file per key.** For `tracks` that's a list of contract `Track`s, which is what F-09's report already scores.
- **Every backend reads the cache first.** "Stub" means "never compute", not "different data".
- **The remote server must produce the same key as the client,** or the result is refused. That way the cache never holds a result that doesn't match its key.
- **There's no third-party opt-in yet:** a host not on the allowlist always fails closed. The per-project opt-in in D-017 is still to be built, by whoever adds tier 3.
- **The PyTorch extras are in `pyproject.toml`** (D-010 planned optional groups). Model-specific packages (`rfdetr`, `opencv-python`, `faster-whisper`) stay hand-installed until their module's owner adds them.

## Next steps
1. **Heads-up in the team chat** about the shared files, then open the PR.
2. **Alex:** run `doctor` on the 5070 and post the output. It confirms the VRAM, and the survey doc and ARCHITECTURE table get updated. If it's the 8 GB laptop version, raise whether Nickolas's 16 GB card should be the reference machine (D-008 is a team decision).
3. **Fit the modules under `run_analysis`** (with each owner):
   - **F-10 (Luis):** after #22 merges, `vision/cache.py` can use `modules.cache`, and `load_tracks` becomes `replay(storage, sha, "tracks", key)`.
   - **F-31 (Paul):** `transcribe_video` becomes an `Analysis` with kind `transcript`.
   - **F-33 (Nickolas):** denoise is a transform, not an analysis, so it stays outside unless it gets cached.
4. **Pin the YuNet and SFace hashes:** `download yunet sface --pin` on a machine with network, check the files, add the hashes to `models.json`.
5. **Install the extras on a real GPU** (`uv sync --extra cu128` on a Blackwell card) and record the `doctor` output in the survey doc.
6. **Propose D-019** for the interface above.

## Gotchas
- **The `cu128` index tops out at torch 2.11.** Newer PyTorch releases ship on newer CUDA indexes, so `cpu` and `cu128` resolve to different versions (2.15 and 2.11). Both are ≥ 2.7, so Blackwell works, but don't compare numbers across them as if they were the same build.
- **`uv sync` removes hand-installed packages,** extras included unless you pass them again. Use `uv sync --inexact --extra cu128`.
- **Luis's driver (555.97) can't run `cu128`.** `doctor` flags it.
- **On Apple Silicon, `--extra cpu` takes torch from PyPI** (the PyTorch CPU index has no macOS wheels), and that build includes MPS.
- **`register` is global.** Two modules can't share a `module` name, and tests use a `test-` prefix.
