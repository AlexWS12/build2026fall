---
author: luis
date: 2026-11-21
branch: luis/f13-vlm-judge
features: [F-13]
base: v0.4.0 @ 6491308
head: 980f7ce
status: in progress (judge and measurement script work; first measured numbers for one model; truth file still a draft)
---

# F-13: a real VLM judge for "find by description", plus a measurement script and the first numbers (gemma3:12b on an L4)

Follows [2026-11-10-luis-f13-targeting](archive/v0.4.0/2026-11-10-luis-f13-targeting.md) (the colour stub and the empty VLM slot) and [2026-11-15-eduardo-d020-vlm-qwen](archive/v0.4.0/2026-11-15-eduardo-d020-vlm-qwen.md) (the D-020 proposal).

## What changed
- **`vision/vlm.py` (new): `VlmJudge`.** For the `local` and `remote` backends. Sends one chat request per track with its ~3 crops as images to any server that speaks the OpenAI chat API (Ollama, llama.cpp, vLLM). Standard library only (`urllib`, a small PNG encoder), so **no new dependency**. Behaviour:
  - Asked for one word: `yes` / `no` / `unsure`. **Any reply that is not exactly one of those is `unsure`** (a rambling model is not saying "no").
  - Fewer than `MIN_CROPS` crops: `unsure` without asking.
  - Server down, HTTP error, timeout or nonsense: raises `DescribeError`, never a verdict.
  - Host gating (D-017): `local` talks to loopback only; `remote` only to a host in `VIBE_REMOTE_HOSTS`.
  - `compound = True`: the model reads the whole sentence ("in grey with a bag").
  - Prompt version is part of the judge's settings, so it's in every cache key.
- **`config.py`: two new env vars.** `VIBE_VLM_URL` (default `http://localhost:11434`) and `VIBE_VLM_MODEL` (**no default**: D-020 is still Proposed). Additive only.
- **`vision/describe.py`:** `make_judge` now returns `ColorJudge` for `stub` and the lazy-imported `vlm_judge(backend)` otherwise. Without `VIBE_VLM_MODEL` the `local`/`remote` backends raise a `DescribeError` saying so.
- **`vision/measure.py` (new)** and **`vision/measure_truth.json` (new, DRAFT):** the measurement script (see below).
- **Tests:** `tests/vision/test_vlm_judge.py`, `tests/vision/test_measure.py` (new); `test_describe_tracks.py` and `tests/agent/test_execute.py` updated (the old "isn't built yet" expectation became "set `VIBE_VLM_MODEL`").

### The measurement script
`python -m vibe.modules.vision.measure TRUTH.json --hardware "..." --runs-on {this-machine|team-machine|university|third-party} [--backend] [--samples] [--json OUT]`

- Runs the judge on **hand-labelled boxes** (the truth file says which track matches each description), not on detector output, so it measures the judge and not the detector.
- Counts **right / wrong "no" / wrong "yes" / unsure separately**. There is deliberately no single accuracy number: D-020's only acceptance criterion is "never a confident wrong no".
- `--hardware` is required. `--runs-on` is required and checked: `third-party` needs `consent_cloud` on the clip (D-017); `this-machine` with a VLM judge needs a loopback URL. `university` (HiPerGator) was added because the team approved HPG use.
- Collects all problems in the truth file before failing, uses the benchmark's clip manifest, hash check and label loader.

## How to check it
Tests (no GPU, no network needed):
```
cd backend
uv run pytest -q          # 1598 passed, 2 skipped, 1 failed (see Gotchas: Windows-only chmod test)
uv run ruff check . && uv run ruff format --check .
```
Run on 2026-11-21 on Windows: `ruff check` and `ruff format --check` are clean. The one failure is `test_writing_keeps_the_timeline_permissions` (POSIX chmod); it is known from earlier handoffs and is unrelated to this branch.

To reproduce the numbers you need the four F-09b clips registered locally (`python -m vibe.benchmark add-clip ...`, hashes are in `clips.json`), a VLM server, and then:
```
VIBE_BACKEND=remote VIBE_REMOTE_HOSTS=localhost VIBE_VLM_MODEL=gemma3:12b \
  python -m vibe.modules.vision.measure backend/src/vibe/modules/vision/measure_truth.json \
  --hardware "NVIDIA L4 23 GB (HiPerGator)" --runs-on university --json out.json
```
(with an SSH tunnel to the Ollama server on the GPU node as `localhost:11434`; `remote` + `localhost` is the explicit opt-in for a tunnel, see `vlm.py`).

## Measured results (first run; read the caveats before quoting)
**Setup:** `gemma3:12b` (Q4_K_M) through Ollama, NVIDIA L4 23 GB on HiPerGator (node `c1105a-s10`), reached over an SSH tunnel; `--runs-on university`, backend `remote`; 3 crops per track, prompt v1, `max_side` 512, `max_tokens` 16; run 2026-11-21 (~15:38 UTC). Truth: `measure_truth.json`, 13 cases on 4 clips, hand-labelled boxes.

**Totals: 45 tracks scored (+3 ambiguous, not scored).**

| Outcome | Count |
|---|---|
| right | 29 (13 matches said `yes`, 16 non-matches said `no`) |
| **wrong "no"** (the D-020 criterion) | **0** |
| wrong "yes" | 2 |
| unsure | 14 (1 on a matching track, 13 on non-matching ones: 13 of the 31 non-matches) |

The two wrong "yes" are both on `hikers-backpacks` (filmed from behind): "in a grey shirt" said yes for h3 (the notes say purple top / lavender shorts) and "with a red backpack" said yes for h4 (the notes say pink backpack). The one unsure on a match is "with a pink backpack" for h4. The 3 ambiguous tracks (not scored): h4 for "grey shirt" came back `unsure`; h1 and h3 for "red backpack" (partly red) came back `yes`.

Per case (right / wrong-no / wrong-yes / unsure): neon green jacket 2/0/0/1; orange jacket 2/0/0/1; white denim jacket 3/0/0/0; pink denim jacket 2/0/0/1; bucket hat 3/0/0/0; glasses 2/0/0/1; white t-shirt 2/0/0/1; red plaid shirt 2/0/0/1; tote bag 2/0/0/1; tan shirt 2/0/0/1; pink backpack 2/0/0/4; grey shirt 3/0/1/1; red backpack 2/0/1/1.

**Time per track** (48 tracks asked, 254 s total): mean 5.29 s, median 5.38 s, max 6.89 s. Includes the tunnel round trip and PNG encoding, not cutting the crops.

**Baseline on the same truth file:** the colour stub (`ColorJudge`, this laptop's CPU, `--runs-on this-machine`, same day): 1 right, 44 unsure, 0 wrong "no", 0 wrong "yes". It is safe but almost always masks, which is what it's for; it can't read "bucket hat" or "backpack".

**Smoke test** through the tunnel with synthetic crops (same model): solid red crops + "in red" gave `yes`, solid blue + "in red" gave `no`, red + "wearing glasses" gave `unsure`, a single crop gave `unsure`.

### Caveats (important)
- **The truth file is an unverified draft.** It was drafted from the clothing notes in each clip's labels; nobody has watched the clips against it yet. Some "wrong yes" results may be wrong labels (h3/h4 especially).
- **One model, one run, one prompt.** No repeats, no other models. Gemma 3 12B does not fit the 6 GB laptop tier; this says nothing about the 4B/9B models D-020 proposes.
- **Counts, not rates.** Only 14 matching tracks in total; a wrong "no" can only happen on a matching track, so "0 wrong no" means 0 out of 14 chances (13 said yes, 1 said unsure). That is not evidence of a rate.
- **Hand-drawn boxes** are easier than real detector tracks; real use will be somewhat worse.
- **Not tested:** Qwen3.5 (D-020's proposal: banned on HPG), Gemma 4 E4B, InternVL3.5-4B, anything on the 5070.
- **Scale:** at ~5.3 s/track, 677 tracks would take about an hour. That is arithmetic on the measured mean, not a measured run. Stitching fragmented tracks (F-10b) or fewer judge calls would be needed for long videos.

## Decisions made
- **No default model.** `VIBE_VLM_MODEL` must be set; D-020 stays Proposed until someone measures the candidates.
- **Strict parsing:** anything other than exactly yes/no/unsure becomes `unsure`.
- **Stdlib-only HTTP**, so no dependency change and no `uv.lock` churn.
- **Tunnels:** a tunnel to someone else's machine looks like `localhost`. Use `VIBE_BACKEND=remote` and list `localhost` in `VIBE_REMOTE_HOSTS`; the person who opens the tunnel owns that D-017 decision.
- **`--runs-on university`** is a declaration, not enforcement: the team's approval of HiPerGator was verbal and is **not recorded in `docs/ARCHITECTURE.md`**. A decisions-log note would make it durable (needs the team's OK, so I have not added it).
- **Team heads-up needed (shared file):** `config.py` gains `vlm_url()` and `vlm_model()`. Additive, no contract or `schema_version` change, no dependency change.

## Next steps
- Watch the four clips and fix or drop cases in `measure_truth.json` (especially h3/h4), then re-run.
- Run the same truth file on the D-020 candidates that are allowed where they run: Qwen3.5-4B locally or on the 5070 (not HPG), Gemma 4 E4B on HPG, and compare the counts side by side, with hardware stated.
- Add more consented clips (the F-09b clips have `consent_cloud: false`, so they can't go to a third party) so the matching-track count is large enough to say something about a rate.
- Decide how to cut judge calls on long videos (F-10b stitching, or one call per merged track).
- Document `VIBE_VLM_MODEL` / `VIBE_VLM_URL` in the README (not touched here).
- Post the `config.py` heads-up to the team; ask whether to add the HiPerGator approval to the decisions log.

## Gotchas
- **The clips are F-09b Pexels clips with `consent_cloud: false` (D-017).** `third-party` runs are refused by design. The videos and the `data/` store are gitignored and must be re-registered on each machine.
- **Qwen/DeepSeek-type models are banned on HiPerGator** (UF policy, Fla. Stat. §288.860); Gemma, Llama, Mistral are allowed. That is why D-020's first choice couldn't be tested there.
- **HPG workflow:** eduVPN, then an OOD Console job on `hpg-turin` (L4 23 GB, `gpu:1`), `ml ollama`, `ollama serve` (redirect is `> ollama.log 2>&1 &`), then `ssh -L 11434:localhost:11434` through the jump host. The node name changes every session. `ollama` only exists on the node, not on the laptop or in a fresh shell without `ml ollama`.
- **The raw results JSON** (`resultado-gemma3-12b.json`) lives outside the repo and is not committed; the numbers above are copied from it.
- **Windows:** `test_writing_keeps_the_timeline_permissions` fails on Windows (POSIX chmod) with or without these changes.
- Committed locally on `luis/f13-vlm-judge`; not pushed yet and no PR opened.
