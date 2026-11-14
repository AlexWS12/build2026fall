import type {
  AnalysisReply,
  ChatRecord,
  ChatReply,
  ExportReply,
  Health,
  HistoryReply,
  ProjectDetail,
  ProjectInfo,
  TimelineState,
  TracksReply,
} from './types'

/**
 * Everything the UI asks of the backend. `httpClient` talks to the real routes; the mock
 * (mock.ts) answers with demo data so the UI works with no backend running.
 */
export interface ApiClient {
  health(): Promise<Health>
  /** Projects on this server, newest first. */
  listProjects(): Promise<ProjectInfo[]>
  /** Upload a video and create a project on it. */
  createProject(file: File): Promise<ProjectInfo>
  getProject(projectId: string): Promise<ProjectDetail>
  /** Where the project's source video plays from. */
  videoUrl(projectId: string): string
  /** Send a chat request; the agent plans it and the server applies it to the timeline. */
  sendRequest(projectId: string, text: string): Promise<ChatReply>
  getState(projectId: string): Promise<TimelineState>
  getHistory(projectId: string): Promise<HistoryReply>
  getMessages(projectId: string): Promise<ChatRecord[]>
  getTracks(projectId: string): Promise<TracksReply>
  /** Undo the latest request still in effect. */
  undo(projectId: string): Promise<HistoryReply>
  redo(projectId: string): Promise<HistoryReply>
  /** Find and track people, faces and plates in the video (slow the first time). */
  analyse(projectId: string): Promise<AnalysisReply>
  /** Render the timeline to a new file. */
  exportVideo(projectId: string): Promise<ExportReply>
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** The server's own words for an error, when it sent some (FastAPI's `detail`). */
async function errorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body: unknown = await response.json()
    const detail = (body as { detail?: unknown } | null)?.detail
    if (typeof detail === 'string' && detail) return detail
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => (item as { msg?: unknown } | null)?.msg)
        .filter((msg): msg is string => typeof msg === 'string')
      if (messages.length) return messages.join('; ')
    }
  } catch {
    // Not JSON (a proxy error page, an empty body): fall through to the status line.
  }
  return fallback
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    const fallback = `${init?.method ?? 'GET'} ${path} failed: ${response.status}`
    throw new ApiError(response.status, await errorMessage(response, fallback))
  }
  return (await response.json()) as T
}

const project = (projectId: string, rest = '') =>
  `/api/projects/${encodeURIComponent(projectId)}${rest}`
const post = <T>(path: string) => call<T>(path, { method: 'POST' })

export const httpClient: ApiClient = {
  health: () => call('/api/health'),
  listProjects: () => call('/api/projects'),
  createProject: (file) => {
    const body = new FormData()
    body.append('file', file)
    return call('/api/projects', { method: 'POST', body })
  },
  getProject: (projectId) => call(project(projectId)),
  videoUrl: (projectId) => project(projectId, '/video'),
  sendRequest: (projectId, text) =>
    call(project(projectId, '/requests'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }),
  getState: (projectId) => call(project(projectId, '/state')),
  getHistory: (projectId) => call(project(projectId, '/history')),
  getMessages: (projectId) => call(project(projectId, '/messages')),
  getTracks: (projectId) => call(project(projectId, '/tracks')),
  undo: (projectId) => post(project(projectId, '/undo')),
  redo: (projectId) => post(project(projectId, '/redo')),
  analyse: (projectId) => post(project(projectId, '/analysis')),
  exportVideo: (projectId) => post(project(projectId, '/exports')),
}
