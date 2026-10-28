---
author: eduardo
date: 2026-10-27
branch: eduardo/openapi-types
features: [F-07]
base: untagged @ 94d4d8e
head: 6ca90ba
status: ready for review
---

# F-07 extra: the UI's API types are generated from the backend's OpenAPI spec, and go stale loudly

> **Heads-up:** this changes shared files:
> - the API: `backend/src/vibe/api/app.py` (`/api/health` now returns a `Health` model; same JSON), plus the new `openapi.py`, `__main__.py` and committed `openapi.json`;
> - the UI side of the API: `frontend/src/api/types.ts` and the new `frontend/src/api/generated/`;
> - CI: `.github/workflows/frontend.yml` (new check step and trigger path);
> - package files: `frontend/package.json` and `frontend/package-lock.json` (new dev dependency, `gen:api` script, an npm `overrides` entry).
>
> **From now on, anyone changing a route or a response model must run the two regenerate commands below and commit the result.** Post in the team chat before merging.
>
> This branch is stacked on [the shell](2026-10-27-eduardo-web-ui-shell.md) (`eduardo/web-ui-shell`, PR #8). Merge that first.

## What changed
- **Backend:**
  - `uv run python -m vibe.api` writes `app.openapi()` to `backend/src/vibe/api/openapi.json`; `--check` only checks it. It mirrors `python -m vibe.contract`.
  - `tests/test_api.py` fails if the committed spec differs from the app.
  - `/api/health` returns a `Health` pydantic model instead of `dict[str, str]`, so the spec carries `backend: "stub" | "local" | "remote"`. The JSON it returns is unchanged.
- **Frontend:**
  - `openapi-typescript` 7.13 is a dev dependency. `npm run gen:api` turns the committed spec into `src/api/generated/schema.ts`. It reads the file, not a running server, so frontend CI never needs Python.
  - `types.ts` takes `Health` from the generated types. Everything else stays hand-written until its route lands.
- **CI:** `frontend.yml` runs `gen:api` after `npm ci` and fails if `git diff --exit-code src/api/generated` shows a change. It now also runs when `backend/src/vibe/api/openapi.json` changes.
- **Docs:** README "Run the app" (how to regenerate), and the F-07 row in ROADMAP (this branch added).

## How to check it
```bash
npm install                                      # picks up openapi-typescript
npm test                                         # backend: 128 passed; frontend: 12 passed
npm run lint                                     # Ruff, then oxlint + tsc
uv run --directory backend python -m vibe.api    # no output: the spec is current
npm --prefix frontend run gen:api                # rewrites generated/schema.ts with no diff
```
Verified on 2026-10-27 on macOS (Node 22.16): all of the above, plus `vite build`. I also checked that staleness fails, with a throwaway `uptime_s` field on `Health` (reverted):
- route changed, nothing regenerated → `test_openapi_spec_is_up_to_date` fails;
- spec regenerated but not the types → the CI step's `git diff --exit-code` exits 1.

**Not tested:** `frontend.yml` hasn't run on GitHub yet; the check step was replayed locally.

## Decisions made
Eduardo made these on 2026-10-27:
- **`openapi-typescript` as the generator.** It's types only, no runtime client. The `ApiClient` stays hand-written and uses the types. This is the D-009 extra, not a new architecture-level choice, so there's no decisions-log row.
- **A `Health` response model** so the generated type is as precise as the hand-written one was.
- **An npm `overrides` entry** pointing `openapi-typescript`'s `typescript` peer at our TypeScript 6. Its latest release (7.13.0) still declares `typescript ^5.x`. It only uses TypeScript to print the file, and the output builds and typechecks under 6. This avoids making everyone use `--legacy-peer-deps`.

## Next steps
- **Review and merge** after the shell (PR #8) and the heads-up.
- **API routes card (Sprint 2):** as each route lands, give it a response model, regenerate, and swap the hand-written type in `types.ts` (`ProjectInfo`, `ChatReply`, `TimelineState`) for the generated one. Then `types.ts` can be deleted.

## Gotchas
- **Each workflow catches a different kind of staleness.** A route change without regenerating anything is caught by the backend test, not frontend CI: frontend CI only reads the committed spec. A regenerated spec with stale types is caught by frontend CI.
- **Return a pydantic model, not a `dict`,** from routes the UI uses. A `dict[str, str]` return becomes `{[key: string]: string}` in the generated types.
- **Don't hand-edit `generated/`**: CI regenerates it and fails on any difference.
- **Remove the `overrides` entry** once `openapi-typescript` supports TypeScript 6.
