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
