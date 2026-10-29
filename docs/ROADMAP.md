# Roadmap

> **Status (2026-10-28):** sprints are weekly, and **demo day is 2026-12-04**. **Sprint 1 closed as v0.1.0 (2026-10-28). Sprint 2 (2026-10-27 → 2026-11-03): first end-to-end path.** See [Sprints](#sprints).

## How this file works
- **Backlog** lists every feature we know of, with an ID. IDs never change or get reused, so handoffs, branches and PRs can refer to them (`F-32`).
- **Sprints** pick features from the backlog and assign each one to a person.
- **Status** in the backlog is updated by whoever works on a feature, in their PR. Only edit the rows you own; that keeps merges clean.
- **New ideas** go to the bottom of the right section with the next free ID, status `idea`.

Priorities: **Must**, **Should**, **Stretch★** (live: top-priority stretch), **Stretch**.
Status: `idea` → `planned` → `in progress` → `done` (or `dropped`, with a reason).

## Backlog

### Foundation (needed before features can be built in parallel)
| ID | Feature | Priority | Depends on | Status | Notes |
|---|---|---|---|---|---|
| F-01 | Repo skeleton: backend package, test runner, stub mode, one-command run | Must | – | done | Layout decided: D-011, ARCHITECTURE "Code layout" |
| F-02 | Edit timeline contract v0 in code: op models, fold, undo, generated schema, example timelines | Must | – | done | ARCHITECTURE "The contract". Unblocks everyone |
| F-03 | Video I/O: probe, decode frames, frame↔time, audio extraction | Must | F-01 | done | |
| F-04 | Renderer: export path (ffmpeg) + per-frame path, shared effect code | Must | F-02, F-03 | idea | The per-frame path is required for live later |
| F-05 | Agent/planner: template parser + LLM JSON plan + validation + clarify | Must | F-02 | in progress | Cloud LLM allowed |
| F-06 | Toolbox scaffolding: typed tool schemas, registry, stub implementations, CLI per tool | Must | F-02 | in progress | |
| F-07 | Web UI shell: upload, chat, player, timeline view, export | Must | F-01 | done | |
| F-08 | Compute setup: `stub`/`local`/`remote` model backends, shareable analysis cache, model download script, per-tier install notes (CPU / small GPU / RTX 5070; CUDA 12.8+ for the 5070) | Must | F-01 | in progress | ARCHITECTURE "Compute". Optional: model server on the 5070 |
| F-09 | Benchmark harness: test clips + labels + request set + report script | Must | F-02 | idea | Numbers only from here |
| F-64 | API routes: upload, request, timeline state | Must | F-06 | planned | Shared file. Starts from the routes F-07's mock uses |

### Visual edits
| ID | Feature | Priority | Depends on | Status | Notes |
|---|---|---|---|---|---|
| F-10 | Find and track people, faces, plates, named objects (cached per video) | Must | F-03, F-08 | idea | Knight Hacks: EgoBlur + YOLOE |
| F-11 | Blur / pixelate / black-box targets | Must | F-04, F-10 | idea | |
| F-12 | Time-bounded edits ("first half", "after 0:30") | Must | F-05 | idea | `resolve_time` |
| F-13 | Targeting by description ("the person in red", "everyone except me") | Must | F-10 | idea | VLM yes/no per track |
| F-14 | Click / draw a box to target, tracked through the clip | Should | F-10 | idea | |
| F-15 | Segmentation masks instead of boxes | Should | F-10 | idea | SAM 2 / SAM 3 |
| F-16 | Remove a person or object with background fill (video inpainting) | Should | F-15 | idea | Heaviest compute; preview before applying |

### Cuts
| ID | Feature | Priority | Depends on | Status | Notes |
|---|---|---|---|---|---|
| F-20 | Cut / keep a time range | Must | F-04 | idea | Source→output time mapping in the derived state |
| F-21 | Cut by transcript ("cut where she talks about X") | Should | F-31 | idea | |
| F-22 | Cut by on-screen content ("cut the part with the dog") | Stretch | F-10 | idea | |

### Audio
| ID | Feature | Priority | Depends on | Status | Notes |
|---|---|---|---|---|---|
| F-30 | Mute a time range | Must | F-04 | idea | |
| F-31 | Transcription with word timestamps | Should | F-03 | idea | Whisper family |
| F-32 | Who spoke when (diarization) + mute a specific speaker | Should | F-31 | idea | Needs separation if speakers overlap |
| F-33 | Background noise reduction | Must | F-03 | idea | |
| F-34 | Remove a specific sound ("the fan noise", "the music") | Stretch | F-33 | idea | |

### Human in the loop
| ID | Feature | Priority | Depends on | Status | Notes |
|---|---|---|---|---|---|
| F-40 | Agent reports what it did in plain words + raw tool calls | Must | F-05 | idea | |
| F-41 | Review queue for low-confidence results | Must | F-02, F-07 | idea | Knight Hacks "Check" |
| F-42 | Hand fixes: select, change effect, remove, drag/correct box | Must | F-07, F-11 | idea | Through the same toolbox |
| F-43 | Undo per request + version history | Must | F-02 | idea | |
| F-44 | Preview-before-apply for heavy edits (inpainting, cuts) | Should | F-04 | idea | |

### Live mode (top-priority stretch)
| ID | Feature | Priority | Depends on | Status | Notes |
|---|---|---|---|---|---|
| F-50 | **Live feasibility spike**: per-frame latency of the detector + tracker + identity check on the RTX 5070, 720p/1080p | Stretch★ | F-08 | idea | **Do early.** Its result decides whether live moves up |
| F-51 | Standing rules in the contract and renderer (also usable offline) | Stretch★ | F-02, F-04 | idea | Contract v0 should already include rules |
| F-52 | Identity enrollment + "only I am visible" (everyone else masked, unsure = masked) | Stretch★ | F-10 | idea | Embeddings stored locally |
| F-53 | Live pipeline: capture → per-frame analysis → render → delay buffer | Stretch★ | F-50, F-51 | idea | |
| F-54 | Virtual camera output for OBS / streaming apps | Stretch★ | F-53 | idea | |
| F-55 | Set or change rules mid-stream from chat | Stretch★ | F-05, F-53 | idea | The LLM is never on the per-frame path |

### Other stretches
| ID | Feature | Priority | Depends on | Status | Notes |
|---|---|---|---|---|---|
| F-60 | Photos as one-frame videos | Stretch | F-11 | idea | Done before at Knight Hacks |
| F-61 | Captions / subtitles from the transcript | Stretch | F-31 | idea | |
| F-62 | Multi-clip projects (join, reorder) | Stretch | F-20 | idea | |
| F-63 | Background replacement and other generative edits | Stretch | F-15 | idea | |

## Notes for whoever plans the sprints
- **There are 10 people and no sub-teams.** Each sprint assigns features (or parts of them) to individuals. Two people on one feature should split it by files, not share them.
- **F-02 (the contract) comes first, or very early.** Until it exists, people can't build against stubs in parallel. A good first sprint gets the foundation (F-01–F-09) far enough that everything else can start against stubs, as Knight Hacks did in its first hour.
- **Schedule F-50 (live feasibility) early**, even though live is a stretch. It's the top-priority stretch, and we need its numbers to know whether it's realistic.
- **Compute (F-08) early.** Most of the team will be on laptops (ARCHITECTURE "Compute"). Assign GPU-heavy work (model evaluation, benchmarks, F-50) to whoever has a GPU or access to the 5070; everyone else builds on stubs and the shared analysis cache. Ask each person what GPU they have when assigning.
- **Every feature should end with an exit check:** a command or demo someone can run to see it work, recorded in the handoff.
- Sprints are one week long, Tuesday → Tuesday, starting 2026-10-20. Each Tuesday closes one sprint and starts the next. The last sprint is cut short to end the day before demo day (see [Schedule](#schedule)).
- **Members claim cards; nobody is assigned.** The planner lists the cards, and each person picks the ones they want (see [Claiming a card](#claiming-a-card)).

## Sprints
### Schedule
Weekly sprints, Tuesday → Tuesday: each Tuesday closes one sprint and starts the next. Each close tags the next minor version (`/b-release`). **Demo day is Friday 2026-12-04.**

| Sprint | Dates | Version at close | Rough focus (tentative, re-plan each week) |
|---|---|---|---|
| 1 | 2026-10-20 → 2026-10-27 | v0.1.0 | Foundation against stubs + settle the open stack questions |
| 2 | 2026-10-27 → 2026-11-03 | v0.2.0 | First end-to-end path: request → plan → toolbox → timeline → render (renderer F-04, planner F-05) |
| 3 | 2026-11-03 → 2026-11-10 | v0.3.0 | Core Must edits (blur, cut, mute, noise reduction) on real models |
| 4 | 2026-11-10 → 2026-11-17 | v0.4.0 | Human in the loop (review queue, hand fixes, undo) + Should features. Decide on live mode, based on the F-50 numbers |
| 5 | 2026-11-17 → 2026-11-24 | v0.5.0 | Stretches (live first) |
| 6 | 2026-11-24 → 2026-12-01 | v0.6.0 | Stretches + hardening. **Short week: Thanksgiving is 2026-11-26** |
| 7 | 2026-12-01 → 2026-12-03 | v0.7.0 | **3-day demo sprint:** feature freeze, bug fixes, demo script and clips, rehearsal |

### Claiming a card
- Each row in a sprint table is a card. An `_open_` owner means nobody has claimed it yet.
- To claim a card, put your name and branch in its row: either in a one-line PR, or in the first PR for that card. Say so in the team chat too, so two people don't start the same card.
- A card can be split between two people, by files rather than by sharing them. Add a row for each part, e.g. `F-07 (chat panel)`.
- **Research cards** end in a decision: a new row in the decisions log in [ARCHITECTURE.md](ARCHITECTURE.md#decisions-log) (proposed to the team before it's accepted), plus a short write-up in your handoff entry. Any number in a write-up comes from a script, with the hardware stated.

### Sprint 1: foundation + stack decisions   (2026-10-20 → 2026-10-27)  · version at close: v0.1.0

**Goal:** by Tuesday 2026-10-27, the open stack questions are decided and logged. Also by Tuesday, the contract v0 exists in code, with a repo skeleton anyone can install with uv and test in stub mode. That way Sprint 2 can build the first end-to-end path in parallel against stubs.

**Sprint lead:** Alex or Eduardo.

**Research (settle open questions)**
| Card | Owner | Branch | Exit check | Status |
|---|---|---|---|---|
| R-1 Planner LLM: **open choice** between any AI provider's hosted model (Anthropic, OpenAI, Google, ...) or an open-weight model run on our own hardware. Shortlist a few, then compare them on a draft request set of ~20 requests (shared with F-09). Also decide which requests the template parser should take | _open_ | | Script output with plan validity, latency and cost per request (or the hardware needed, for open models) for each candidate → decision row | planned |
| R-2 Project storage and analysis cache: one folder per project (`timeline.jsonl` + `cache/` keyed by video hash) vs a database | alex | alex/storage | Decision row, with the folder layout written down for F-02/F-08 | done |
| R-3 Detection, tracking and identity model shortlist for F-10/F-50/F-52, **with licenses** (e.g. RT-DETR, YOLOX, EgoBlur, YOLOE, SAM 2, face embedders). Avoid AGPL unless the team agrees | _open_ | | Shortlist table (model, task, license, weights size, CUDA 12.8 support) → decision row | planned |
| R-4 Data policy and target shape: may frames go to a cloud model, or only text and metadata? Boxes or segmentation masks from the start? | _open_ | | Two decision rows. Both open questions closed in ARCHITECTURE | planned |

**Build (foundation)**
| Card | Owner | Branch | Exit check | Status |
|---|---|---|---|---|
| F-01 Repo skeleton: backend package, uv project (D-010), pytest in stub mode, lint (Ruff), GitHub Actions CI, one-command run | eduardo | eduardo/repo-skeleton | Fresh clone → `uv sync` → tests pass on CI with no GPU | done |
| F-02 Contract v0 in code: op models, fold, undo, generated JSON schema, example timelines. **Highest priority: everyone in Sprint 2 builds on it** | alex | alex/contract-v0 | Example timelines validate. Fold and undo tests pass. `schema_version` set | done |
| F-03 Video I/O: probe, decode frames, frame↔time, audio extraction | eduardo | eduardo/video-io | Tests pass on a tiny clip that ffmpeg generates during the test (no video is committed) | done |
| F-06 Toolbox scaffolding: typed tool schemas, registry, stub tools, CLI per tool (starts from the F-02 draft) | _open_ | | `<tool> --help` lists tools. A stub tool writes a valid op to an example timeline | planned |
| F-07 Web UI shell: React + Vite + TS (D-009) in `frontend/`, with upload, chat, player and timeline view placeholders against a mocked API, and a Vite `/api` proxy to the backend. **Extra:** generate TS types from the backend's OpenAPI spec into `frontend/src/api/generated/`, plus a CI check that fails if they're out of date (D-009) | eduardo | eduardo/web-ui-shell, eduardo/openapi-types (extra) | `npm run dev` shows the shell. Vitest passes. Extra: `npm run gen:api` regenerates the types, and CI fails on a stale type | done |
| F-08 Compute setup: `stub`/`local`/`remote` backend interface, model download script, install notes per tier, **plus the team GPU survey** (who has what, and the 5070's VRAM) | _open_ | | Stub backend replays a cached result in a test. Survey table added to ARCHITECTURE "Compute" | planned |
| F-09 Benchmark harness, first cut: consented test clips + manifest, label format, request set, report script | _open_ | | `report` script runs on the stub backend and prints a table. Clips are listed in the manifest (not committed) | planned |
| F-50 Live feasibility spike: per-frame latency of detector + tracker + identity check at 720p/1080p, **on the RTX 5070** (needs whoever has it, or remote access) | _open_ | | Script output with fps and latency per stage, hardware stated. Uses R-3 candidates | planned |

**Risks / dependencies:**
- F-02 gates F-06 and all of Sprint 2. If it isn't merged by midweek, F-06 builds on the draft branch.
- F-01 builds on the proposed Sprint 1 stack (see the stack-sprint1 handoff). The team is close to settling it, and any change gets logged as a decision row.
- R-1 needs the request set that F-09 drafts. R-1 can write its own draft set first, and F-09 adopts it.
- F-50 can only run on the RTX 5070. If nobody with access claims it, it slips to Sprint 2, which still leaves time for the Sprint 4 live decision.
- Shared files (contract, dependency files, CI): post in the team chat before merging (HANDOFF.md, "Shared files").
- F-04 (renderer) and F-05 (planner) are left for Sprint 2 on purpose: they depend on F-02 and R-1. Anyone who finishes early can draft them against the contract.

**Closed (v0.1.0, 2026-10-28):** shipped F-01 repo skeleton, F-02 contract v1, F-03 video I/O, F-07 web UI shell (plus generated API types) and R-2 storage (D-014 still Proposed). Moved to Sprint 2: R-1, R-3, R-4, F-06, F-08, F-09, F-50 (F-50 still needs the RTX 5070).

### Sprint 2: first end-to-end path   (2026-10-27 → 2026-11-03)  · version at close: v0.2.0

**Goal:** by Tuesday 2026-11-03, a request typed in the UI goes through the planner and the toolbox, lands as ops in the edit timeline, and comes out as a render. Stub models are fine.

**Sprint lead:** Alex.

**Order:** first F-06 and F-64 (everything else plugs into them); then F-04, F-05 and R-1; alongside, F-08, R-3 → F-50 and R-4; F-09 can slip.

**Research (carried over from Sprint 1)**
| Card | Owner | Branch | Exit check | Status |
|---|---|---|---|---|
| R-1 Planner LLM: any hosted provider or an open-weight model, compared on the ~20-request set (shared with F-09). Also which requests the template parser takes | paul | | Script output with plan validity, latency and cost per request (or hardware needed) → decision row; planner candidates updated in README and ARCHITECTURE | in progress |
| R-3 Detection, tracking and identity model shortlist for F-10/F-50/F-52, **with licenses**. Avoid AGPL unless the team agrees | jonathan | jonathan/r3-model-shortlist | Shortlist table (model, task, license, weights size, CUDA 12.8 support) → decision row | in progress |
| R-4 Data policy and target shape: may frames go to a cloud model? Boxes or masks from the start? | _open_ | | Two decision rows. Both open questions closed in ARCHITECTURE | planned |

**Build**
| Card | Owner | Branch | Exit check | Status |
|---|---|---|---|---|
| F-06 Toolbox scaffolding: typed tool schemas, registry, stub tools, CLI per tool. **Priority: first.** Shared file (tool names and arguments) | pablo | pablo/f06-toolbox | `<tool> --help` lists tools. A stub tool writes a valid op to an example timeline | in progress |
| F-64 API routes: `POST /api/projects`, `POST /api/projects/{id}/requests`, `GET /api/projects/{id}/state`, one request at a time per project. **Priority: first.** Shared file (API routes) | _open_ | | With `VITE_API_MODE=http` the UI uploads, sends a request and shows the real timeline. Route tests pass in stub mode. OpenAPI spec and UI types regenerated | planned |
| F-04 Renderer: export path (ffmpeg) + per-frame path sharing the effect code; nothing on the per-frame path calls the network. Can be split export / per-frame | _open_ | | A test renders a generated clip with a blur, a cut and a mute from an example timeline and checks the output. No GPU | planned |
| F-05 Planner: template parser + plan validation + clarify reply; the LLM plan plugs in after R-1 | pablo | pablo/f05-planner-v2 | Plain requests → valid tool calls (or clarify) with no network and no GPU | in progress |
| F-08 Compute setup: `stub`/`local`/`remote` backend interface, model download script, install notes per tier, **plus the team GPU survey**. Cache via `Storage.cache_path()` | eduardo | eduardo/compute-setup | Stub backend replays a cached result in a test. Survey table added to ARCHITECTURE "Compute" | in progress |
| F-09 Benchmark harness, first cut: consented clips + manifest, label format, request set, report script. Can slip | jean | | `report` runs on the stub backend and prints a table. Clips listed in the manifest, not committed | in progress |
| F-50 Live feasibility spike on the **RTX 5070**: per-frame latency of detector + tracker + identity check at 720p/1080p, with R-3 candidates | _open_ | | Script output with fps and latency per stage, hardware stated | planned |

**Risks / dependencies:**
- F-06 and F-64 gate the end-to-end path. Until F-06 lands, F-05 and F-64 build against a stub registry.
- F-50 needs the RTX 5070 (or remote access) and R-3's shortlist. Its numbers decide live mode in Sprint 4.
- R-1 and F-09 share the request set; F-05 uses it too.
- Shared files (tool names, API routes, OpenAPI spec): post in the team chat before merging.

**Closed:** (filled in at sprint close: what shipped, what moved to the next sprint)

### Sprint template
Copy this for each new sprint, above this template, so the newest sprint is last.

```markdown
### Sprint N: <goal in a few words>   (YYYY-MM-DD → YYYY-MM-DD)  · version at close: v0.N.0

**Goal:** one or two sentences on what's true at the end of this sprint.

**Sprint lead:** <name>

| Card | Owner | Branch | Exit check | Status |
|---|---|---|---|---|
| F-xx (part) or R-x <question> | _open_ or <name> | <name>/<topic> | <how anyone can see it works> | planned |

**Risks / dependencies:** ...
**Closed:** (filled in at sprint close: what shipped, what moved to the next sprint)
```
