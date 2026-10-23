# Roadmap

> **Status (2026-10-23):** the backlog is drafted; **no sprints are planned yet.** Planning the sprints is the next task, and anyone can pick it up: run `/sync`, read this file plus the README and ARCHITECTURE, then fill in [Sprints](#sprints).

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
| F-01 | Repo skeleton: backend package, test runner, stub mode, one-command run | Must | – | idea | Decide the code layout here |
| F-02 | Edit timeline contract v0 in code: op models, fold, undo, generated schema, example timelines | Must | – | idea | ARCHITECTURE "The contract". Unblocks everyone |
| F-03 | Video I/O: probe, decode frames, frame↔time, audio extraction | Must | F-01 | idea | |
| F-04 | Renderer: export path (ffmpeg) + per-frame path, shared effect code | Must | F-02, F-03 | idea | The per-frame path is required for live later |
| F-05 | Agent/planner: template parser + LLM JSON plan + validation + clarify | Must | F-02 | idea | Cloud LLM allowed |
| F-06 | Toolbox scaffolding: typed tool schemas, registry, stub implementations, CLI per tool | Must | F-02 | idea | |
| F-07 | Web UI shell: upload, chat, player, timeline view, export | Must | F-01 | idea | |
| F-08 | Compute setup: `stub`/`local`/`remote` model backends, shareable analysis cache, model download script, per-tier install notes (CPU / small GPU / RTX 5070; CUDA 12.8+ for the 5070) | Must | F-01 | idea | ARCHITECTURE "Compute". Optional: model server on the 5070 |
| F-09 | Benchmark harness: test clips + labels + request set + report script | Must | F-02 | idea | Numbers only from here |

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
- Sprint length and sprint dates aren't decided yet. Pick them when planning.

## Sprints
*None planned yet.* Copy the template below for each sprint, newest at the bottom.

### Sprint template
```markdown
### Sprint N: <goal in a few words>   (YYYY-MM-DD → YYYY-MM-DD)  · version at close: v0.N.0

**Goal:** one or two sentences on what's true at the end of this sprint.

| Person | Feature(s) | Branch | Exit check | Status |
|---|---|---|---|---|
| <name> | F-xx (part) | <name>/<topic> | <how anyone can see it works> | planned |

**Risks / dependencies:** ...
**Closed:** (filled in at sprint close: what shipped, what moved to the next sprint)
```
