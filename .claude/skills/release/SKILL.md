---
name: release
description: Close a sprint. Rolls the sprint's handoff entries into CHANGELOG.md, archives them under docs/handoffs/archive/<version>/, closes the sprint in docs/ROADMAP.md, commits, and tags the new version. Only for the sprint lead at sprint close.
disable-model-invocation: true
---

# /release: close a sprint and tag the version

The rules are in `HANDOFF.md` ("Versions" and "Sprint close"). Only the sprint lead runs this, on `main`.

## Steps

1. **Preconditions.** On `main`, clean working tree, up to date with `origin/main` (`git fetch origin --tags` then `git status`). The tests pass on `main`. If any of these fail, stop and say why.

2. **Pick the version.** Latest tag: `git describe --tags --abbrev=0` (no tag means this is `v0.1.0`). A sprint close bumps MINOR (`v0.3.0` → `v0.4.0`); a fix release between sprints bumps PATCH. Confirm the number with the user.

3. **Write the changelog.** Read every entry in `docs/handoffs/` (not `archive/`). Add a section `## [0.N.0] - YYYY-MM-DD` to `CHANGELOG.md` under `[Unreleased]`, grouped as Added / Changed / Fixed / Removed, one line per user-visible change with its feature ID. Move anything already listed under `[Unreleased]` into it. Show the user the section before saving.

4. **Archive the entries.** `git mv docs/handoffs/*.md docs/handoffs/archive/v0.N.0/`. Leave `docs/handoffs/` empty except for `archive/` and `.gitkeep`.

5. **Close the sprint** in `docs/ROADMAP.md`: fill in its "Closed" line (what shipped, what moved to the next sprint) and update the feature statuses that are now `done`.

6. **Commit and tag.** `git commit -m "chore(release): v0.N.0"`, then `git tag -a v0.N.0 -m "Sprint N: <goal>"`. **Ask before pushing**, then run `git push origin main --follow-tags`.

7. **Report:** version, tagged commit hash, and a 3-5 line summary of the sprint for the team chat.
