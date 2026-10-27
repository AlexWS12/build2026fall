import editSession from '../../../backend/src/vibe/contract/examples/edit_session.state.json'
import type { ApiClient } from './client'
import type { TimelineState } from './types'

// The contract's own example, so the timeline view always draws a state the backend can fold.
const exampleState = editSession as TimelineState

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

/** A fake backend: any upload becomes a project whose timeline is the contract example. */
export function createMockClient({ delayMs = 300 }: { delayMs?: number } = {}): ApiClient {
  let requests = 0

  return {
    async health() {
      await wait(delayMs)
      return { status: 'ok', backend: 'stub' }
    },
    async createProject(file) {
      await wait(delayMs)
      const dot = file.name.lastIndexOf('.')
      return {
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
    },
    async sendRequest(_projectId, text) {
      await wait(delayMs)
      requests += 1
      return {
        request_id: `r_mock_${requests}`,
        reply: `(mock) Got "${text}". The agent (F-05) will turn requests like this into edits.`,
      }
    },
    async getState() {
      await wait(delayMs)
      return exampleState
    },
  }
}
