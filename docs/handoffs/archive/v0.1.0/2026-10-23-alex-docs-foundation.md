---
author: alex
date: 2026-10-23
branch: main (first commit of the repo)
features: []
base: untagged @ none (empty repo)
head: the first commit (see `git log`)
status: done
---

# Project docs foundation: scope, architecture draft, workflow, handoffs

## What changed
- Wrote the project docs from a planning session with Claude: `README.md` (pitch, background, scope, live mode, constraints, tentative stack, limitations), `CLAUDE.md` (rules and reading map for Claude), `HANDOFF.md` (branches, handoff entries, versioning, sprint close), `docs/ARCHITECTURE.md` (layers, human in the loop, draft contract v0, live mode, Knight Hacks lessons, decisions log, open questions), `docs/ROADMAP.md` (feature backlog F-01…F-63, sprint planning notes, sprint template), `CHANGELOG.md`.
- Added Claude Code skills `/sync`, `/handoff`, `/release` in `.claude/skills/`, plus a `.gitignore`.
- No code yet.

## How to check it
Open the repo in Claude Code and run `/sync`, or ask "how does this project work?". Claude should answer from the docs without any other context.

## Decisions made
See the decisions log in `docs/ARCHITECTURE.md` (D-001 to D-008): agent → deterministic toolbox architecture; hybrid inference (a cloud LLM is allowed for planning, heavy models on our GPUs); web app; SemVer tags bumped at sprint close starting at v0.1.0; one file per handoff; live mode as the top-priority stretch, designed for from v0 (standing rules in the contract, a per-frame renderer path); compute tiers, so CPU-only laptops can work through stub/local/remote model backends and a shared analysis cache, with Alex's RTX 5070 as the reference machine.

We deliberately did **not** pick models, libraries or a code layout. The tech stack in the README is candidates only.

## Next steps
1. **Plan the sprints** (anyone can do this with Claude): follow "Notes for whoever plans the sprints" in `docs/ROADMAP.md`. Decide sprint length and dates, assign the foundation features (F-01–F-09) first so all 10 people can build against stubs, and schedule the live feasibility spike (F-50) early. Fill in the Sprints section using its template.
2. **Finish the compute picture** (open questions in `docs/ARCHITECTURE.md`): confirm the 5070's VRAM, list what GPU each teammate has, and check for university or cloud GPU credits.
3. **Pick a sprint lead** for each sprint (they run `/release` at its close; the first close tags `v0.1.0`).

## Gotchas
- The Knight Hacks repo (https://github.com/Edugre/KnightHacks-26) is inspiration, not a base: start fresh, reuse ideas, and copy code only where it fits. Its `IMPLEMENTATION.md` section 5 and `docs/STACK.md` hold measured numbers (detector speeds, the live feasibility estimate) on AMD hardware we may not have.
- Live mode is a stretch, but it's the feature the project lead cares about most. Don't make choices that rule it out (see "Live mode" in `docs/ARCHITECTURE.md`).
