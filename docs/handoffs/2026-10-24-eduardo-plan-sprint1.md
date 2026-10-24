---
author: eduardo
date: 2026-10-24
branch: eduardo/plan-sprint1
features: []
base: untagged @ 2d1a585
head: d3f41ac
status: ready for review
---

# Sprint schedule up to demo day, and Sprint 1 cards open to claim

> **Heads-up:** this sets the sprint calendar and how work gets picked up for the whole team. Post it in the team chat when the PR opens.

## What changed
- `docs/ROADMAP.md`, new **Schedule** section: weekly sprints, Tuesday → Tuesday, starting 2026-10-20. Each Tuesday closes one sprint and starts the next. There are 7 sprints (v0.1.0 to v0.7.0), and the last one is a 3-day demo sprint (2026-12-01 → 2026-12-03) before **demo day on Friday 2026-12-04**. The focus for Sprints 2–7 is rough and gets re-planned each week.
- New **Claiming a card** section: nobody is assigned work. Members claim cards by putting their name and branch in the row and saying so in the team chat. A card can be split by files. Research cards end in a decisions-log row plus a write-up.
- **Sprint 1 (2026-10-20 → 2026-10-27)**: 12 open cards.
  - Research: R-1 planner LLM (an open choice between any hosted provider and an open-weight model), R-2 project storage and analysis cache, R-3 detection/tracking/identity model shortlist with licenses, R-4 data policy (frames to the cloud?) plus boxes vs masks.
  - Build: F-01, F-02 (top priority), F-03, F-06, F-07, F-08 (plus the team GPU survey), F-09, F-50 (needs the RTX 5070).
  - Each card has an exit check. Risks and dependencies are listed.
- Updated the "Notes for whoever plans the sprints" and the status line. The sprint template now uses Card/Owner columns and has a sprint lead line.
- Backlog statuses were not changed: each owner updates their own rows.
- Builds on [2026-10-24-eduardo-stack-sprint1.md](2026-10-24-eduardo-stack-sprint1.md).

## How to check it
Read "Sprints" in `docs/ROADMAP.md`. There are no tests yet, because there's no code and no test command.

## Decisions made
- Weekly Tuesday → Tuesday sprints, with the schedule planned backward from demo day.
- Members claim cards instead of being assigned work, and no areas are assigned.
- The sprint lead is Alex or Eduardo.
- Sprint 1 settles the open stack questions through research cards. There's no backend-stack card, because the team is close to settling the proposed stack. R-1 deliberately doesn't limit the planner to one provider.
- No architecture decisions-log rows were added. These are process choices, not architecture choices.

## Next steps
- Post the schedule and the Sprint 1 cards in the team chat, get a review, and merge.
- Members claim Sprint 1 cards. F-02 (the contract) should be claimed first.
- Confirm who is the Sprint 1 lead before the 2026-10-27 close (`/b-release`, tag `v0.1.0`).
- Whoever takes R-1 should update the planner candidates in `README.md` and `docs/ARCHITECTURE.md`, which still name only `claude-opus-5` / `claude-sonnet-5`.

## Gotchas
- Sprint 1 started on 2026-10-20 but was only planned on 2026-10-24, so about 3 days are left for 12 unclaimed cards. Expect several cards to roll into Sprint 2. F-02 is the one that can't slip.
- F-50 can only run on the RTX 5070 (or with remote access to it). If nobody with access claims it, it slips, but it has to land before the live decision in Sprint 4.
- Thanksgiving (2026-11-26) falls in Sprint 6.
