# How we work: branches, handoffs, versions

There are 10 people, each working in parallel with Claude. The rules below make two things true:
1. **Anyone can start work and get the current state in one command** (`/sync`).
2. **Anyone can stop work and leave it ready for the next person in one command** (`/handoff`).

The `/sync`, `/handoff` and `/release` commands live in `.claude/skills/` and run in Claude Code. Each one follows the steps written here, so if a command isn't available you can do the same steps by hand.

## The loop

```
 /sync  ──►  branch from main  ──►  work + commit  ──►  /handoff  ──►  PR  ──►  merge to main
   ▲                                                                                │
   └────────────────────────── next person (or you, tomorrow) ─────────────────────┘
```

1. **Start:** run `/sync`. Claude fetches `main` and tells you the current version, what changed since your branch started (or this sprint's handoffs, if you have no branch), whether the contract changed, and what's next for you in [docs/ROADMAP.md](docs/ROADMAP.md).
2. **Branch:** branch from an up-to-date `main` as `<your-name>/<topic>`, e.g. `maria/audio-mute`, `jose/cut-tool`. One branch per feature or piece of one.
3. **Work:** commit often. Commit messages use a type prefix: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`, e.g. `feat: mute a time range`.
4. **Stop:** run `/handoff` whenever you stop for a while, not only when the feature is done. It runs the tests, writes your handoff entry, and updates your feature's status in the roadmap.
5. **PR:** before opening a PR, rebase on the latest `main` (`git pull --rebase origin main`) and make sure the tests pass. The PR includes your handoff entry. At least one other person reviews it.
6. **`main` always works.** Never push straight to `main` (the only exception is the sprint lead's release commit and tag). If your change breaks `main`, fixing it comes before anything else.

## Shared files
Some files every feature depends on. **Post in the team chat before changing them**, and expect `/sync` to flag them to everyone:
- the edit timeline contract (its definition in code once it exists; today, "The contract" in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md));
- the toolbox's tool names and arguments;
- the API routes between UI and backend;
- dependency files, run scripts, CI config.

The list grows as code lands: whoever creates a shared file adds it here.

## Handoff entries

### What they are
**A handoff entry is a short note that says where you left off.** Each one is its own file, so 10 people can write them at the same time without merge conflicts:

```
docs/handoffs/2026-10-25-maria-audio-mute.md
docs/handoffs/2026-10-25-jose-cut-tool.md
docs/handoffs/2026-10-26-ana-ui-timeline.md
```

File name: `YYYY-MM-DD-<name>-<topic>.md`. **Never edit someone else's entry.** If you take over their work, write your own entry and link theirs.

### Lifecycle
1. **Written** on your branch by `/handoff`. Running it again on the same day updates your entry instead of creating a new one.
2. **Merged** to `main` with your PR.
3. **Read** by everyone's `/sync` during the sprint.
4. **Archived at sprint close** by `/release`: the sprint's entries are summarized into [CHANGELOG.md](CHANGELOG.md) and moved to `docs/handoffs/archive/vX.Y.Z/`. They're never deleted; the archive answers "why did we do it this way?" later.

So `docs/handoffs/` only ever holds the current sprint's entries.

### Template
```markdown
---
author: <name>
date: YYYY-MM-DD
branch: <name>/<topic>
features: [F-xx]
base: <version tag or "untagged"> @ <short hash of the main commit the branch started from>
head: <short hash of the last commit in this handoff>
status: in progress | ready for review | done | blocked
---

# <One-line summary of what you did>

## What changed
- ...

## How to check it
<Commands to run, or what to click, to see it work. "Not testable yet" is a valid answer; say why.>

## Decisions made
<Anything you decided that others should know. If it's an architecture-level choice, also add it to the decisions log in docs/ARCHITECTURE.md.>

## Next steps
- ...

## Gotchas
<What surprised you, what's fragile, what the next person shouldn't relearn.>
```

## Versions

### The two IDs every piece of work has
- **The commit hash** (e.g. `a1b2c3d`) is the exact ID of one commit. Handoffs record it so anyone can see precisely what code a note refers to.
- **The version** (e.g. `v0.2.0`) is a readable name for a commit at the end of a sprint, made with a **git tag**. A tag is a permanent label stuck on one commit, like a bookmark. `git checkout v0.2.0` gets exactly that code, and GitHub lists each tag as a release.

### Numbering (SemVer)
`vMAJOR.MINOR.PATCH`:
- **`0.x.y` while the project is in development.** The leading 0 means "things may still change in breaking ways". We start at `v0.1.0`.
- **MINOR goes up once per sprint** (`v0.1.0` → `v0.2.0` → ...).
- **PATCH goes up for a fix to a released version between sprints** (`v0.2.0` → `v0.2.1`), rarely needed.
- **`v1.0.0` is the first version the team calls usable end to end**, decided by the team.

**PRs never change the version.** Only a sprint close does, so 10 parallel PRs never conflict on it.

The edit timeline contract has its **own** `schema_version`, separate from the app version (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)).

### Sprint close (`/release`)
Done by the sprint lead, on an up-to-date `main`:
1. Summarize the sprint's handoff entries into a new `## [0.N.0] - YYYY-MM-DD` section of [CHANGELOG.md](CHANGELOG.md), under Added / Changed / Fixed / Removed.
2. Move the entries to `docs/handoffs/archive/v0.N.0/`.
3. Fill in the sprint's "Closed" line in [docs/ROADMAP.md](docs/ROADMAP.md).
4. Commit (`chore(release): v0.N.0`), tag (`git tag -a v0.N.0 -m "Sprint N"`), then push the commit and the tag (`git push origin main --follow-tags`).
