---
name: handoff
description: Leave the current work ready for the next person. Runs the tests, writes or updates this branch's handoff entry in docs/handoffs/ (with base and head commit hashes), updates the owned feature rows in docs/ROADMAP.md, and commits them. Use when the user is stopping work, finishing a feature, or about to open a PR.
disable-model-invocation: true
---

# /handoff: leave the work ready for the next person

The format and rules are in `HANDOFF.md` ("Handoff entries"). Follow them exactly.

## Steps

1. **Check where we are.**
   - Refuse on `main`: handoffs belong to a feature branch. Offer to create `<name>/<topic>` from the current changes.
   - `git fetch origin`. Name: `git config user.name` (ask if it's unset; use a short lowercase first name in file names).
   - Uncommitted changes? Show `git status --short` and ask whether to commit them first (the entry should describe committed work).

2. **Run the tests.** Use the project's test command (see `README.md` / `HANDOFF.md`). If none exists yet, say so. Never claim tests pass without running them; record the real result in the entry.

3. **Gather facts.**
   - base: `git merge-base HEAD origin/main` (short hash) and `git describe --tags --abbrev=0 <base>` (or "untagged").
   - head: `git rev-parse --short HEAD`.
   - What changed: `git log --oneline <base>..HEAD` and `git diff --stat <base>..HEAD`.
   - Features: from the branch name, commits, or the sprint table in `docs/ROADMAP.md`; ask if unclear.
   - Ask the user (briefly, one message) for anything you can't infer: decisions made, next steps, gotchas. Fill in what you can from the session first.

4. **Write the entry** at `docs/handoffs/YYYY-MM-DD-<name>-<topic>.md`, using the template in `HANDOFF.md`.
   - If this person already has an entry for this branch dated today, update it instead of creating another.
   - Never edit anyone else's entry. If continuing someone's work, link their entry under "What changed".
   - Be concrete and honest: commands that actually work, what isn't done, what's untested.

5. **Update the shared docs, only where this work owns them.**
   - `docs/ROADMAP.md`: the Status column of this person's own feature rows and sprint rows only.
   - Architecture-level decisions: propose a row for the decisions log in `docs/ARCHITECTURE.md` and add it only if the user agrees.
   - If the work changed the contract or another shared file, remind the user to post a team heads-up and note it at the top of the entry.

6. **Commit** the entry and doc updates (`docs: handoff <topic>`), after showing the user the entry. Ask before pushing. If the work is ready for review, offer to open the PR, mentioning the entry in its description.
