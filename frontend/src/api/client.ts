import type { ChatReply, Health, ProjectInfo, TimelineState } from './types'

/**
 * Everything the UI asks of the backend. The shell runs against the mock by default, so it
 * works with no backend. Only `/api/health` exists on the backend today; the other routes are
 * a proposal until the API routes card settles them.
 */
export interface ApiClient {
  health(): Promise<Health>
  /** Upload a video and create a project on it. */
  createProject(file: File): Promise<ProjectInfo>
  /** Send a chat request; the agent turns it into ops on the project's timeline. */
  sendRequest(projectId: string, text: string): Promise<ChatReply>
  getState(projectId: string): Promise<TimelineState>
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    throw new ApiError(response.status, `${init?.method ?? 'GET'} ${path} failed: ${response.status}`)
  }
  return (await response.json()) as T
}

export const httpClient: ApiClient = {
  health: () => call('/api/health'),
  createProject: (file) => {
    const body = new FormData()
    body.append('file', file)
    return call('/api/projects', { method: 'POST', body })
  },
  sendRequest: (projectId, text) =>
    call(`/api/projects/${encodeURIComponent(projectId)}/requests`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }),
  getState: (projectId) => call(`/api/projects/${encodeURIComponent(projectId)}/state`),
}
