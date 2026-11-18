---
author: paul
date: 2026-11-02
branch: paul/Research-planner
features: [R-1]
base: v0.1.0 @ 21ee75c
head: a5776ef
status: ready for review
---

# R-1 planner LLM: research doc adapted to `main`; no candidate scored, so no model picked

> **This work delivers the research doc and this entry.** No candidate has been scored and no model is picked, so F-05's LLM half still has nothing to plug in. `docs/ROADMAP.md:160` still lists script output, a decision row and `README.md` and `docs/ARCHITECTURE.md` updates as the exit check. None of those are done, and the team still needs to confirm that row.
>
> **No benchmark code is on this branch.** The doc describes the comparison and what's needed to run it.

## What changed
- **`docs/research/r1-planner-llm.md`:** the Oct 28 research, rewritten against `main` at `21ee75c`. It holds the shortlist, team hardware, how to run the comparison, the decision rule, the parser's scope, preflight results, a comparison plan, a draft decision row and the replacement lines for `README.md` and `docs/ARCHITECTURE.md`.
- **Shortlist, not a pick:**
  - Hosted: Haiku 5.5, Sonnet 5.5, GPT-6.1 Terra and Gemini 3.8 Flash, with Opus 5.5 as the ceiling.
  - Local: Qwen3.5-4B, Qwen3.5-9B, Gemma 4 E4B and Gemma 4 12B.
  - Working hypothesis: Haiku 5.5, with Qwen3.5-4B as the offline fallback.
- **Main finding:** `requests_draft.json` can't compare models yet. Of its 34 requests, the parser plans 18 and asks on 8. The other 8 reach the LLM, and all of them expect a question. `Plan.model_validate(raw)` requires `calls` (`agent/planner.py:85`), so the LLM can't ask on purpose, and a broken model scores 8 of 8 by failing twice.
- **Proposed fix:** give the reply a `clarify` field, and score the 24 requests that reach the LLM: those 8 plus 16 new ones. The doc lists all 24.
- **Template parser:** keep F-05's scope, with one recommended change, below.
- **Not changed:** code, `README.md`, `docs/ARCHITECTURE.md` and `docs/ROADMAP.md`.

## How to check it
Run the snippet in the doc's "Appendix: the routing count" from `backend/` with `uv run python`. It prints 18 template plans, 8 parser questions and 8 LLM-route questions, with no model or GPU.

Verified on 2026-11-02 on Windows 11, Python 3.12.2:
- `npm test`: backend 278 passed. The frontend step stopped with `'vitest' is not recognized`, because its dependencies aren't installed on this machine. This branch changes no frontend files.
- `uv run pytest tests/agent -q`: 150 passed.
- `uv run ruff check` and `uv run ruff format --check`: both pass.
- CI won't run on this PR, because it only changes `docs/` and the workflows only run on backend and frontend changes.

**Not tested:**
- Scoring. No candidate has been through a scored comparison, and no benchmark script is on this branch.
- GPT-6.1 Terra and Gemini 3.8 Flash have never completed a call, and no local candidate has run on local hardware. Qwen3.5-9B has only run through OpenRouter.
- Whether LM Studio enforces a schema.
- The team hardware table is as reported on Oct 28. Only paul's card has been checked since.
- Nobody but paul has reviewed the gold results.

## Decisions made
None in the decisions log, and nothing goes there until there are results.

**Still open:** four questions to settle before the first scored run, because changing them afterwards would mean tuning the rule to the results. The doc recommends an answer to each:
- Do the gates cover all 50 core requests or the 24 that reach the LLM? Recommended: the 24.
- Does a mask that leaves people visible count as `unsafe`? Recommended: yes.
- Is "cheapest" based on cached or uncached cost? Recommended: uncached.
- Do requests for future tools run in this pass? Recommended: no.

## Next steps
1. **Team:** review the doc, especially the 24 requests (jean, for F-09) and the four questions above.
2. **Team:** decide who runs the comparison, and confirm the R-1 row in `docs/ROADMAP.md`, which still lists it as the exit check.
3. **Whoever runs it:** follow the doc's "Comparison plan". It needs OpenAI and Gemini credits, Ollama or LM Studio, and a reviewed benchmark script.

**pablo:** "mute it" and "silence it" currently become a whole-clip mute, because `_MUTE` accepts "it" (`agent/templates.py:28`), and no test covers it. Pronouns go to the LLM everywhere else in the parser, so the doc recommends the same here.

## Gotchas
- **The preflight numbers can't be reproduced from this branch,** because the prototype script that produced them isn't here.
- **Use the next free decision number.** The draft row says D-0XX, because open PRs may claim numbers first.
- **The R-1 row's branch column is empty on `main`.** This branch leaves `docs/ROADMAP.md` alone until the team confirms the row.
- **The parser's 26 of 34 correct is weak evidence,** because the set was written alongside the parser.
