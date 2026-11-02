---
author: paul
date: 2026-11-01
branch: paul/Research-planner
features: [R-1]
base: v0.1.0 @ 21ee75c
head: 21ee75c (no commits on this branch yet)
status: in progress
---

# R-1 planner LLM: research doc adapted to `main`; no candidate scored, so no model picked

> **The exit check isn't met.** No candidate has been scored, so there's no script output to compare, no decision row, and the `README.md` and `docs/ARCHITECTURE.md` candidates are unchanged. R-1 is due Tue 2026-11-03.
>
> **R-1 is on two branches.** This one has the research doc, which is R-1's write-up. The benchmark code is on `paul/r1-planner-llm`, which has its own entry: `git show paul/r1-planner-llm:docs/handoffs/2026-11-01-paul-r1-planner-llm.md`.

## What changed
- **`docs/research/r1-planner-llm.md`:** the Oct 28 research, rewritten against `main` at `21ee75c`, with PRs #16 and #17 noted where they change something. It holds the shortlist, team hardware, how to run the comparison, the decision rule, the parser's scope, preflight results, a plan to Nov 3, a draft decision row and the replacement lines for `README.md` and `docs/ARCHITECTURE.md`.
- **Shortlist, not a pick:**
  - Hosted: Haiku 5.5, Sonnet 5.5, GPT-6.1 Terra and Gemini 3.8 Flash, with Opus 5.5 as the ceiling.
  - Local: Qwen3.5-4B, Qwen3.5-9B, Gemma 4 E4B and Gemma 4 12B.
  - Model to beat, as a hypothesis: Haiku 5.5, with Qwen3.5-4B as the offline fallback.
- **Main finding:** `requests_draft.json` can't compare models yet. Of its 34 requests, the parser plans 18 and asks on 8. The other 8 reach the LLM, and all of them expect a question. `Plan.model_validate(raw)` requires `calls` (`agent/planner.py:85`), so the LLM can't ask on purpose, and a broken model scores 8 of 8 by failing twice.
- **Proposed fix:** give the reply a `clarify` field, and score the 24 requests that reach the LLM: those 8 plus 16 core requests from `requests_llm.json` on `paul/r1-planner-llm`. The doc lists all 24.
- **Template parser:** keep F-05's scope, with one recommended change, below.
- **Not changed:** code, `README.md`, `docs/ARCHITECTURE.md` and `docs/ROADMAP.md`.

## How to check it
Run the snippet in the doc's "Appendix: the routing count" from `backend/` with `uv run python`. It prints 18 template plans, 8 parser questions and 8 LLM-route questions, with no model or GPU.

Verified on 2026-11-01 on Windows 11, Python 3.12.2:
- `npm test`: backend 278 passed. The frontend step stopped with `'vitest' is not recognized`, because its dependencies aren't installed on this machine. This branch changes no frontend files.
- `uv run pytest tests/agent -q`: 150 passed.
- `uv run ruff check` and `uv run ruff format --check`: both pass.
- CI hasn't run, because there's no PR yet.

**Not tested:**
- Scoring. `run` has never reached a real model, and the doc's `vibe.agent.bench` commands only exist on `paul/r1-planner-llm`.
- GPT-6.1 Terra, Gemini 3.8 Flash and every local candidate have never completed a call.
- Whether LM Studio enforces a schema, and whether `report --requests <file>` can score only the 24.
- The team hardware table is as reported on Oct 28. Only paul's card has been checked since.
- Nobody but paul has reviewed the gold results.

## Decisions made
**This doc is R-1's write-up** (paul, 2026-11-01), because it's accurate against `main`. Nothing goes in the decisions log until there are results.

**Still open:** four questions to settle before the first scored run, because changing them afterwards would mean tuning the rule to the results. The doc recommends an answer to each:
- Do the gates cover all 50 core requests or the 24 that reach the LLM? Recommended: the 24.
- Does a mask that leaves people visible count as `unsafe`? Recommended: yes.
- Is "cheapest" based on cached or uncached cost? Recommended: uncached.
- Do requests for future tools run in this pass? Recommended: no.

## Next steps
The full schedule is in the doc's "Plan to Nov 3".
1. **paul:** settle the four questions above.
2. **paul, team chat:** ask chris for Gemini credits, anyone for OpenAI credits, nickolas or pablo for a Gemma 4 12B run, and jean to review the 24 requests.
3. **paul:** install Ollama or test LM Studio's server, then update `candidates.py` on `paul/r1-planner-llm`.
4. **Mon Nov 2:** preflight the untested candidates, run Opus once and fix any gold results it exposes, then do 5 runs each on the rest.
5. **Tue Nov 3:** print the report, propose the decision row to the team, apply the `README.md` and `docs/ARCHITECTURE.md` lines, add the results to this write-up and open the PRs.

**pablo:** "mute it" and "silence it" currently become a whole-clip mute, because `_MUTE` accepts "it" (`agent/templates.py:28`), and no test covers it. Pronouns go to the LLM everywhere else in the parser, so the doc recommends the same here.

## Gotchas
- **The older copy on `paul/r1-planner-llm` conflicts with this doc,** so it has to come off that branch before both merge. It stays in that branch's history at `6dbcf92`.
- **This doc describes `main`, not `paul/r1-planner-llm`.** "No `clarify` field" and "no benchmark" are true here and not there.
- **`candidates.py` doesn't match the shortlist yet.** It has `gpt-5.6-terra` rather than `gpt-6.1-terra`, and lacks `qwen3.5:4b` and Gemma 4 E4B. A scored run before that's fixed won't match the doc.
- **`report`'s `decision` uses cached cost today,** so the recommended uncached rule still needs a change on that branch.
- **The row is D-019 only if PR #17 merges first.** That PR claims D-017 and D-018, so check the number before proposing it.
- **The R-1 row's branch column is empty on `main`.** `paul/r1-planner-llm` already fills it in, so this branch leaves `docs/ROADMAP.md` alone.
- **The parser's 26 of 34 correct is weak evidence,** because the set was written alongside the parser.
