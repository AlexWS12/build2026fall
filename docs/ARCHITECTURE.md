# Architecture

> **Status: tentative (2026-10-23).** This is the shape we intend to build. The layers and the rules in **bold** are agreed; the models, libraries and the exact contract fields are not. When something gets decided, add it to the [decisions log](#decisions-log) and update the section it affects. Don't treat a candidate as a decision.

## The layers

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │ Input: chat request · click / drawn box · standing live rule         │
 └───────────────┬──────────────────────────────────────────────────────┘
                 ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ Agent / planner: request → plan (JSON) of tool calls                 │
 │   template parser for plain requests; one LLM call for the rest      │
 └───────────────┬──────────────────────────────────────────────────────┘
                 ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ Toolbox: fixed, typed, deterministic tools                           │──► AI modules
 │   find_*, check_*, resolve_time, add_mask, cut_range, mute, ...      │    vision · audio · generative
 └───────────────┬──────────────────────────────────────────────────────┘    (results cached per video)
                 ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ Edit timeline (THE CONTRACT): op log ──fold──► derived state         │◄── human in the loop:
 └───────────────┬──────────────────────────────────────────────────────┘    preview, review, fix, undo
                 ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ Renderer (deterministic): whole file → export · frame by frame → live│
 └──────────────────────────────────────────────────────────────────────┘
```

### 1. Input
A chat message, a UI action (click a target, draw a box, drag a box), or a **standing rule** ("from now on, blur anyone who isn't me"). Every input gets a `request_id`, so all the ops it causes can be undone together.

### 2. Agent / planner
- **It turns a request into a plan and never touches pixels or audio.** Its only power is calling toolbox tools.
- **Plain requests skip the LLM.** A template parser handles requests like "blur all faces" or "cut 0:10 to 0:20" instantly, at no cost.
- **Harder requests get one LLM call that writes the whole plan as JSON**, and plain code executes it. At Knight Hacks, letting the LLM call tools one step at a time took over 2 minutes per request; one plan took 2-7 s.
- **An invalid plan is retried once with the error, then the agent asks the user to clarify.** It never guesses silently.
- Hybrid inference: the LLM may be a cloud model (candidate: Claude). It receives the request, video metadata, and the summaries the tools return (ids, labels, times, counts), **not footage by default**.
- Open: which LLM; whether the planner also sees a few frames (privacy and cost trade-off); how conversation history ("now do the same for him") is passed.

### 3. Toolbox
- **The tools are fixed, typed and deterministic.** Arguments are validated against a schema, so a malformed call fails loudly. Tools return compact JSON, never images.
- **Two kinds of tool:**
  - **query tools** look at the video and return candidates, e.g. `find_objects("person")`, `check_attribute(ids, "wearing red?")`, `find_speech("weather")`, `find_speakers()`, `resolve_time("first half")`;
  - **edit tools** write ops to the timeline, e.g. `add_mask`, `remove_mask`, `cut_range`, `mute`, `denoise`, `remove_object`, `add_rule`, `undo`.
- **Hand edits from the UI go through the same tools**, so they land in the same op log and undo works the same way.
- **Analysis runs once per video and is cached** (tracks, transcripts, speaker turns), so repeat requests are cheap.
- Every tool has a CLI and a stub implementation, so it can be tested without a GPU.

### 4. AI modules
These are wrapped by the query tools and swappable behind them.
- **Vision:** detection (open vocabulary), segmentation, tracking, visual questions (VLM), identity (face/person embeddings).
- **Audio:** transcription with word timestamps, diarization (who spoke when), speaker separation, noise suppression.
- **Generative:** video inpainting (object removal), and later background replacement.
- **The rule for every module:** expose a **whole-clip** call (offline) and, where it could ever run live, a **per-frame / per-chunk** call. Even if only the offline path is implemented at first, the interface leaves room for the other.
- Candidates are in the README's tech stack table. Heavy models run on our GPUs, possibly behind one resident model server, as Knight Hacks did.

### 5. Edit timeline (the contract)
See [The contract](#the-contract-edit-timeline-v1-in-code) below. Everything upstream writes to it and everything downstream reads from it. **It's the one interface that lets 10 people build in parallel against stubs.**

### 6. Renderer
- **It's deterministic:** the same timeline and the same source always give the same output.
- **It applies the fail-safe rules:** interpolate boxes between keyframes, hold a lost track's mask for a few frames and grow it, pad uncertain masks more, and on an error mask the whole affected segment and log it, never skip it.
- **Two paths:** **export** (whole file, through ffmpeg) and **frame by frame** (preview and live). They share the same masking and effect code, so preview, export and live look identical.

## Code layout
One repo, with the backend and frontend side by side, each with its own toolchain (D-011). A frontend-only person never needs Python, and a backend-only person never needs Node beyond the root scripts.

```
backend/                 Python 3.12, uv (D-010). Package name: vibe
  src/vibe/
    contract/            edit timeline: op models, fold, undo, JSON schema (F-02)
    video/               probe, decode, frame<->time, audio extraction (F-03)
    toolbox/             typed tools that write ops (F-06)
    modules/             AI modules, each with stub / local / remote backends (F-08)
    render/              export and per-frame paths (F-04)
    storage/             project folders, videos by hash, analysis cache paths (R-2)
    agent/               planner: request -> plan of tool calls (F-05)
    api/                 FastAPI app; every route lives under /api
    config.py            settings from the environment (VIBE_BACKEND, default stub; VIBE_DATA_DIR)
  tests/
frontend/                React + Vite + TS (D-009), created by F-07
  src/api/generated/     TS types generated from the backend's OpenAPI spec (F-07 extra)
package.json             root scripts: `npm run dev` starts every part of the app
.github/workflows/       backend.yml and frontend.yml, one per side
```

- **One folder per layer.** Each feature owner works in their own subpackage, so parallel branches rarely touch the same files.
- **The browser talks to one address.** The Vite dev server proxies `/api` to the backend (F-07), so the UI and API share an origin in development.
- **CI runs per side.** Each workflow only runs when its side changes, and the backend's runs with no GPU on the `stub` backend.

## Compute: everyone can work, whatever their machine
The team's hardware varies widely. Some people have a laptop with no usable GPU, some a weak or older GPU, and the strongest known machine is **Alex's RTX 5070** (VRAM to be confirmed: 12 GB on the desktop card, 8 GB on the laptop version). **Nobody should be blocked by their hardware.**

**Compute tiers:**

| Tier | Machine | What runs there | Used for |
|---|---|---|---|
| 0 | Any laptop, CPU only | Stub models, the UI, agent/planner (cloud LLM), contract, renderer (ffmpeg on CPU is fine), and small audio models (e.g. Whisper small, DeepFilterNet run on CPU) | Most feature work: UI, agent, toolbox, timeline, renderer, tests |
| 1 | Laptop or desktop GPU, ~4–8 GB | Small or quantized models: lightweight detectors, trackers, Whisper, maybe small segmentation | Developing and spot-checking one model at a time |
| 2 | **RTX 5070 (reference machine)** | Detection + segmentation + VLM (quantized) + audio models, loaded on demand; the live feasibility spike | Real end-to-end runs, **benchmarks**, demos |
| 3 | Rented / cloud GPU (optional, open question) | Heavy jobs: video inpainting on long clips, big VLMs | Only if tier 2 can't do it; check free credits (university HPC, student programs, Colab/Kaggle) first |

**Design rules that make the tiers work:**
- **Every AI module has three interchangeable backends**, picked by config:
  - **`stub`** replays cached or ground-truth results, with no model;
  - **`local`** runs the model on this machine;
  - **`remote`** calls a model server on another machine over HTTP.
  Code above the module can't tell which one it got.
- **The analysis cache is shareable.** Detections, tracks, transcripts and speaker turns for a test clip are computed once on a GPU machine and saved as files. Anyone can then develop on **real** model output from a CPU laptop. This extends the Knight Hacks stub mode, which only replayed hand-made ground truth.
- **A remote model server is possible.** The 5070 machine (or a rented GPU) can run a model server that teammates call over the network, e.g. through a private network like Tailscale. It only helps while that machine is on, so it's a convenience, never a requirement.
- **Prefer models with small variants** (n/s/m/l sizes, quantized builds), so the same code runs on tier 1 with a smaller model and on tier 2 with the full one.
- **12 GB (or 8 GB) means models take turns.** Not everything fits in VRAM at once (a VLM alone is ~5–6 GB at 4-bit for an 8B model), so the backend loads models on demand and unloads idle ones. Live mode needs its per-frame models (detector, tracker, identity) to fit together.
- **Benchmarks state the hardware.** The 5070 is the reference for quoted numbers; numbers from other machines are labeled as such.

**Gotcha (RTX 50 series):** these are Blackwell GPUs, which need a recent CUDA (12.8+) and a PyTorch build compiled for them (PyTorch 2.7+ with the `cu128` wheels). Older wheels and some libraries with custom CUDA kernels may not support them yet. Check this before adopting any model.

## Storage
> Code: [`backend/src/vibe/storage/`](../backend/src/vibe/storage/) (R-2). Decision: D-014.

Everything lives in plain folders under one **data directory**: `VIBE_DATA_DIR`, by default `data/` at the repo root (gitignored). There's no database.

```
data/
  videos/<sha256>.<ext>                 each uploaded video, stored once by content hash
  cache/<sha256>/<kind>/<key>.json      AI analysis per video: tracks, transcript, speakers... (F-08)
  projects/<project_id>/
    project.json                        name, created_at, which video (hash, extension, original name)
    timeline.jsonl                      the op log, in the contract's file format
    tracks.user.json                    tracks drawn or corrected by hand
    exports/                            rendered files
```

- **Videos are stored once.** Two projects on the same clip share one file, and the hash is the same key the cache uses.
- **The analysis cache belongs to the video, not the project.** Detector tracks, transcripts and speaker turns are facts about the video, so every project on it reuses them. **To share results computed on the RTX 5070, copy `cache/<sha256>/`**: a teammate with the same clip (same hash) gets real model output on a CPU laptop (D-008). Each cache **key** must change whenever the result would, e.g. `<model>@<version>-<settings hash>`, so switching models never serves stale results. What goes in each `kind`, and its exact keys, is F-08's call.
- **Hand-drawn tracks are edits,** so they live in the project, not in the cache. The renderer gets cache tracks + user tracks.
- **Writes are crash-safe.** JSON files are written to a temp file and renamed. An op is appended as one line and synced; if a crash leaves an incomplete last line, it's dropped on the next read with a warning.
- **One request at a time per project.** The contract needs a request's ops to be contiguous, and storage doesn't lock, so the API layer applies requests to a project one at a time.
- **`project.json` has its own `storage_version`** (1), bumped on a breaking layout change, like the contract's `schema_version`.
- **Why not a database:**
  - sharing a project or a cache is copying a folder;
  - the files are readable when debugging;
  - appending to the op log is appending a line;
  - nothing to install;
  - Knight Hacks worked this way.

  A database (e.g. SQLite) would pay off with many concurrent writers or queries across projects, neither of which we have.

## Human in the loop
The fallback for model mistakes is a person, made cheap and fast:
- **The agent reports what it did in plain words:** "Asked 'is this the person in red?' about 4 people: 1 yes, 2 no, 1 unsure → blurred 2 (the yes and the unsure)". The raw tool calls are available too.
- **Confidence is stored on everything a model produced.** Below a threshold it's flagged, and a **review queue** steps the user through flagged spots.
- **Direct manipulation:** click a target to change its effect or remove it, drag a box to correct it (it re-tracks from there), draw a box to add one.
- **Undo** reverts a whole request (every op with that `request_id`), and older versions stay in the log.
- **Fail safe by edit type:**

| Edit type | When unsure... |
|---|---|
| Privacy (blur, pixelate, box) | mask it (fail closed), flag it for review |
| Cut / keep | don't cut; ask, or preview the candidate ranges |
| Mute / audio | preview the result; ask when the target speaker is ambiguous |
| Object removal | show the inpainted preview before applying; the user accepts or rejects |
| Live rules | mask (fail closed); there's no time to ask |

## The contract: edit timeline (v1, in code)
> **The code is the source of truth:** [`backend/src/vibe/contract/`](../backend/src/vibe/contract/) (F-02). Generated JSON schemas live in [`contract/schema/`](../backend/src/vibe/contract/schema/), and example logs with their folded states in [`contract/examples/`](../backend/src/vibe/contract/examples/). This section explains the rules; for exact fields, read the models or the schemas. `schema_version` is **1**. Design choices: D-013.

The timeline is an **append-only op log** plus a **derived state** computed by folding the log. Ops are what the agent and UI produce, and the state is what the renderer reads. Geometry (where a target is on each frame) is **not** in either one: it lives in **tracks** from the analysis cache.

```
 agent / toolbox / UI hand edits ──► OpLog (ops, append-only) ──fold()──► TimelineState ─┐
 detectors / tracker / drawn boxes ─► tracks (analysis cache, keyed by track id) ────────┴─► renderer
```

**Op kinds** (one typed model each; an unknown kind or field, or a bad value, fails validation):

| Kind | Does |
|---|---|
| `add_mask` / `remove_mask` | mask targets (track ids) with an effect (`blur`, `pixelate`, `box` + padding, strength), or unmask them, over a time range |
| `set_effect` | change the effect only where targets are already masked |
| `cut` / `uncut` | remove a time range from the output, or bring it back. "Keep only X" is the toolbox writing cuts around X |
| `mute` / `unmute` | silence a time range: all audio, or one `speaker` (diarization id) |
| `add_audio_filter` / `remove_audio_filter` | e.g. `denoise` over a time range |
| `add_rule` / `remove_rule` | **standing rule** evaluated on every frame: mask every `category` detection except `except_identities` (live mode; also works offline). Unknown or unsure identity = masked |
| `undo` / `redo` | undo the latest request in effect, or redo the most recently undone one |

Every op has `op_id`, `request_id` (the chat message or UI action that caused it) and `source` (`agent` or `user`). `remove_object` (inpainting) isn't in v1; it gets added when its research lands.

**Rules:**
- **Time is source time, in seconds.** Every time range refers to the original video, so a cut never shifts other edits. Ranges include the start and exclude the end, and `end: null` means "until the end of the video". The state converts with `source_to_output()` / `output_to_source()`; the toolbox converts what the user sees on screen into source time before writing an op.
- **Later ops win.** Each op paints its own time range over what came before: per target for masks, per speaker for mutes, per filter for audio filters. Blur 0–30 then pixelate 10–20 gives blur, pixelate, blur. Cuts add up and merge.
- **Undo is per request and linear.** `undo` must name the latest request still in effect and appends an `undo` op (nothing is ever deleted), so history and redo come free. A new edit after an undo clears redo. A request's ops must be contiguous in the log (the server applies one request at a time).
- **Fold is pure:** no files, no cache, no network, and the same log always gives the same state. That makes it cheap to test and safe to call per frame (live mode).
- **Every state span keeps the `op_id` and `request_id` that made it**, so the UI can say where a mask came from.
- **Tracks:** `track_id`, `label`, `source` (`detector` / `user`), optional `identity`, and keyframes `{t, box, conf, state}`. Boxes are **normalized** `[x1, y1, x2, y2]` in 0..1, so one track works on a 720p preview, a 1080p export and a live stream.
- **File format** (`OpLog.to_jsonl()`): a header line `{"schema_version": 1}`, then one op per line. Where the file lives is R-2's decision.

**Changing the contract:**
- **Any change that breaks a reader or writer bumps `SCHEMA_VERSION`** in `contract/common.py`. A new optional field or a new op kind doesn't.
- **Post a heads-up in the team chat before merging**, and add a decisions-log row.
- Run `uv run python -m vibe.contract` (from `backend/`) to regenerate the schemas and example states, and commit them. A test fails if they're stale.

## Live mode
**Priority stretch** (README, "Live mode"). We are not building it first, but every early decision must keep it possible.

**The flow:**
```
 camera ─► capture ─► [per-frame analysis: detect every k frames, track in between, identity check]
                         │
 standing rules ─────────┤   (set by chat once; the LLM is never on the per-frame path)
                         ▼
                    per-frame renderer ─► delay buffer (~0.5–1 s) ─► virtual camera ─► OBS / stream
```

- **Standing rules, not one-off edits.** "Only I am visible" becomes an `add_rule` op with `category: "person", except_identities: ["id_me"]` (see `contract/examples/live_only_me.jsonl`). The same rule can run offline on a file.
- **Identity:** an enrollment step (a few seconds of the streamer on camera) stores an embedding locally. Each person track is checked against it. **Not matching or unsure means masked.**
- **Latency budget:** capture, detection on some frames, tracking on every frame, masking and encoding all have to fit in one frame interval on average. The delay buffer absorbs spikes. Knight Hacks measured about 134 ms per 720p face + plate + person pass on a Strix Halo iGPU (p99 500 ms), which suggests detecting every ~4th frame plus tracking, behind about 0.8 s of delay. That was **measured, not built**, on different hardware.
- **What to do early:** a feasibility spike that measures per-frame latency of the candidate person detector, tracker and identity check on the reference RTX 5070, at 720p and 1080p. Its result decides whether live mode stays a stretch or moves up.
- **Design rules now (cheap to keep, expensive to retrofit):**
  - rules exist in the contract from v0;
  - the renderer has a per-frame entry point;
  - AI modules have per-frame or per-chunk calls where they could ever run live;
  - nothing on the per-frame path calls the network.
- Open: virtual camera tooling per OS (OBS Virtual Camera, v4l2loopback, pyvirtualcam); whether live runs in the browser or as a local helper process.

## Lessons from Knight Hacks (don't relearn these)
- One LLM-written plan per request beats step-by-step tool calling (over 2 min → 2–7 s).
- Open-vocabulary detectors are weak on relationships and attributes ("the guy in red in the back"). Instead: detect all people, then ask a VLM yes/no per track, then track.
- YOLOE found 0% of faces on the PP4AV benchmark, so specialist models (EgoBlur) handle faces and plates. **Benchmark before trusting a model.**
- A person split into many short tracks means many VLM calls and inconsistent answers. Track stitching helped.
- A CDN or proxy in front of the app can cache exports. Give every rendered file a new URL.
- Label test data carefully: head boxes vs face boxes changed recall numbers by a lot.
- fp16 inference and a warm-up at startup were the biggest speed wins on video models.

## Decisions log
Add a row when something gets decided. **"Accepted" means "what we're going with for now", not "final".** Any decision can be revisited when we learn something (a benchmark, a failed prototype): add a new row explaining why, and mark the old one `Superseded by D-0xx` instead of deleting it.

| ID | Date | Decision | Status | Why |
|---|---|---|---|---|
| D-001 | 2026-10-23 | Agent → deterministic typed toolbox → AI modules → edit timeline → renderer. The agent never touches pixels or audio | Accepted | Worked in the Knight Hacks MVP; makes edits inspectable, undoable and testable |
| D-002 | 2026-10-23 | Hybrid inference: a cloud LLM is allowed for planning; heavy vision and audio models on our GPUs | Accepted | Better planning quality; models stay where the footage is |
| D-003 | 2026-10-23 | Web app (browser UI + Python backend) | Accepted | Easiest for 10 people to run; same shape as Knight Hacks |
| D-004 | 2026-10-23 | SemVer tags, bumped at sprint close, starting at v0.1.0; handoffs record commit hashes | Accepted | No version conflicts between parallel PRs; see HANDOFF.md |
| D-005 | 2026-10-23 | Handoffs are one file per handoff in `docs/handoffs/`, rolled into CHANGELOG and archived at sprint close | Accepted | No merge conflicts with 10 parallel branches |
| D-006 | 2026-10-23 | Template parser for plain requests + one LLM call writing a JSON plan for the rest | Proposed | Carried over from Knight Hacks; confirm once the toolbox is defined |
| D-007 | 2026-10-23 | Live mode is the top-priority stretch; the contract has standing rules and the renderer a per-frame path from v0 | Accepted | Keeps live possible without building it first |
| D-008 | 2026-10-23 | Compute tiers: every AI module has `stub` / `local` / `remote` backends; the analysis cache is shareable; the RTX 5070 is the reference machine for benchmarks | Accepted | Team hardware ranges from CPU-only laptops to one RTX 5070; nobody should be blocked |
| D-009 | 2026-10-24 | Frontend: React + Vite + TypeScript; frontend types generated from the backend's OpenAPI schema | Accepted | The timeline, review queue and box editing are state-heavy UI shared by 10 people; typed components catch contract mismatches at build time |
| D-010 | 2026-10-24 | Python environments and dependencies are managed with uv (pinned Python version, one committed `uv.lock`) | Accepted | One fast, reproducible install for everyone; optional dependency groups keep GPU-only packages (PyTorch `cu128`) off CPU-only laptops |
| D-011 | 2026-10-24 | One repo with `backend/` (Python package `vibe`, src layout, one subpackage per layer) and `frontend/` side by side, each with its own toolchain and CI workflow; root `package.json` scripts start everything; Python pinned to 3.12 for now | Accepted | Generated frontend types (D-009) need both sides in one PR; separate folders keep the toolchains apart. Revisit Python 3.14 after the R-3 model shortlist |
| D-012 | 2026-10-24 | Backend web framework: FastAPI + Uvicorn | Proposed | Used by the F-01 skeleton; its OpenAPI output is what D-009's generated types need. Confirm as a team |
| D-013 | 2026-10-26 | Contract v1 (F-02): Pydantic v2 models, one per op kind; geometry in separate tracks (normalized boxes), not in ops or state; all times are source time in seconds; later ops win over their own range; undo/redo are appended ops, linear, per request; fold is pure | Proposed | Typed ops catch LLM and UI mistakes and generate the JSON schema and TS types; separate tracks let detectors improve without invalidating edits and fit live mode; source time keeps cuts from shifting other edits; append-only undo keeps full history. Confirm on the F-02 PR |
| D-014 | 2026-10-26 | Storage (R-2): plain folders under `VIBE_DATA_DIR`, no database. Videos stored once by sha256; a shared analysis cache keyed by video hash, then kind and key (key includes model, version and settings); one folder per project with `project.json`, `timeline.jsonl`, `tracks.user.json`, `exports/`; atomic writes | Proposed | Sharing results computed on the 5070 = copying a folder (D-008); no duplicate videos; readable files; append-only log; nothing to install. A database adds nothing for one writer per project. Confirm on the R-2 PR |
| D-015 | 2026-10-28 | Vision models (R-3): only permissively licensed models (Apache 2.0 / MIT, code and weights) on the default path. Detect: RF-DETR N/S for live, L for offline (D-FINE / DEIMv2 as alternates). Faces and plates: EgoBlur Gen2 offline, YuNet for live. Track: Roboflow `trackers` (ByteTrack default; OC-SORT / BoT-SORT). Identity: SFace, upgrade to AdaFace. Masks: SAM 2.1 (EdgeTAM for live). Excluded unless the team agrees: YOLOE and anything loaded through Ultralytics, BoxMOT (AGPL), YOLO-World (GPL), InsightFace weights (non-commercial), re-ID weights (research-only data), SAM 3.x (custom license) | Proposed | No AGPL or non-commercial weights; all pure PyTorch / ONNX, so they run on the 5070 (sm_120) with PyTorch ≥ 2.7 cu128; best published accuracy per latency. Speeds are vendor numbers on other GPUs, so F-50 must measure them on the 5070 before this is accepted. Write-up: `docs/research/r3-model-shortlist.md` |

## Open questions
- **Compute:** confirm the 5070's VRAM (12 GB desktop or 8 GB laptop). Does anyone else have a GPU worth listing? Are university HPC, club hardware or cloud credits available for tier 3? Should the 5070 host a shared model server, and when would it be on?
- Which LLM for the planner, and its cost per request? Team decision; candidates are Claude Opus 5 (`claude-opus-5`) and Claude Sonnet 5 (`claude-sonnet-5`), ideally compared on the F-09 request set.
- May frames or footage go to a cloud model (e.g. so the planner can see the scene), or only text and metadata?
- Boxes or segmentation masks from the start?
- What does "fix by hand" look like for audio edits?
- Live: virtual camera tooling per OS; browser vs local helper process.
