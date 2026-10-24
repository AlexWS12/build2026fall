# CLAUDE.md

This repo is **Vibe Editing Agent** (working title), a 10-person Fall 2026 semester project: a web app where you edit video by chatting with an agent ("blur this person for the first half", "cut this", "mute him", "remove the people in the background"). **Live mode** (applying rules like "only I am visible" to a livestream) is the top-priority stretch. Everyone on the team works with Claude, often in parallel on separate branches.

## Where to find things
Don't guess about the project: read the file that answers the question.

| Question | Read |
|---|---|
| What is this, scope, features, stretches, constraints, limitations, tentative stack, background | `README.md` |
| How it's built, the edit-timeline contract, live mode design, decisions made, open questions | `docs/ARCHITECTURE.md` |
| What's left to build, feature IDs (F-xx), sprints and who owns what | `docs/ROADMAP.md` |
| Branches, PRs, handoff entries, versioning, sprint close | `HANDOFF.md` |
| Most recent work and what's next | `docs/handoffs/*.md` (current sprint), then `CHANGELOG.md` |
| Reference MVP the design comes from | https://github.com/Edugre/KnightHacks-26 (read-only inspiration, not our codebase) |

When someone asks a general question ("how does the project work?", "what's the scope?", "what should I do?"), answer from these files and cite them. When they're starting work, suggest `/b-sync`.

## Rules

**Workflow**
- Never commit to `main` directly. Work on `<name>/<topic>` branches and merge through PRs (`HANDOFF.md`). The one exception is the sprint lead's release commit and tag (`/b-release`).
- At the start of a work session, run or suggest `/b-sync`. Before the user stops, or when they say they're done, run or suggest `/b-handoff`.
- Keep `main` working: run the tests before every push, and rebase on `origin/main` before opening a PR.
- Ask before pushing, force-pushing, tagging or rewriting history.
- Commits are authored by the team member under their own git identity. Never add `Co-Authored-By: Claude` or any other Claude attribution, and never commit as Claude. Use type prefixes: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`.

**Architecture** (see `docs/ARCHITECTURE.md`)
- The agent never touches pixels or audio. It calls typed toolbox tools, which write ops to the edit timeline; only the renderer produces output.
- Hand edits from the UI go through the same toolbox so undo works for everything.
- Fail safe: privacy edits fail closed (unsure = masked); other edits ask or preview. Never skip errors silently.
- Keep live mode possible: standing rules stay in the contract, the renderer and AI modules keep a per-frame path, and nothing on the per-frame path calls the network.
- Every module has a stub mode and tests that run without a GPU. AI modules support `stub` / `local` / `remote` backends, because many teammates have weak GPUs or none (`docs/ARCHITECTURE.md`, "Compute"). Don't assume the user has a GPU: ask, or check.

**Tentative vs decided**
- Most of the tech stack is tentative. Don't present a candidate as a decision, and don't lock one in silently. When an architecture-level choice is made, add it to the decisions log in `docs/ARCHITECTURE.md` (ask the user first).
- When several options are open and the choice matters, lay out the trade-off briefly and let the user decide.

**Contract and shared files**
- Changing the edit-timeline contract, the toolbox's tool names or arguments, the API routes, or dependency and run files affects everyone. Tell the user to post a heads-up to the team first, bump `schema_version` for contract changes, and update the examples and tests (`HANDOFF.md`, "Shared files").

**Honesty**
- Quality and speed numbers come only from scripts (the benchmark harness), with the hardware stated. Never estimate a number and present it as measured.
- In handoff entries, say what actually works and what doesn't. "Not tested" is fine; claiming it works when it wasn't checked is not.

**Docs hygiene**
- Put information in the file that owns it (table above) instead of creating new top-level docs. Keep this file short; it's loaded into every session.
- Update a feature's status in `docs/ROADMAP.md` only for the rows the user owns.
- Never edit someone else's handoff entry; write a new one that links it.
- Secrets go in `.env` (gitignored). Videos, model weights and outputs are never committed.
