---
author: eduardo
date: 2026-10-24
branch: eduardo/rename-team-skills
features: []
base: untagged @ baa46b5
head: 8187b02
status: ready for review
---

# Team skills renamed to /b-sync, /b-handoff, /b-release

> **Heads-up:** this renames the team commands (shared files). After it merges, use `/b-sync`, `/b-handoff` and `/b-release`. The old `/sync`, `/handoff` and `/release` no longer exist in the repo. PR #2.

## What changed
- Renamed `.claude/skills/{sync,handoff,release}` to `.claude/skills/{b-sync,b-handoff,b-release}`, and updated each skill's `name:` frontmatter and heading.
- Updated every command reference in `CLAUDE.md`, `HANDOFF.md`, `README.md`, `CHANGELOG.md`, `docs/ROADMAP.md` and `docs/ARCHITECTURE.md`. Realigned the workflow diagram in `HANDOFF.md`, which the longer names had pushed out of line.
- Left alex's entry ([2026-10-23-alex-docs-foundation.md](2026-10-23-alex-docs-foundation.md)) with the old names, because we don't edit others' entries.

## How to check it
Check out the branch and open Claude Code. `/b-sync` and `/b-handoff` should appear in the skill list and run the project skills. Verified on 2026-10-24: Claude Code listed `b-sync`, and `/b-handoff` loaded `.claude/skills/b-handoff` (this entry was written with it). `/b-release` was not run. There are no tests yet because there's no code.

## Decisions made
- **The `b-` prefix on all three team skills.** In Claude Code, a personal skill in `~/.claude/skills/` with the same name overrides the project skill, without any error. `sync`, `handoff` and `release` are common names, so anyone with a personal skill under one of them would get theirs instead of ours. This already happened: a personal `/handoff` wrote a session note instead of a team handoff entry. Renaming only `handoff` was rejected because the other two names are just as likely to collide.

## Next steps
- Post the team heads-up (it can cover PR #1 too), then get a review and merge PR #2.
- New skills added to `.claude/skills/` should also use the `b-` prefix.

## Gotchas
- PR #1 (`eduardo/stack-sprint1`) and this PR both edit `README.md` and `docs/ARCHITECTURE.md`, on different lines. Whichever merges second may need a quick rebase.
- If someone's `/b-sync` does something unexpected, check `~/.claude/skills/` for a personal skill with the same name.
