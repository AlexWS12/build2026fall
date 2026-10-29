# R-4: Data Policy and Target Shape

**Bottom line:** Keep footage on machines the team controls. The planner LLM gets text and metadata only. Frames, crops and audio may go to a teammate's machine (e.g. the RTX 5070 through a `remote` backend), but they go to a third-party cloud service only when a project explicitly opts in, and only for clips whose consent covers it. Biometric data (face embeddings) never leaves the machine that made it and isn't written to the shared analysis cache. Live mode sends nothing over the network. For targets, keep **boxes** as the v1 shape the whole pipeline uses, and add **segmentation masks later (F-15)** as an optional reference on each keyframe, with the box kept as the fallback. Proposed as **D-017** and **D-018** in `docs/ARCHITECTURE.md`.

## TL;DR

- **D-017 Data policy (local-first):**
  - Planner LLM: text and metadata only (request, video metadata, tool summaries, transcript excerpts). No pixels, no audio.
  - Team machines (same machine, or a teammate's machine over a private network): anything except biometric data from other machines.
  - Third-party cloud (LLM/VLM APIs, rented GPUs, Colab/Kaggle): text and metadata always; media only with a per-project opt-in, off by default, and only for clips whose consent covers cloud processing. Never biometric data.
  - Live mode: nothing on the per-frame path calls the network (already a rule).
  - Bystander biometrics: computed in memory, matched only against the enrolled user's template, then discarded. Tracks store the match result, not the embedding.
- **D-018 Target shape (boxes now, masks later):**
  - v1: every target is a track of normalized boxes (already in contract v1, D-013). The renderer masks the padded box.
  - Later (F-15): an optional mask reference per keyframe, pointing at a mask file in the analysis cache (e.g. COCO RLE). Masks never go in the op log or inline in tracks.
  - The box stays required. When a frame's mask is missing or unsure, the renderer masks the padded box (fail closed).
  - The op log is unaffected. Adding the field to `Keyframe` still breaks old readers of new tracks (`extra="forbid"`), so F-15 is a shared-file change and likely a `schema_version` bump.

---

## Question 1: May frames or footage go to a cloud model?

### What's already decided
- **D-002 (Accepted):** a cloud LLM is allowed for planning; heavy vision and audio models run on our GPUs.
- **ARCHITECTURE, "Agent / planner":** the LLM receives the request, video metadata and tool summaries, "not footage by default". Still open there: whether the planner also sees a few frames.
- **README, "Constraints":** test and demo clips are our own consented footage or licensed public video; consent is recorded in a manifest. Whether footage may go to a cloud model is listed as open.
- **CLAUDE.md / ARCHITECTURE, "Live mode":** nothing on the per-frame path calls the network.

So the planner case was half-settled. What nothing on `main` covers is every **other** way footage can leave a machine.

### Where footage could leave the machine
| Path | Who builds it | What would leave | Today |
|---|---|---|---|
| Planner LLM sees frames ("look at the scene") | F-05 / R-1 | Frames | "Not by default"; frames still an open question |
| `remote` backend to a model server | F-08 | Frames, crops, audio | Allowed by design, destination unspecified |
| Tier 3 rented / cloud GPU (Colab, Kaggle, rented instance) | F-08, anyone | Whole clips | Listed as optional, no policy |
| Cloud VLM for attribute questions ("the person in red", "the part with the dog") | F-13, F-22 | Track crops, frames | Knight Hacks pattern: VLM yes/no per track. Local VLM planned on the 5070 |
| Generative edits (background replacement, inpainting) via a hosted API | F-16, F-63 | Frames, masks | Not designed yet |
| Cloud transcription / diarization / sound removal | F-31, F-32, F-34 | Audio | Local Whisper / DeepFilterNet planned |
| Logs, debugging, error reports | everyone | Whatever gets logged | No rule |
| Live mode | F-53 | Nothing (per-frame path is offline) | Already a rule |

### Why not just allow it
1. **The people in the footage aren't all users.** Bystanders never agreed to anything. Our own test clips are consented, but the product's whole purpose is editing footage with other people in it, and demo-day uploads won't come with a consent manifest.
2. **Face embeddings are regulated as biometric data in some places.** Illinois' BIPA counts a "scan of face geometry" as a biometric identifier and requires informed written consent before collecting one, plus a public retention and destruction schedule.\[1\]\[2\] Under the GDPR, biometric data used "for the purpose of uniquely identifying a natural person" is special-category data, prohibited by default unless an Article 9(2) exception applies.\[3\]\[4\] Our identity step (F-52, "everyone except me") computes face embeddings for every visible person, so it is exactly this kind of processing. We are a course project, not a company, and this is not legal advice, but the cheapest way to stay well clear is to keep embeddings local and transient.
3. **Live mode can't use the network anyway.** Anything we only get working through a cloud API won't carry over to live mode, our top-priority stretch.
4. **The plan already runs models locally.** D-002 and D-015 put detection, tracking, identity and masks on our own GPUs. Saying "no third-party media by default" costs little today.
5. **Provider terms change and are hard to verify.** For example, secondary sources disagree about how long Anthropic keeps commercial API inputs (30 days on Anthropic's own help page as summarized by search, 7 days on some third-party sites), while all agree API data isn't used for training by default.\[5\]\[6\] A policy that depends on one provider's current terms needs re-checking every time they change; a destination-based policy doesn't.

### Why not forbid it completely
- **Tier 3 exists for a reason.** Video inpainting on long clips and large VLMs may not fit in the 5070's 8–12 GB (ARCHITECTURE, "Compute"). A flat ban would block F-16 / F-63 on long clips and some F-13 / F-22 quality, with no escape hatch.
- **Our own test clips can be consented for it.** Teammates filming themselves can agree to cloud processing; that's different from a stranger's upload.

### The policy (proposed as D-017)
**Data classes:**
- **Text and metadata:** request text, video metadata (duration, fps, resolution), tool summaries (track ids, labels, times, counts, confidences), transcript excerpts.
- **Media:** frames, crops, clips, masks drawn from frames, audio.
- **Biometric:** face embeddings and identity templates.

**Destinations:**
| Destination | Text and metadata | Media | Biometric |
|---|---|---|---|
| The same machine | yes | yes | yes, enrolled user's template only; bystander embeddings in memory only |
| A team-controlled machine (teammate's computer, the 5070 over a private network like Tailscale) | yes | yes | no |
| A third-party service (LLM/VLM API, rented GPU, Colab, Kaggle, any hosted model) | yes | **only with the project's opt-in** and only for clips whose recorded consent covers cloud processing | no |
| Live per-frame path | no network at all | no network at all | no network at all |

**Rules that follow:**
- **The planner never sees pixels.** It gets the tool summaries, and attribute questions ("is this the person in red?") are answered by a VLM on a team machine, which returns yes / no / unsure per track (the Knight Hacks pattern). This closes the "whether the planner also sees a few frames" question in ARCHITECTURE.
- **Transcript excerpts count as text.** F-21 (cut by transcript) needs the planner to read what was said. Send the excerpts the request needs, not whole transcripts by habit. This is the one grey area: speech is personal data too, but without it F-21 can't work through the planner.
- **Bystander embeddings are transient.** Compute them, match them against the enrolled user's template only, store the result (`Track.identity` or null) and discard the embedding. Never write embeddings to the shareable analysis cache (`cache/<sha256>/`), since sharing that cache means copying it to teammates. The enrolled user's template stays on the machine where they enrolled. This builds on the R-3 handoff's privacy note.
- **The opt-in fails closed.** Any code path that would send media to a non-team host checks the project's opt-in first. If it's off, the edit stops and tells the user what it would have needed. It never silently falls back to sending, and never silently skips the edit (CLAUDE.md, "Fail safe").
- **Logs carry ids, not media.** Logs, error reports and LLM request dumps may contain text and metadata; they never contain frames, crops, audio or embeddings.

**What it costs:**
- F-13 / F-22 / F-63 depend on what a VLM on the 5070 can do. If F-50 or F-09 show it isn't good enough, the fix is a project opt-in for our own consented clips, or a new decision row, not a quiet exception.
- Someone has to build the opt-in check and the "team machine" allowlist (below).

### Who implements what (not part of R-4)
These are proposals for the owners; R-4 only writes the policy.
- **F-08 (compute, eduardo):** the `remote` backend takes an allowlist of team-controlled hosts. Any other host is treated as third-party and needs the project opt-in.
- **F-64 (API) / project settings:** a per-project `allow_cloud_media` setting, default `false`, shown in the UI.
- **F-09 (benchmark harness, jean):** the clip manifest records whether each clip's consent covers cloud processing.
- **F-52 (identity):** enrollment consent, local template storage, transient bystander embeddings.
- **R-1 (planner LLM, paul):** candidates are compared on text-only input; a model that needs to see frames to plan well doesn't fit this policy.

---

## Question 2: Boxes or segmentation masks from the start?

### What's already decided
- **D-013 / contract v1:** geometry lives in tracks, separate from ops. A `Track` has `Keyframe`s of `{t, box, conf, state}` with normalized boxes (`backend/src/vibe/contract/tracks.py`). Effects have `padding` (default 0.15) to grow the box.
- **D-015 (R-3):** SAM 2.1 is the proposed mask engine offline, EdgeTAM for live. R-3 calls live masks a stretch goal.
- **ROADMAP:** F-15 "Segmentation masks instead of boxes" is a Backlog card. F-04 (renderer), F-10 (find and track) and F-11 (blur / pixelate / box) are all box-based.

So "boxes or masks" isn't really open. The real question is **whether masks can be added later without redoing the box pipeline**, and what the renderer does when it has both.

### Boxes first: why
1. **Everything in Sprint 2–3 already assumes boxes:** the contract, F-04's exit check ("a masked region changed"), F-10's tracker output and F-11's effects.
2. **Boxes fail closed by construction.** A padded box covers more than the person. A mask that's a few pixels off leaks an ear or a hand. For privacy edits, the bigger shape is the safer default.
3. **Masks need a GPU.** SAM 2.1 runs on tier 2 (the 5070). CPU-only teammates would only ever see masks through the stub backend replaying a cache.
4. **Live masks are a stretch.** R-3 lists EdgeTAM as a live candidate, unmeasured on the 5070. Live mode works with boxes alone.

### Where boxes fall short
- **"Everyone except me" with people overlapping.** A bystander's box can cover part of the streamer. That errs toward privacy (the streamer is partly blurred), so it's a quality problem, not a leak.
- **Object removal (F-16).** Video inpainting needs a pixel mask of what to remove. F-16 therefore depends on F-15.
- **Looks.** A blurred rectangle looks worse than a blurred silhouette. That's a polish item, not a correctness one.

### The design (proposed as D-018)
- **v1: boxes.** No change to contract v1.
- **Later, F-15 adds an optional mask reference to each keyframe**, e.g. `mask: {key, frame} | None`, pointing at a mask file in the analysis cache (`cache/<sha256>/masks/<key>/`), stored as run-length encoded masks (COCO RLE is the common format, with `pycocotools` and `rlemasklib` to read and write it).\[7\]\[8\] Masks never go inline in tracks (too large) or in the op log (geometry isn't an op, D-013).
- **The box stays required.** It's what tracking, the UI's drag-to-correct and the fallback use.
- **Renderer rule:** use the mask (dilated by the effect's padding) when the frame has one and the keyframe isn't `uncertain`; otherwise mask the padded box. A missing, failed or unsure mask means the box, never nothing.
- **Compatibility:**
  - The op log format doesn't change. Masks are geometry, and `schema_version` currently gates the op log header (`contract/log.py`).
  - Tracks are contract models with `extra="forbid"` (`contract/common.py`). Old tracks read fine under new code, because the field is optional, but **old code rejects new tracks** that carry a mask. Under the current rule ("bump it on any change that breaks a reader or writer", `common.py`), F-15 most likely bumps `schema_version`. That's for F-15 and the contract owner to decide, with a heads-up to the team.
  - Analysis cache keys include the model and version (D-014), so tracks made with masks don't overwrite box-only caches.

---

## Caveats

- **Legal points are from secondary sources.** This session's network blocked the primary statute and regulation pages, so the BIPA and GDPR summaries come from search results and law-firm or regulator summaries.\[1\]\[2\]\[3\]\[4\] Search results also mention a 2024 BIPA amendment (P.A. 103-769) and proposed 2025 bills; their effect wasn't checked. Nothing here is legal advice. The policy is built to be conservative enough not to depend on the details.
- **Provider retention terms weren't verified.** The Anthropic numbers above are from search summaries that disagree with each other.\[5\]\[6\] The policy deliberately doesn't depend on them. If the team ever enables the cloud opt-in, whoever wires it up checks the chosen provider's current terms first.
- **Whether a 5070 VLM is good enough is unmeasured.** That's for F-50 / F-09, with the hardware stated. Nothing here is a speed or quality number.
- **The mask field's exact shape is F-15's call.** `{key, frame}` is a sketch to show the change is small, not a spec.
- **Not checked:** university or course rules on recording people (if any apply to demo-day footage). Worth asking the course staff.

## Sources

1. Illinois Biometric Information Privacy Act, 740 ILCS 14 (sections 10 and 15), via search summaries of the statute text: https://law.justia.com/codes/illinois/2022/chapter-740/act-740-ilcs-14/ and https://codes.findlaw.com/il/chapter-740-civil-liabilities/il-st-sect-740-14-15/
2. Seventh Circuit opinion summarizing BIPA section 15(a) duties: https://www.govinfo.gov/content/pkg/USCOURTS-ca7-20-02782/pdf/USCOURTS-ca7-20-02782-0.pdf
3. UK ICO, "Biometric data guidance: key data protection concepts" (special-category biometric data only when used to uniquely identify): https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/guidance-on-biometric-data/key-data-protection-concepts
4. GDPR Article 4(14) (definition of biometric data): https://www.legislation.gov.uk/eur/2016/679/article/4/adopted/data.html
5. Anthropic Privacy Center, "Is my data used for model training?" (as summarized by search; not fetched): https://privacy.claude.com/en/articles/7996866-is-my-data-used-for-model-training
6. Third-party summary claiming a shorter API retention window (conflicts with [5]): https://www.developersdigest.tech/models/data-retention/anthropic
7. rlemasklib documentation (COCO RLE format and operations): https://rlemasklib.readthedocs.io
8. COCO mask API wrapper example (`encode` / `decode` of RLE masks): https://huggingface.co/spaces/sklum/detection_metrics/blob/main/mask.py
