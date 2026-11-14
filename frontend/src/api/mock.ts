import editSession from '../../../backend/src/vibe/contract/examples/edit_session.state.json'
import { ApiError, type ApiClient } from './client'
import type { HistoryReply, ProjectInfo, TimelineState } from './types'

// The contract's own example, so the timeline view always draws a state the backend can fold.
const exampleState = editSession as TimelineState

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

/** The example's requests, as the history route would list them. */
function exampleHistory(): HistoryReply {
  const ids = new Set<string>()
  for (const span of [
    ...Object.values(exampleState.masks).flat(),
    ...exampleState.mutes,
    ...exampleState.audio_filters,
  ]) {
    ids.add(span.request_id)
  }
  return {
    requests: [...ids].sort().map((request_id) => ({
      request_id,
      source: 'agent',
      status: 'applied',
      kinds: [],
      text: null,
      reply: null,
    })),
    // Demo data is read-only: there is no op log behind it to undo.
    can_undo: false,
    can_redo: false,
  }
}

/**
 * A fake backend with demo data: any upload becomes a project whose timeline is the contract
 * example. It never claims an edit happened, so every request replies `not_applied`.
 */
export function createMockClient({ delayMs = 300 }: { delayMs?: number } = {}): ApiClient {
  let requests = 0
  let current: ProjectInfo | null = null

  const unavailable = (what: string) =>
    new ApiError(503, `${what} needs the real backend; this is demo data.`)

  return {
    async health() {
      await wait(delayMs)
      // Not "ok": the badge should never suggest a real backend answered.
      return { status: 'mock', backend: 'stub' }
    },
    async listProjects() {
      await wait(delayMs)
      return []
    },
    async createProject(file) {
      await wait(delayMs)
      const dot = file.name.lastIndexOf('.')
      current = {
        storage_version: 1,
        project_id: 'prj_mock',
        name: dot > 0 ? file.name.slice(0, dot) : file.name,
        created_at: new Date().toISOString(),
        video: {
          sha256: '0'.repeat(64),
          ext: dot > 0 ? file.name.slice(dot).toLowerCase() : '',
          original_name: file.name,
        },
      }
      return current
    },
    async getProject(projectId) {
      await wait(delayMs)
      if (!current || current.project_id !== projectId) {
        throw new ApiError(404, `no project '${projectId}'`)
      }
      return {
        info: current,
        // Unknown until the player has loaded the file: the UI falls back to its own reading.
        video: { duration: 0, width: 16, height: 9, fps: 0, has_audio: true },
        analysed: false,
      }
    },
    videoUrl: () => '',
    async sendRequest(_projectId, text) {
      await wait(delayMs)
      requests += 1
      return {
        request_id: `r_mock_${requests}`,
        status: 'not_applied',
        reply: `(mock) Got "${text}". The agent (F-05) will turn requests like this into edits.`,
        steps: [],
      }
    },
    async getState() {
      await wait(delayMs)
      return exampleState
    },
    async getHistory() {
      await wait(delayMs)
      return exampleHistory()
    },
    async getMessages() {
      await wait(delayMs)
      return []
    },
    async getTracks() {
      await wait(delayMs)
      return { analysed: false, tracks: [] }
    },
    async undo() {
      await wait(delayMs)
      throw unavailable('Undo')
    },
    async redo() {
      await wait(delayMs)
      throw unavailable('Redo')
    },
    async analyse() {
      await wait(delayMs)
      throw unavailable('Analysis')
    },
    async exportVideo() {
      await wait(delayMs)
      throw unavailable('Export')
    },
    async transcribe() {
      await wait(delayMs)
      const word = (text: string, start: number, end: number) => ({
        text,
        start,
        end,
        probability: 0.9,
      })
      return {
        model: 'mock',
        language: 'en',
        segments: [
          {
            start: 1.2,
            end: 2.0,
            text: '(mock) Hello there.',
            words: [word('Hello', 1.2, 1.6), word('there.', 1.6, 2.0)],
          },
        ],
      }
    },
  }
}
