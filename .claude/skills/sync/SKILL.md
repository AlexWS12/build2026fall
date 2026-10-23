---
name: sync
description: Catch up on the project before starting work. Fetches main, finds which handoff entries, contract changes and commits landed since the current branch started (or summarizes the current sprint if there's no branch), and reports the version, what's new, what affects this branch and what's next. Use at the start of a work session, or when asked "what's the current state", "what changed", "what should I work on".
---

# /sync: catch up before working

Read-only, except for `git fetch`. Never rebase, merge, pull into a branch with local changes, or edit files without asking.

## Steps

1. **Fetch and locate.**
   - `git fetch origin --tags --prune`
   - Current branch: `git branch --show-current`. Working tree clean? `git status --short`.
   - Current version: `git describe --tags --abbrev=0 origin/main` (no tag yet = "untagged, before v0.1.0").
   - If `origin/main` doesn't exist yet (brand-new repo), skip to step 3 and use the local files.

2. **Find what's new.**
   - **On a feature branch:** base = `git merge-base HEAD origin/main`. Then:
     - new handoff entries: `git diff --name-only --diff-filter=A <base> origin/main -- docs/handoffs/`
     - commits on main since then: `git log --oneline <base>..origin/main`
     - contract and shared-file changes: `git diff --stat <base> origin/main` and look for the shared files listed in `HANDOFF.md` ("Shared files"): the contract, the toolbox, API routes, dependency and run files, `docs/ARCHITECTURE.md`
     - overlap with this branch: `git diff --name-only <base> HEAD` against `git diff --name-only <base> origin/main`
   - **On `main` or no branch yet:** if the tree is clean and `main` is behind, ask before running `git pull --ff-only`. "New" means every entry currently in `docs/handoffs/` (not `archive/`).

3. **Read.** Read the new handoff entries in full. Read the latest `CHANGELOG.md` section and the current sprint in `docs/ROADMAP.md`. If the contract or `docs/ARCHITECTURE.md` changed, read the diff. If this is the person's first time (no branch, few commits by them), also skim `README.md` so you can answer project questions.

4. **Report**, short and in this order:
   - **Version and position:** e.g. "main is at v0.2.0 + 7 commits (a1b2c3d). Your branch `maria/audio-mute` started at 3f2e1d0, 7 commits behind."
   - **What's new since you started:** one line per handoff entry (author, feature, what changed), with the file path.
   - **What affects you:** contract or shared-file changes, files changed on both main and this branch (likely conflicts), and decisions-log additions. Say "nothing" if there are none.
   - **What's next for you:** this person's assignments in the current sprint (match `git config user.name` or the branch prefix against the sprint table), plus "Next steps" from their own latest handoff entry. If no sprint is planned yet, say so and point to the planning notes in `docs/ROADMAP.md`.
   - **Suggested action:** e.g. "Rebase on main before continuing (you touch `render/` and so did jose/cut-tool)." Ask before doing it.
