---
author: paul
date: 2026-11-17
branch: paul/r1b-planner-llm
features: [R-1b]
base: v0.3.0 @ 716554b
head: 48fd041
status: in progress
---

# R-1b: planner LLM benchmark on main's 12 tools; local models and Nemotron fully scored, Groq and Gemini partial

> **Heads-up: shared files.** Not in `main` yet, so no team post has gone out. Post one before the PR. This branch changes:
> - **Dependency files:** an optional `llm` group in `backend/pyproject.toml`, and `backend/uv.lock`. CI and teammates who don't run the benchmark are unaffected.
> - **`.env.example`:** `GROQ_API_KEY`, `GEMINI_API_KEY` and `OPENROUTER_API_KEY`. The paid keys run only with `--allow-paid`.
> - **API routes:** `POST /api/projects/{id}/requests` takes a planner dependency (`get_planner`). The route's arguments and replies are unchanged, and so is `openapi.json`. `api/app.py` fails at startup if `VIBE_PLANNER_LLM` is set to something it can't use.
> - **`backend/src/vibe/config.py`:** `planner_llm()` reads `VIBE_PLANNER_LLM`. Unset (the default, and what tests and CI use) means templates only.
> - **Planner:** the LLM reply in `backend/src/vibe/agent/llm.py`.
> - **`HANDOFF.md`'s "Shared files" list** adds `llm.py`, the request set and `.env.example`.
> - **Request set:** `backend/tests/agent/requests_llm.json`, in F-05's format plus `route`, `tools`, `category` and `alternatives`.

## What changed
Follows my [R-1 entry](2026-11-01-paul-r1-planner-llm.md). R-1b is the Sprint 4 card carried into Sprint 5: run the comparison with free candidates only, add the decision row, wire the chosen model into `plan_request`, and flag cut ranges the LLM invents.

- **Nov 12–16 (`ffd0dc1`..`6922ea6`):**
  - The benchmark was ported to main's tools.
  - **Decision rule fixed before any scored run** (`agent/bench/__init__.py`). Over the core requests the templates don't take, 5 runs each:
    1. zero unsafe, or the fewest;
    2. at least 80% correct;
    3. p95 under 5 s.
  - **Free candidates only:** Groq, the Gemini free tier, OpenRouter `:free`, and Ollama on our own GPUs.
  - **`VIBE_PLANNER_LLM`** wires a benchmark candidate into the chat route.
  - **Cut guard:** a cut or keep at a time the user didn't give comes back as a question.
  - **Gemini** goes through its OpenAI-compatible endpoint.
  - **Preflight** waits between a rate-limited model's two calls. The free keys were tested from a Claude cloud session on Nov 16.
- **Nov 17: `main` @ `716554b` merged in (`f0700fc`, no rebase).**
  - **Conflicts:** `api/projects.py` and `tests/test_api_projects.py`.
  - **Three test updates the merge required:**
    - The LLM route test now checks that the LLM's plan is **applied**, on a seeded face track.
    - The schema test allows `undo`/`redo`, which take no arguments, to have no `required` list.
    - "undo that" in the extras became "take back my last change", because main's templates now take "undo that".
  - **The planner now sees 12 tools:** `find_by_description` (F-13), `find_speech` (F-21), `undo` and `redo` are new.
- **Nov 17 fixes:**
  - `77de96e`: Ollama `num_ctx` 8192 → **4096**, so `qwen3.5:9b` fits fully on an 8 GB card.
  - `1145e5a`: Groq `max_tokens` → **512** (`GROQ_MAX_TOKENS`), for Groq's per-minute output-token limit. A test checks it.
- **Nov 17 runs and doc (`48fd041`, `docs/research/r1-planner-llm.md` "Results so far"):**
  - the 12-tool preflight, the first scored runs, and two rules added before any hosted run was complete (see "Decisions made");
  - the draft decision row is renumbered **D-022**, because D-020 and D-021 are taken on `main`.

## How to check it
```bash
cd backend
uv run pytest -q
uv run --group llm pytest -q tests/agent
uv run ruff check . && uv run ruff format --check . && uv run python -m vibe.api --check
PYTHONUTF8=1 uv run python -m vibe.agent.bench report      # needs the .jsonl files (see Gotchas)
```
Results at `48fd041` on Windows 11 Home, Python 3.12.2, uv 0.12.11:
- **Tests:** backend **1759 passed, 2 skipped, 1 failed**. The failure is the known Windows `test_toolbox_cli.py::test_writing_keeps_the_timeline_permissions`. `tests/agent` with the `llm` group: **621 passed**. Ruff and the spec check pass.
- **Candidate zero** (templates only, no model): they take 30 of the 64 core requests and get none wrong. **34 reach the LLM**, the same as before the merge.

**Scored runs**, script output from `report`, `--set llm --runs 5` (34 requests × 5). Machine: Paul's PC with an RTX 4060 Ti 8 GB. Local models run through Ollama at `num_ctx` 4096, and all stayed 100% on the GPU. Hosted ones ran over the same PC's own internet connection.

| Candidate | Results | Correct | Unsafe | p95 s | Gates |
|---|---|---|---|---|---|
| `qwen3.5-4b` (local) | 170 | 67% | 38 | 2.60 | fail, fail, pass |
| `qwen3.5-9b` (local) | 170 | 62% | 34 | 4.84 | fail, fail, pass |
| `gemma4-e4b` (local) | 170 | 73% | **5** | 2.59 | pass, fail, pass: **offline fallback** |
| `openrouter-nemotron-3.5-super-vl` | 170 | 86% | 21 | 8.04 | fail, pass, n/a (quality only) |
| `groq-gpt-oss-120b` | 112, partial | 86% | 16 | 1.22 | not compared yet |
| `groq-qwen3.8-27b` | 93, partial | 80% | 13 | 1.58 | not compared yet |
| `groq-gpt-oss-20b` | 78, partial | 83% | 13 | 0.98 | not compared yet |
| `gemini-3.5-flash-lite` | 144, partial | 85% | 21 | 4.37 | not compared yet |

- No model is chosen yet: no candidate that can win has a complete run.
- Under the current gold results, every hosted model so far has more unsafe results than Gemma's 5. Most of those come from 4 contested gold results (see Next steps).

**Not tested:**
- `VIBE_PLANNER_LLM` set to a real model in the running app. The route tests use a fake planner.
- The R-1b exit check's end-to-end requests with a real model.
- `gemma4-12b`, which needs more than 8 GB.
- `openrouter-gemma-4-31b`: upstream 429s all day, so 0 results.
- CI on this branch.

## Decisions made
paul made these on 2026-11-17, before any hosted candidate had a complete run. They're R-1b method choices, not decisions-log rows; the decision itself will be D-022.
- **If no free hosted model passes every gate, R-1b proposes no hosted LLM:** the templates, plus the offline fallback as the planner LLM where Ollama runs. Written into the rule's docstring and the doc.
- **A failed provider call is re-run, not scored.** These are records whose result is "LLM call failed", such as timeouts and provider errors before a 429. They're moved to `outputs/r1-planner/set-aside/`, where `report` and `--resume` don't look. On Nov 17: 3 Flash-Lite records, and 19 of Gemini 3.8 Flash's 21.
- **`gemini-3.8-flash` is dropped.** Its free daily limit looks like about 20 requests, which would mean about 8 more days. Flash-Lite stays as the one Gemini model the doc says never to drop.
- **All hosted runs stay on this PC,** resumed when Paul asks, so each candidate's latency comes from one network.
- **Groq's GPT-OSS models run at `--rpm 1.7`:** a call is about 4.5K tokens with 12 tools, and Groq allows 8K tokens a minute.

## Next steps
1. **Resume the hosted runs each day until all are at 170.** Calls left:
   - `gpt-oss-120b` 58;
   - `gpt-oss-20b` 92;
   - `qwen3.8-27b` 77;
   - Flash-Lite 26 (23 never run, plus the 3 set aside);
   - `openrouter-gemma-4-31b`: one try a day; it's on the drop list.

   The commands are below, under Gotchas. My estimate is 1–3 days, inferred from Groq's daily-limit messages, not measured.
2. **Gold review, by Jean (F-09) and Pablo (F-05, `requests_draft.json`):**
   - "blur the guy in red" is stale. Propose the F-13 template's plan as its gold.
   - "keep everything except 0:10 to 0:20" and "keep faces blurred" both ask a question; check whether they should be plans.
   - Keep "trim 0:10 to 0:20" asking.
   - Jean's open review of `requests_llm.json`.

   A gold change only means a free re-score. Re-scoring the four complete candidates in memory with the "guy in red" fix changes **no gate**.
3. **Decide:** run `report --detail`, fill in D-022 from the doc's draft, show Paul the exact text, then post it to the team as a proposal. After that, apply the doc's "Replacement lines" to `README.md` and `docs/ARCHITECTURE.md`.
4. **Exit check:** set `VIBE_PLANNER_LLM=<chosen candidate>`. "hide everyone's faces in the second half" should get a valid plan, and "cut the boring part" a question. Check it through `plan_request` and once in the app.
5. **Then:** merge `main` again, post the shared-file heads-up above, and open the PR.
6. **Follow-ups after the decision:**
   - `find_speech`'s description: say it matches exact words.
   - The extras' eval-only `find_speech`/`undo`/`redo`/`check_attribute` against main's real tools.
   - `GROQ_RPM`'s comment, which still assumes 3.3K tokens a call.
   - `report` mixing settings within one candidate.
   - Alex's gap: an LLM failure becomes a clarify question, so the 503 can't be reached.

## Gotchas
- **Don't change the prompt or the tools until every hosted run is done.** That includes a new merge of `main`, a tool description, or a new tool. `report` leaves out results made with an older prompt hash, so the partial hosted results would be lost. `main` @ `6491308` (the v0.4.0 release) changes no toolbox or agent file.
- **The raw results aren't in git:** they're in `outputs/r1-planner/*.jsonl` on Paul's PC. Anyone writing the report needs those files.
- **Resume commands,** from `backend/` in PowerShell:
  ```powershell
  $env:PYTHONUTF8 = '1'; $net = "Paul's Windows 11 PC, its own internet connection"
  uv run --group llm --env-file ../.env python -m vibe.agent.bench run --candidate groq-gpt-oss-120b --set llm --runs 5 --resume --rpm 1.7 --network $net
  uv run --group llm --env-file ../.env python -m vibe.agent.bench run --candidate groq-gpt-oss-20b  --set llm --runs 5 --resume --rpm 1.7 --network $net
  uv run --group llm --env-file ../.env python -m vibe.agent.bench run --candidate groq-qwen3.8-27b  --set llm --runs 5 --resume --network $net
  uv run --group llm --env-file ../.env python -m vibe.agent.bench run --candidate gemini-3.5-flash-lite --set llm --runs 5 --resume --rpm 4 --network $net
  ```
  - GPT-OSS needs `--rpm 1.7` every time, because the built-in default of 2 a minute is over Groq's 8K-tokens-a-minute limit.
  - Gemini's `--rpm 4` is a guess; Google doesn't publish its free limits.
- **Groq has three free-plan limits, and each one stops a run with a 429:**
  - 8K tokens a minute;
  - 1,000 **output** tokens a minute, checked before each request runs (hence `max_tokens` 512);
  - 200K tokens a day.

  The daily one says "try again in N min", which suggests it refills gradually. A 403 from Cloudflare even without a key means the network is blocked (a VPN, for example), not the key.
- **Gemini 402 means the key belongs to a paid project.** The model lookup still works with it. Use a free-tier key from a project with no billing.
- **OpenRouter's free models also have a per-minute limit** (`free-models-per-min`). Pass `--rpm 15`.
- **Ollama misreports `gemma4:e4b`'s VRAM** as 0.35 GB. `nvidia-smi` showed about 3.9 GB in use.
- **Claude Code can stop background runs** when Windows reports low memory; it did twice. The saved results survive, and `--resume` continues. To prevent it, start Claude Code with `CLAUDE_CODE_DISABLE_BG_SHELL_PRESSURE_REAP=1` set.
