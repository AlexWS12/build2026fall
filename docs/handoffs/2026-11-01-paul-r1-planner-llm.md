---
author: paul
date: 2026-11-01
branch: paul/r1-planner-llm
features: [R-1]
base: v0.1.0 @ 21ee75c
head: bbe0c37
status: in progress
---

# R-1 planner LLM: benchmark and LLM slot built and reviewed; preflight ran, but no candidate is scored and nothing is picked

> **No scored numbers exist yet, so there's no pick.** Only a preflight has reached real models. OpenAI and Gemini are out of credits, and Ollama isn't installed. R-1 is due Tue 2026-11-03.
>
> **Heads-up:** this changes **shared files**. `backend/pyproject.toml` and `backend/uv.lock` gain an optional `llm` dependency group, and "Shared files" in `HANDOFF.md` gains the planner files and request set. It also changes `agent/planner.py` from F-05. Post in the team chat before merging. Tool names and arguments, the contract and storage aren't touched.
>
> **Stacked on F-06:** the branch starts from `pablo/f06-toolbox` at `7602899` (PR #16, still open), because F-06 deletes `agent/tools_draft.py` and changes gold plans. Rebase once #16 merges. R-1 is the 9 commits in `7602899..bbe0c37`; a diff against `main` also shows F-06.
>
> **D-017 is taken:** PR #17 (nickolas, R-4) claims D-017 and D-018, so R-1's row becomes D-019 if #17 merges first.

## What changed
- **LLM slot, `backend/src/vibe/agent/llm.py`:**
  - `reply_schema(registry)` builds one JSON schema from the tool registry: `{"clarify": string|null, "calls": [one variant per tool]}`. No recursion, no numeric or length limits, objects closed, optional fields nullable.
  - `system_prompt`, with 4 few-shot examples that aren't in the scored set.
  - `parse_reply`, and `SchemaPlanner`, which implements F-05's `LlmPlanner`. Use one instance per request.
- **Providers, `agent/providers.py`:** Anthropic, OpenAI (also used for OpenRouter), Gemini and Ollama behind one `Provider` interface. SDKs are imported lazily, and SDK retries are off so they can't hide latency.
- **Planner (F-05), `agent/planner.py`:**
  - An LLM reply with a non-empty `clarify` now returns `Clarify(reason="LLM asked")` with no retry.
  - `_plan_with_llm` is now the public `plan_with_llm`, so the bench can also run the LLM on requests templates would take. `plan_request` behaves the same when the LLM doesn't ask.
- **Benchmark, `agent/bench/`, run as `python -m vibe.agent.bench preflight | run | report`:**
  - `score.py` compares plans by what they do: refs followed, `resolve_time` evaluated on a 120 s fixture, step names and independent order ignored, free text matched by keywords. Outcomes: correct, over_asked, unsafe, wrong_plan, unknown_label, unresolvable_time, invalid, refusal, provider_error.
  - `candidates.py`: `opus-5-5` (ceiling), `sonnet-5-5`, `haiku-5-5`, `gpt-5.6-terra`, `gemini-3.8-flash`, `qwen3.5-9b` and `gemma4-12b` (Ollama), `openrouter-qwen3.5-9b` (quality only). Prices are dated 2026-10-31.
  - `extras.py`: a **draft, eval-only** registry of future tools (`check_attribute`, `find_speech`, `find_speakers`, `denoise`, `add_rule`, `undo`, `redo`). It is not the toolbox: every `run` raises `NotImplementedError`.
  - `report.py` applies the decision rule below. Raw replies go to `outputs/r1-planner/` (gitignored), so re-scoring costs no API calls.
- **Request set, `backend/tests/agent/requests_llm.json`:** 26 new requests, 16 on the five real tools and 10 on the extras; 20 expect a plan and 6 expect a question. The bench loads it with F-05's 34 in `requests_draft.json`: 60 requests, 50 of them core.
- **Fixes from a code review on 2026-11-01, with tests:**
  - A missing `OPENROUTER_API_KEY` made the OpenAI SDK send `OPENAI_API_KEY` to openrouter.ai. It now stops with a `KeyError`.
  - A Gemini network failure crashed the run. It's now an `LlmError`, and `test_providers.py` checks this on all four adapters.
  - Failed calls counted as 0 seconds, so a model that timed out could pass the p95 gate. Latency now times the whole planner call.
  - A partial run could win the decision. A candidate is now only compared if it ran exactly what the ceiling ran.
  - Wrong cuts could escape `unsafe` by going through `resolve_time`. Any cut the gold plan doesn't have is now `unsafe`; a cut left out is only `wrong_plan`.
- **Also:** `.env.example` (key names only); tests in `test_llm.py`, `test_llm_bench.py`, `test_planner.py` and `test_providers.py`; the R-1 row in `docs/ROADMAP.md`.

## How to check it
```bash
cd backend
uv run --exact pytest -q             # no llm group, as in CI: 508 passed, 1 failed, 5 skipped
uv run --group llm pytest -q         # with the SDKs: 512 passed, 1 failed, 1 skipped
uv run --group llm pytest tests/agent -q   # 300 passed
uv run ruff check . && uv run ruff format --check .
uv run python -m vibe.agent.bench report   # empty tables, plus the candidate zero line
```
Verified on 2026-11-01 on Windows 11, Python 3.12.2, uv 0.12.11. The 1 failure is the Windows-only test under "Gotchas". CI hasn't run: it only runs on PRs and `main`, and there's no PR yet. The branch is on `origin` at `e2b5faa`; the 3 commits after it are local.

**Preflight, 2026-11-01** (`python -m vibe.agent.bench preflight`), from Windows 11 with an RTX 4060 Ti 8 GB; the network wasn't recorded. Times are one cold first call, not scored results. Anthropic's calls cost about $0.04 by the script's price table; OpenRouter's charge isn't tracked and shows $0. The raw output is in `outputs/r1-planner/preflight-20261101T205743Z.txt`, on paul's machine only.

| Candidate | Model found | Core schema | Extras schema |
|---|---|---|---|
| `opus-5-5` | yes | accepted, 4.30 s | rejected: grammar too large |
| `sonnet-5-5` | yes | accepted, 3.29 s | rejected: grammar too large |
| `haiku-5-5` | yes | accepted, 1.54 s | rejected: grammar too large |
| `gpt-5.6-terra` | yes | not tested: 429, no credits | not tested |
| `gemini-3.8-flash` | yes | not tested: 402, credits used up | not tested |
| `qwen3.5-9b`, `gemma4-12b` | not tested | Ollama not running | not tested |
| `openrouter-qwen3.5-9b` | yes | accepted, 2.54 s | accepted, 1.90 s |

**Candidate zero:** the template parser alone takes 26 of the 50 core requests and is wrong on 0 (`report`, no model involved). That share comes from how the set was written (34 template-oriented requests plus 16 written to miss the templates), so it says nothing about real traffic.

**Not tested:**
- `run` has never reached a real model, so no scored result exists, and `report` has never read real data.
- OpenAI's and Gemini's response handling, and their settings (`reasoning_effort: none`, thinking level `minimal`). Only their model IDs are confirmed.
- Whether local models accept the schema or a property named `$ref`. The Ollama tags `qwen3.5:9b` and `gemma4:12b` are guesses.
- The end-to-end production path through `plan_request` with a real provider.
- Follow-ups that need chat history (`LlmPlanner` gets none) and requests that need video metadata. The plan executor doesn't exist yet, so no plan has been executed.
- Nobody but paul has reviewed the gold plans.

## Decisions made
paul made these during R-1 (say on the PR if you disagree). None is in the decisions log yet; the row comes with the results.
- **Decision rule, fixed on 2026-10-31 before any run** (`agent/bench/__init__.py`). Over the core requests, all runs:
  1. zero `unsafe` results, or the fewest if no candidate has zero;
  2. correct within 5 points of the Opus 5.5 ceiling;
  3. p95 under 5 s.

  The cheapest hosted candidate that passes wins. The local model that passes, or comes closest, becomes the offline fallback. Extras are reported but don't decide anything. The 2026-11-01 fixes added that candidates must match the ceiling's runs, and widened `unsafe` to any cut the gold plan doesn't have; both came before any scored run.
- **Plans use the D-016 format,** not the intent schema from the original research doc, because F-05 already merged that format.
- **The LLM can ask** through a `clarify` field, because some requests have no tool to do them.
- **Scope: the five real tools decide;** the extras show how candidates might handle future tools.
- **Same schema, prompt and ref key for every candidate,** including local ones. `WIRE_REF_KEY` in `bench/run.py` switches all of them together, so the comparison stays fair.
- **Assumption until F-10 defines labels:** `find_objects.label` is an enum of `face`, `person` and `license_plate`. Renderer-owned `effect.padding` and `strength` are left out of the schema.

**Still open, waiting on paul:**
- **How extras run, now that Claude rejects their schema.** Recommended: one looser schema for extras on every candidate (`tool` as a list of names, all `args` nullable), with core staying strict and `validate_plan` still checking every plan. The alternative is dropping the extras runs, which the cut line below allows.
- **Whether a mask that leaves people visible counts as `unsafe`.** Today, masking the wrong half of a clip is only `wrong_plan`. Recommended: yes, because `CLAUDE.md` says privacy edits fail closed.

## Next steps
Where the exit check stands: the script is built but has no real results; the decision row and the README and `ARCHITECTURE.md` candidates aren't done (stale at `README.md:135`, `docs/ARCHITECTURE.md:40` and `:266`).
1. **paul:** add OpenAI and Gemini credits. Run `winget install Ollama.Ollama`, pull Qwen3.5 9B and Gemma 4 12B, and put the real names from `ollama list` in `candidates.py`. Settle the two open decisions above.
2. **Preflight the untested candidates.** If any rejects a property named `$ref`, set `WIRE_REF_KEY = "ref"` for all of them before any scored run:
   ```bash
   PYTHONUTF8=1 uv run --group llm --env-file ../.env python -m vibe.agent.bench preflight --candidate gpt-5.6-terra --candidate gemini-3.8-flash --candidate qwen3.5-9b --candidate gemma4-12b
   ```
3. **Opus 5.5 core pass, 1 run.** Fix gold plans it fails for a good reason, and have **jean** or another teammate review them:
   ```bash
   PYTHONUTF8=1 uv run --group llm --env-file ../.env python -m vibe.agent.bench run --candidate opus-5-5 --set core --runs 1 --max-usd 2 --network "<your connection>"
   ```
4. **Full runs, 5 each,** with Opus included: hosted candidates, then Qwen on this machine's RTX 4060 Ti, labelled as such. Try Gemma; if it doesn't fit, use OpenRouter for quality only. Ask **alex** for a run of the finalists on the 5070. If time runs short, drop in this order: Gemma and OpenRouter, the consistency metric, running the extras. Never drop the core set, candidate zero, the routed column, or the Anthropic, OpenAI, Gemini and Qwen runs.
5. **End-to-end on the production path:** `plan_request("hide everyone's faces in the second half", llm=SchemaPlanner(<provider>))` should return a valid plan, and `"cut the boring part"` should return the model's own question.
6. **Write it up:** `report --detail`; propose the decision row (show paul the exact text first); fix the stale lines; update this entry with the numbers, hardware and network.
7. **Open the PR, stacked on F-06.** Tell **jean** (F-09) that `requests_llm.json` exists, in the same format plus `route`, `tools`, `category`, `alternatives` and `$keywords`.

**Review findings not fixed:**
- `report` mixes runs with different prompts or settings in one row and prints only the first run's settings (`bench/run.py`, `bench/report.py`). Save a hash of the prompt and schema in each record, and warn when a candidate has more than one.
- `report` crashes with `StatisticsError` when none of a candidate's saved requests are in the set (`candidate_row` in `bench/report.py`).
- Non-`cp1252` text stops a piped run on Windows. Set `PYTHONUTF8=1` until `main` reconfigures stdout.
- Options nothing uses can go: `thinking_budget`, `max_tokens`, Ollama's `host`, `think` and `num_ctx`, `labels`, `--requests`, `Candidate.note`, and the `canonical()` wrapper.
- `ARCHITECTURE.md`'s "Agent / planner" section explains plans but not the `clarify` reply, though `HANDOFF.md` now points there for it. Add a sentence when the candidates are updated.

## Gotchas
- **Claude's structured output has a size limit, and the real toolbox will reach it.** All three Claude models accept the schema for 5 tools and reject it for 12 with `The compiled grammar is too large`; where the limit falls in between is unknown. **pablo:** whoever adds tools past the first few needs the looser schema or a different approach.
- **`report` reads every `*.jsonl` in `outputs/r1-planner/`.** A test run with `--limit` makes that candidate "not compared" until its file is deleted. Opus's 1-run pass can count as the first of its 5 (`--runs 4` next time) only if the prompt doesn't change; otherwise delete it.
- **A "Winner" line means nothing until every candidate has run.** With only Opus saved, `decision` names Opus, because it has nothing to compare against.
- **`test_writing_keeps_the_timeline_permissions` fails on Windows** (`tests/toolbox/test_toolbox_cli.py`, F-06): the mode stays `0o666` after a `chmod`. This branch doesn't touch `toolbox/` or its tests, and CI runs on Linux. **pablo:** consider skipping it on Windows.
- **"Stub:" reaches the LLM.** `find_objects`' description (`toolbox/registry.py:18`) goes into the system prompt word for word. Whether that affects any model is untested. **pablo:** the prompt changes when that text does, so bench numbers only hold for the descriptions they ran with.
- **`mute.speaker` is a plain string in the real tool.** The extras registry lets it take a `$ref` from `find_speakers`; the real `mute` will need `speaker` in `ref_args` when that tool arrives.
- **Gemini 3.8 Flash's price doubles on 2027-01-01.** The price in `candidates.py` is the promotional one, so check it against the semester's run time before calling it cheapest.
- **Latency:** preflight's calls are the cold ones (Claude compiles a new schema and caches it for 24 hours; a local model loads into VRAM). The report also keeps each tool set's first call out of p50 and p95. Pass `--network`, or the numbers are labelled "not stated".
