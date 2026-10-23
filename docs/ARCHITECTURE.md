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
See [The contract](#the-contract-edit-timeline-draft-v0) below. Everything upstream writes to it and everything downstream reads from it. **It's the one interface that lets 10 people build in parallel against stubs.**

### 6. Renderer
- **It's deterministic:** the same timeline and the same source always give the same output.
- **It applies the fail-safe rules:** interpolate boxes between keyframes, hold a lost track's mask for a few frames and grow it, pad uncertain masks more, and on an error mask the whole affected segment and log it, never skip it.
- **Two paths:** **export** (whole file, through ffmpeg) and **frame by frame** (preview and live). They share the same masking and effect code, so preview, export and live look identical.

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

## The contract: edit timeline (draft v0)
> **Draft. Field names will change during sprint 1.** Once code exists, the source of truth becomes the code that defines it (e.g. pydantic models) plus a generated JSON schema; this section then just links to them.

The timeline is an **append-only op log** plus a **derived state** computed by folding the log. Ops are what the agent and UI produce, and the state is what the renderer reads. Undo removes the ops of the last request and re-folds.

```jsonc
// Op (append-only)
{
  "op_id": "op_12",
  "request_id": "r_4",                 // the chat message or UI action that caused it
  "kind": "add_mask",                  // see op kinds below
  "targets": ["trk_3"],                // track / speaker / object ids from the analysis cache
  "params": { "effect": "blur", "padding": 0.15 },
  "time_range": [0.0, 31.5],           // seconds; optional (absent = whole clip)
  "source": "agent"                    // agent | user | rule
}
```

**Op kinds (draft):**

| Kind | Does | Notes |
|---|---|---|
| `add_mask` / `remove_mask` / `set_effect` | visual masking (blur, pixelate, box) | the Knight Hacks core |
| `cut` / `keep` | remove or keep a time range | changes the output timing; the derived state maps source time → output time |
| `mute` / `unmute` | silence a time range, or one speaker's audio | speaker mute needs separation |
| `audio_filter` | e.g. denoise over a range | |
| `remove_object` | inpaint targets out over a range | references a cached inpainting result |
| `add_rule` / `remove_rule` | **standing rule**, e.g. "mask every person not matching identity X" | evaluated per frame; this is what live mode runs on, and it works offline too |

```jsonc
// Derived state (what the renderer reads); shape is a draft
{
  "schema_version": 0,
  "video": { "fps": 30, "width": 1920, "height": 1080, "duration": 60.0 },
  "visual":  { "trk_3": { "label": "person", "effect": "blur", "keyframes": [ { "f": 0, "box": [0,0,0,0], "conf": 0.9, "state": "detected" } ] } },
  "cuts":    [ [10.0, 15.0] ],
  "audio":   [ { "kind": "mute", "speaker": "spk_1", "range": [20.0, 30.0] } ],
  "rules":   [ { "rule_id": "rule_1", "kind": "mask_all_except", "identity": "id_me", "effect": "blur" } ]
}
```

**Contract rules:**
- **It has its own `schema_version`, separate from the app version.** Any change that breaks a reader or writer bumps it.
- **A contract change needs a heads-up to the team before merging**, an entry in the decisions log, and updated examples and tests. `/sync` flags contract changes to everyone.
- **Stubs and tests are built from hand-written example timelines.**

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

- **Standing rules, not one-off edits.** "Only I am visible" becomes `add_rule(mask_all_except, identity=me)`. The same rule can run offline on a file.
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

## Open questions
- **Compute:** confirm the 5070's VRAM (12 GB desktop or 8 GB laptop). Does anyone else have a GPU worth listing? Are university HPC, club hardware or cloud credits available for tier 3? Should the 5070 host a shared model server, and when would it be on?
- Which LLM for the planner, and its cost per request?
- May frames or footage go to a cloud model (e.g. so the planner can see the scene), or only text and metadata?
- Boxes or segmentation masks from the start?
- How is the analysis cache stored (files per video, a database)?
- What does "fix by hand" look like for audio edits?
- Frontend: React + Vite, or plain JS?
- Live: virtual camera tooling per OS; browser vs local helper process.
