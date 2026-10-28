---
author: eduardo
date: 2026-10-27
branch: eduardo/web-ui-shell
features: [F-07]
base: untagged @ 94d4d8e
head: d06af85
status: ready for review
---

# F-07 shell: a React + Vite UI with upload, player, chat and timeline, running on a mock API

> **Heads-up:** this changes shared files: the root `package.json` and `package-lock.json` (new scripts, and `npm install` now also installs the frontend) and `.github/workflows/` (new `frontend.yml`). It also adds `frontend/src/api/` to HANDOFF.md's shared files, because it holds the UI's side of the API routes. Post in the team chat before merging.

## What changed
- **`frontend/`:** Vite + React 19 + TypeScript (D-009), with its own `package.json` and lockfile (D-011).
  - **Layout:** a header with a backend status badge, the **Player** and **Timeline** on the left, **Upload** and **Chat** on the right. Below 860px wide it's one column.
  - **Player:** plays the uploaded file locally, with an object URL. Nothing is sent anywhere for playback.
  - **Chat:** disabled until there's a project. Sending a request shows it, then the reply, then refreshes the timeline.
  - **Timeline:** read-only. One lane per masked track, then cuts, mutes and audio filters, placed by source time over the video's duration. Each span's tooltip shows the effect, `op_id` and `request_id`. Open-ended spans (`end: null`) run to the end of the video.
- **`frontend/src/api/`:**
  - `ApiClient` with `health`, `createProject`, `sendRequest` and `getState`.
  - **The mock is the default,** so the UI works with no backend. Every upload becomes a project whose timeline is the contract's own `edit_session.state.json`, imported straight from `backend/src/vibe/contract/examples/` so it can't drift from the contract.
  - `VITE_API_MODE=http` switches to the HTTP client, through the Vite `/api` proxy to `:8000`.
  - `types.ts` holds hand-written copies of `TimelineState` and `ProjectInfo`, until the generated types (the F-07 extra) replace them.
- **Root scripts:** `npm run dev` starts the backend and the frontend together. `npm test` and `npm run lint` cover both sides, and each side also has its own `:backend` / `:frontend` scripts.
- **CI:** `.github/workflows/frontend.yml` runs `npm ci`, oxlint, `tsc`, Vitest and `vite build` on Node 22. It runs on changes to `frontend/**` and to the contract examples, because the mock imports them.
- **Docs:** the README's "Run the app" section, the CI line in ARCHITECTURE's "Code layout", and HANDOFF's shared files.

## How to check it
```bash
npm install          # root scripts, then the frontend's packages
npm run dev          # http://localhost:5173: badge says "Backend: mock (stub)"; upload any video, then chat
npm test             # backend: 127 passed; frontend: 12 passed
npm run lint         # Ruff, then oxlint + tsc
```
Verified on 2026-10-27 on macOS (Node 22.16). In the browser:
- an upload through the mock drew all 5 timeline lanes;
- "blur all faces" got the mock reply;
- there were no console errors;
- at 375px wide, there was no sideways scroll;
- under `npm run dev`, `/api/health` reached the real backend through the proxy.

I replayed the CI steps on a clean copy of `frontend/` and they passed. `frontend.yml` itself hasn't run on GitHub until this PR. **Not tested:** playing a real video (the browser check used a fake file), and `VITE_API_MODE=http` beyond `/api/health`, because the other routes don't exist yet.

## Decisions made
Eduardo made these on 2026-10-27. None are architecture-level, so there are no decisions-log rows:
- **A hand-written mock `ApiClient`, not MSW.** It's simpler, with no service worker. MSW can come later if we want to exercise the HTTP client in tests.
- **CSS Modules, not Tailwind,** so the team doesn't get a styling decision by accident.
- **oxlint, not ESLint,** because that's what the current Vite template ships.
- **Proposed API routes** (the mock and the HTTP client both use them; nothing on the backend yet):
  - `POST /api/projects` (multipart `file`) → `ProjectInfo`;
  - `POST /api/projects/{id}/requests` (`{text}`) → `{request_id, reply}`;
  - `GET /api/projects/{id}/state` → `TimelineState`.

  The backend card that builds them (see Alex's storage handoff) settles the real shapes, and the UI follows.

## Next steps
- **Review and merge** after the heads-up. F-07 stays `in progress` until the extra lands.
- **F-07 extra (phase 4), a follow-up PR:**
  - `uv run python -m vibe.api` writes the OpenAPI spec to a committed file, and a backend test fails if it's stale;
  - `npm run gen:api` turns it into `frontend/src/api/generated/` with `openapi-typescript`, and frontend CI fails on stale types;
  - generating from a committed file means frontend CI never needs Python;
  - `types.ts` gets replaced as routes start returning those models. Today the spec only has `/api/health`.
- **API routes card:** propose it for Sprint 2, starting from the routes above.
- **Later UI work** builds on this shell:
  - the review queue (F-41) and hand fixes (F-42) on the timeline and player;
  - the player showing rendered previews once F-04 exists.

## Gotchas
- **The mock reads files outside `frontend/`.** `vite.config.ts` allows the dev server to read `backend/src/vibe/contract/examples`, and `tsconfig.app.json` turns on `resolveJsonModule`. Moving or renaming `edit_session.state.json` breaks the mock, and frontend CI catches it.
- **Vitest globals are off,** so `src/test/setup.ts` registers Testing Library's `cleanup` and stubs `URL.createObjectURL`, which jsdom lacks.
- **The badge says "mock" in mock mode, not "ok",** so nobody mistakes the mock for a running backend.
- **Plain helpers go in their own module, not in a component file.** oxlint warns when a component file exports anything else, because it breaks fast refresh. `TimelineView`'s helpers live in `components/timeline.ts` for that reason.
