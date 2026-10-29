# Vibe Editing Agent

*Working title. Fall 2026 semester build.*

Edit a video by talking to it. You load a clip and type what you want:

- "Blur this person."
- "Blur this person for the first half of the video."
- "Cut this part." / "Cut the part where he talks about the weather."
- "Mute this person." / "Remove the background noise."
- "Remove the people in the background so it looks like they were never there."

An agent turns each request into calls to a fixed toolbox of editing tools. The tools use AI models to find what the request means: the person, the voice, the noise. They write the result into an edit timeline that you can preview, fix by hand and undo. A deterministic renderer applies that timeline to produce the final video. When the AI isn't sure, it asks you or shows you, and never guesses silently.

**Live mode is the stretch goal we care about most:** keep the editor running in the background during a livestream and type "make sure only I am visible, blur everyone else", and it applies to the stream in near real time. See [Live mode](#live-mode-priority-stretch).

> **Status (2026-10-23):** documentation only, no code yet. The technical approach is **tentative**: the stack below lists candidates, not decisions, and even the decisions we've made can be revisited as we learn (see the decisions log in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#decisions-log)). The product goals (scope, live mode, human in the loop) are the stable part. Sprints have not been planned yet; that is the next step (see [docs/ROADMAP.md](docs/ROADMAP.md)).

## Where to read next

| If you want to know... | Read |
|---|---|
| What this is, what's in scope, constraints and limits | this file |
| How it's built, the shared contract, open technical questions, decisions made | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| What's left to build, and the sprints | [docs/ROADMAP.md](docs/ROADMAP.md) |
| How to start and finish work, handoffs, branches, versions | [HANDOFF.md](HANDOFF.md) |
| What changed in each version | [CHANGELOG.md](CHANGELOG.md) |
| What happened most recently, and what's next | the entries in [docs/handoffs/](docs/handoffs/), or run `/b-sync` in Claude Code |
| How Claude should behave in this repo | [CLAUDE.md](CLAUDE.md) |

## Background

**Where the idea came from.** At Knight Hacks 2026 in Orlando (Oct 9-11, 2026), part of this team built an MVP of one feature, censoring: [Edugre/KnightHacks-26](https://github.com/Edugre/KnightHacks-26) ("Blurr"). You typed "blur every license plate and the person in the red shirt around halfway" or drew a box. An agent turned the request into a plan, typed tools ran it, detection models (EgoBlur for faces and plates, YOLOE for everything else, Qwen3-VL for questions like "is this the person in red?") found and tracked the targets, and a deterministic renderer blurred them. That repo is our **reference and inspiration, not our codebase**. We start fresh and reuse its ideas, and its code where it fits.

**What the MVP proved, and what we're keeping:**
- **The agent never edits pixels.** It only picks and calls tools. Models analyze, and the renderer is the only thing that writes video. That makes every edit inspectable and undoable.
- **One shared contract** (there, a mask timeline) let four people build in parallel against stubs. Here it becomes a general **edit timeline**.
- **Plain requests skip the LLM.** A template parser handles simple requests, and anything harder gets **one** LLM call that writes the whole plan as JSON. Calling tools one step at a time took over 2 minutes per request; one plan took 2-7 s.
- **A human stays in the loop.** You can preview, step through low-confidence spots, fix a box by hand, and undo. When unsure, privacy edits masked more, never less.
- **Every number came from a script.** Benchmarks were reproducible, never typed in by hand.

**This build** goes from one feature to a general conversational editor: visual edits, cuts, audio, object removal and, as a stretch, live. The team is **10 people**, organized by **sprints with per-person assignments** (no sub-teams), and **everyone codes heavily with Claude**. The repo is set up so any member can clone it, open Claude Code, and get the full context without asking anyone. That's why the docs are split the way they are, and why handoffs exist ([HANDOFF.md](HANDOFF.md)).

**Decisions made on 2026-10-23** (details in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#decisions-log)):
- **Inference:** hybrid. A cloud LLM may be used for the agent and planner; heavy vision and audio models run on our own GPUs.
- **Product:** a web app with a Python backend.
- **Versioning:** SemVer tags, bumped at each sprint close, starting at `v0.1.0`. Every handoff records commit hashes.
- **Handoffs:** each handoff is its own file in `docs/handoffs/`, read and written through the `/b-sync` and `/b-handoff` Claude commands.

## How it works (short version)

```
 request (chat, click, drawn box, or a standing live rule)
   │
   ▼
 Agent / planner ─── turns the request into a plan of tool calls; never touches pixels
   │
   ▼
 Toolbox ─────────── fixed, typed, deterministic tools (find_people, mute_speaker, cut_range, ...)
   │        └──────► AI modules: vision, audio, generative (detect, track, segment, transcribe, inpaint...)
   ▼
 Edit timeline ───── the shared contract: an op log of edits plus a derived state
   │        └──────► human in the loop: preview, review low-confidence spots, fix by hand, undo
   ▼
 Renderer ────────── deterministic; applies the timeline to a file (export) or to frames (live)
```

Full version: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Scope

### Must have
The core loop, end to end, on real footage:
- **Upload a video, chat a request, preview the result, fix it, undo, export.**
- **Visual privacy edits:** blur, pixelate or black-box a person, face, license plate or named object, for the whole clip or a time range ("the first half", "after 0:30").
- **Targeting by description:** "the person in the red shirt", "everyone except me", "only the woman on the left".
- **Cuts:** remove or keep a time range ("cut 0:10 to 0:15", "cut the first 5 seconds").
- **Audio basics:** mute a time range, and reduce background noise.
- **Human in the loop:** the agent says what it did in plain words. Low-confidence results are flagged for review. Every target can be fixed by hand (click it, drag its box, change its effect), and every change can be undone.
- **A benchmark harness** that measures quality and speed from committed test data.

### Should have
- **Mute a specific person:** speaker separation and diarization ("mute the guy on the right").
- **Cut by content:** using the transcript ("cut where she talks about the weather") or by what's on screen ("cut the part with the dog").
- **Draw or click to target:** a drawn box or clicked object becomes a tracked target.
- **Object or person removal with background fill:** video inpainting, so the scene looks like they were never there. This is high-impact but hard; quality on long or fast-moving shots is the open risk.

### Live mode (priority stretch)
**The scenario:** you're streaming from your apartment and someone walks in. You type "make sure only I am visible, blur everyone else", and from then on the stream going out has everyone but you blurred, within a short delay.

**Why it's only a stretch:** the whole detect, track and mask pipeline has to keep up with the frame rate. At Knight Hacks, one face + plate + person detection pass on a Strix Halo iGPU took about **134 ms per 720p frame**. Live looked **feasible at 720p behind about a 0.8 s delay** (detect every few frames and track in between), but it was measured, never built. "Only I am visible" also needs **identity**: the app has to know which person is you (enroll yourself once, then anyone who isn't you gets masked; unsure counts as "not you", so they are masked).

**Why it's a priority anyway:** it's the most compelling use of the product. So **we design for it from day one, even before building it**:
- the contract supports **standing rules** ("from now on, blur anyone who isn't me"), not only one-off edits;
- the renderer and the AI modules expose a **per-frame path**, not only a whole-file path;
- the cloud LLM is used only when a rule is set, never per frame, so live never waits on a network call;
- a **live feasibility spike** (measuring latency on our actual hardware) is in the backlog early, so we know as soon as possible whether it's realistic.

Output would likely go through a virtual camera that OBS or another streaming app picks up. Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#live-mode).

### Other stretches
- Photos, treated as one-frame videos (KnightHacks already did this).
- Captions and subtitles from the transcript.
- Multi-clip projects: join clips, reorder.
- More generative edits: replace a background, change an object.

### Out of scope
- Training large models from scratch. Fine-tuning a small model is fine if a benchmark shows we need it.
- Collecting, storing or training on explicit content.
- Becoming a full professional editor (multi-track compositing, color grading, effects plugins).
- Mobile apps.

## Constraints
These hold whatever stack we pick.

| Constraint | What it means |
|---|---|
| **The agent never touches pixels or audio samples** | It can only call toolbox tools, which write ops to the edit timeline. Only the renderer produces output. |
| **Every AI decision is reviewable** | Anything a model decided is visible in the timeline with its confidence. A human can override it, and every change is undoable. |
| **Fail safe** | Privacy edits fail closed: unsure means masked. Other edits ask or preview instead of guessing, e.g. "I found two people talking about the weather, which one?". Pipeline errors are reported, never silently skipped. |
| **Hybrid inference** | A cloud LLM is allowed for understanding requests and planning. Heavy vision and audio models run on our GPUs. Whether footage or frames may be sent to a cloud model is an **open question**; by default the LLM gets text and metadata only. |
| **Works on any team machine** | Hardware varies from CPU-only laptops to an RTX 5070. Every AI module can run as a stub (replaying cached results), locally, or by calling a model server on another machine, so nobody is blocked by their hardware. See "Compute" in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#compute-everyone-can-work-whatever-their-machine). |
| **Secrets and media stay out of git** | API keys go in `.env` (gitignored). Videos, model weights and outputs are gitignored; clip sources and consent are recorded in a manifest. |
| **Footage consent** | Test and demo clips are our own footage, filmed with the people in them agreeing, or properly licensed public video. |
| **Licenses** | Check every model's license before adopting it. Some popular ones (e.g. Ultralytics YOLO, AGPL-3.0) force the repo to stay open source. |
| **Numbers come from scripts** | Any quality or speed number we quote is produced by the benchmark harness, with the hardware stated. |

## Tentative tech stack
**Nothing here is decided.** These are candidates to evaluate. When one is chosen, it goes in the decisions log in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#decisions-log).

| Layer | Candidates | Notes |
|---|---|---|
| Backend | Python 3.12, FastAPI | Worked well at Knight Hacks |
| Video I/O and render | ffmpeg, OpenCV, PyAV | PyAV or OpenCV for the per-frame path (live) |
| Agent / planner LLM | Claude (Anthropic API); a local open model as fallback | Cloud allowed (hybrid); typed tool schemas, JSON plan |
| Detection | YOLOE / YOLO-World, Grounding DINO, EgoBlur (faces, plates) | Open-vocabulary detection for "find X" |
| Segmentation and tracking | SAM 2 / SAM 3, BoT-SORT / ByteTrack | Pixel-accurate masks, better tracking |
| Visual questions | Qwen-VL family, other VLMs | "Is this the person in red?" |
| Identity (live "only me") | Face embeddings (e.g. InsightFace / ArcFace), person re-ID | Needs an enrollment step |
| Object removal | ProPainter, E2FGVI, diffusion-based video inpainting | Heaviest compute in the product |
| Speech | Whisper / faster-whisper (transcripts, word timestamps) | Enables cut-by-transcript |
| Speakers | pyannote (diarization), speech separation models | "Mute this person" |
| Noise | DeepFilterNet, Demucs, RNNoise | "Remove the background noise" |
| Frontend | **Decided (D-009):** React + Vite + TypeScript | Player, chat, timeline, review; types generated from the backend's OpenAPI schema |
| Environment and packaging | **Decided (D-010):** uv (pinned Python, committed `uv.lock`) | GPU packages in an optional dependency group |
| Compute | Reference machine: an RTX 5070 (Alex); everyone else from CPU-only laptops up; rented GPU optional | Prefer models with small or quantized variants. See ARCHITECTURE "Compute" |

## Limitations and risks we expect
- **Compute is the main limit.** Our strongest known GPU is one RTX 5070, and several teammates have weak GPUs or none. Video models (segmentation, inpainting) are slow and memory-hungry, so long clips at 1080p may take a while, models have to take turns in VRAM, and live mode may only work at lower resolution.
- **Object removal quality varies.** Inpainting looks convincing on short, static shots and much worse on long or fast-moving ones.
- **Descriptive targeting is error-prone.** Detectors handle "person" well but not "the guy in the back on the left". Those requests need a VLM check plus human review.
- **Audio separation is imperfect.** Muting one speaker who talks over another may leave artifacts.
- **Cloud LLM costs and limits.** API calls cost money and have rate limits, so keep the template parser for plain requests.
- **Masks are boxes until segmentation lands.** Boxes blur some background around each target.

## Repository layout
The backend and frontend live side by side, each with its own toolchain. The code layout and what goes in each folder are described in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#code-layout).

```
backend/               Python backend (uv): pyproject.toml, uv.lock, src/vibe/, tests/
frontend/              React + Vite + TS UI (npm), created by F-07
package.json           root scripts: npm run dev / test / lint
.github/workflows/     CI, one workflow per side
README.md              this file
CLAUDE.md              rules and reading map for Claude (loaded automatically)
HANDOFF.md             how we work: branches, handoffs, versions
CHANGELOG.md           what changed per version
docs/
  ARCHITECTURE.md      layers, contract, live mode, decisions, open questions
  ROADMAP.md           feature backlog and sprints
  handoffs/            one file per handoff (current sprint); archive/ holds past sprints
.claude/skills/        /b-sync, /b-handoff, /b-release for Claude Code
```

## Getting started
1. Clone the repo and open it in Claude Code.
2. Run `/b-sync`. Claude summarizes the current state, what's new and what's next.
3. Pick your assignment from the current sprint in [docs/ROADMAP.md](docs/ROADMAP.md), make a branch, and work.
4. Run `/b-handoff` when you stop, then open a PR.

### Run the app
You need [uv](https://docs.astral.sh/uv/) and Node 22 LTS. No GPU is needed: AI modules default to the `stub` backend.

```bash
npm install            # once: root scripts, then the frontend's packages
npm run dev            # the UI at http://localhost:5173 and the backend at http://localhost:8000 (API docs at /docs)
npm test               # backend tests, then frontend tests
npm run lint           # Ruff lint and format check, then oxlint and the TypeScript check
```

The UI runs against a mock API by default, so it works before the backend's routes exist. Set `VITE_API_MODE=http` to use the real backend through the `/api` proxy. Each side also has its own scripts: `test:backend`, `test:frontend`, `lint:backend`, `lint:frontend`.

The UI's API types are generated from the backend's OpenAPI spec, which is committed as `backend/src/vibe/api/openapi.json` so the frontend never needs Python. After changing a route or a response model, regenerate both and commit them:

```bash
uv run --directory backend python -m vibe.api   # writes openapi.json (--check only checks it)
npm --prefix frontend run gen:api               # writes frontend/src/api/generated/ from it
```

A backend test fails if the spec is stale, and frontend CI fails if the generated types are.

The video tests generate tiny clips with the [ffmpeg](https://ffmpeg.org/) command line, so install it to run them: `brew install ffmpeg` (Mac), `winget install ffmpeg` (Windows), or `sudo apt install ffmpeg` (Linux). Without it those tests are skipped; CI installs it and fails instead. The app itself reads video through PyAV, which bundles its own ffmpeg libraries. To check a clip by hand: `uv run --directory backend python -m vibe.video probe path/to/clip.mp4`.

To try a toolbox tool by hand: `uv run --directory backend python -m vibe.toolbox --help` lists the tools, and `... <tool> --help` shows each one's arguments and an example.

To work on the backend directly: `cd backend && uv sync`, then `uv run pytest`. Set `VIBE_BACKEND=local` or `remote` to use real models once they exist.
