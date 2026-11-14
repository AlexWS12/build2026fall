# frontend

The web UI (F-07): React + Vite + TypeScript (D-009). See `docs/ARCHITECTURE.md` for how it fits with the backend.

```bash
npm install
npm run dev         # http://localhost:5173, /api is proxied to the backend on :8000
npm test            # Vitest, once (npm run test:watch to keep watching)
npm run lint        # oxlint
npm run typecheck   # tsc
npm run build
```

## Which backend the UI talks to
`VITE_API_MODE` picks it: unset = automatic (the real backend if `/api/health` answers, demo data otherwise), `http` = backend only, `mock` = demo data only. The status badge says which one answered. With `npm run dev` at the repo root, both start and the UI edits for real.

## State
The editing session is one explicit state machine in `src/state/session.ts` (states, events and the `transition` function), run against the API by `src/state/useSession.ts`. Add a new slow action there as an `Activity` kind, not as another boolean in a component.
