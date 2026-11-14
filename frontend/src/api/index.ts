import { httpClient, type ApiClient } from './client'
import { createMockClient } from './mock'

export type { ApiClient } from './client'

/**
 * Picks the backend once, on first use: the real one if `/api/health` answers, demo data
 * otherwise. So `npm run dev` at the repo root (backend + UI) edits for real, and the UI alone
 * still opens. The status badge always says which one answered.
 */
export function createAutoClient(
  real: ApiClient = httpClient,
  demo: () => ApiClient = createMockClient,
): ApiClient {
  let chosen: Promise<ApiClient> | null = null
  const pick = () => (chosen ??= real.health().then(() => real, demo))
  return {
    health: () => pick().then((c) => c.health()),
    listProjects: () => pick().then((c) => c.listProjects()),
    createProject: (file) => pick().then((c) => c.createProject(file)),
    getProject: (id) => pick().then((c) => c.getProject(id)),
    // Only used to reopen a stored project, which demo data never has.
    videoUrl: (id) => real.videoUrl(id),
    sendRequest: (id, text) => pick().then((c) => c.sendRequest(id, text)),
    getState: (id) => pick().then((c) => c.getState(id)),
    getHistory: (id) => pick().then((c) => c.getHistory(id)),
    getMessages: (id) => pick().then((c) => c.getMessages(id)),
    getTracks: (id) => pick().then((c) => c.getTracks(id)),
    undo: (id) => pick().then((c) => c.undo(id)),
    redo: (id) => pick().then((c) => c.redo(id)),
    analyse: (id) => pick().then((c) => c.analyse(id)),
    exportVideo: (id) => pick().then((c) => c.exportVideo(id)),
    transcribe: (id) => pick().then((c) => c.transcribe(id)),
  }
}

const mode: unknown = import.meta.env.VITE_API_MODE

/** `VITE_API_MODE`: `http` = backend only, `mock` = demo data only, unset = pick automatically. */
export const defaultClient: ApiClient =
  mode === 'http' ? httpClient : mode === 'mock' ? createMockClient() : createAutoClient()
