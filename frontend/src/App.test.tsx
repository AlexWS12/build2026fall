import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, test } from 'vitest'
import App from './App'
import type { ApiClient } from './api'
import type { HistoryReply, TimelineState } from './api/types'
import { createMockClient } from './api/mock'
import { TranscriptPanel } from './components/TranscriptPanel'

const mock = () => createMockClient({ delayMs: 0 })

function upload(name = 'street.mp4') {
  const input = screen.getByLabelText('Video file')
  fireEvent.change(input, { target: { files: [new File(['fake'], name, { type: 'video/mp4' })] } })
}

describe('App shell', () => {
  test('starts on the landing page, with a place to drop a video', () => {
    render(<App api={mock()} />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Say the edit.')
    expect(screen.getByLabelText('Video file')).toBeEnabled()
  })

  test('renders the three panels once a video is uploaded', async () => {
    render(<App api={mock()} />)
    upload()
    for (const name of ['Player', 'Chat', 'Timeline']) {
      expect(await screen.findByRole('region', { name })).toBeInTheDocument()
    }
  })

  test('shows the backend status from /api/health', async () => {
    const live: ApiClient = {
      ...mock(),
      health: () => Promise.resolve({ status: 'ok', backend: 'stub' }),
    }
    render(<App api={live} />)
    expect(await screen.findByText('Backend: ok (stub)')).toBeInTheDocument()
  })

  test('says when the mock API is answering', async () => {
    render(<App api={mock()} />)
    expect(await screen.findByText('Backend: mock (stub)')).toBeInTheDocument()
  })

  test('says so when the backend is unreachable', async () => {
    const down: ApiClient = { ...mock(), health: () => Promise.reject(new Error('offline')) }
    render(<App api={down} />)
    expect(await screen.findByText('Backend: unreachable')).toBeInTheDocument()
  })

  test('there is no chat until a video is uploaded', () => {
    render(<App api={mock()} />)
    expect(screen.queryByLabelText('Request')).not.toBeInTheDocument()
  })

  test('the logo leads back to the landing page', async () => {
    render(<App api={mock()} />)
    upload()
    await screen.findByText('street')
    fireEvent.click(screen.getByRole('button', { name: 'Back to the start screen' }))
    expect(await screen.findByLabelText('Video file')).toBeInTheDocument()
    expect(screen.queryByLabelText('Request')).not.toBeInTheDocument()
  })

  test('uploading creates a project and draws its timeline', async () => {
    render(<App api={mock()} />)
    upload('street.mp4')

    expect(await screen.findByText('street')).toBeInTheDocument()
    expect(screen.getByLabelText('Request')).toBeEnabled()
    const lanes = within(screen.getByRole('list', { name: 'Timeline lanes' }))
    for (const lane of ['trk_1', 'trk_2', 'Cuts', 'Mutes', 'Audio filters']) {
      expect(lanes.getByText(lane)).toBeInTheDocument()
    }
  })

  test('a failed upload shows an error', async () => {
    const failing: ApiClient = {
      ...mock(),
      createProject: () => Promise.reject(new Error('413 too large')),
    }
    render(<App api={failing} />)
    upload()
    expect(await screen.findByRole('alert')).toHaveTextContent('Upload failed: 413 too large')
  })

  test('transcribing shows the transcript with its times', async () => {
    render(<App api={mock()} />)
    upload()
    await screen.findByText('street')
    fireEvent.click(screen.getByRole('button', { name: 'Transcribe' }))

    const segments = within(await screen.findByRole('list', { name: 'Transcript segments' }))
    expect(segments.getByText('0:01.20')).toBeInTheDocument()
    expect(segments.getByText(/Hello there\./)).toBeInTheDocument()
  })

  test('a failed transcription shows an error', async () => {
    const failing: ApiClient = {
      ...mock(),
      transcribe: () => Promise.reject(new Error('503')),
    }
    render(<App api={failing} />)
    upload()
    await screen.findByText('street')
    fireEvent.click(screen.getByRole('button', { name: 'Transcribe' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Transcription failed: 503')
  })

  test('chat and leaving the project wait while a transcription runs', async () => {
    // The server holds the project busy (409), and a new project would get the old transcript.
    const pending: ApiClient = { ...mock(), transcribe: () => new Promise(() => {}) }
    render(<App api={pending} />)
    upload()
    const input = await screen.findByLabelText('Request')
    await screen.findByText('street')
    fireEvent.change(input, { target: { value: 'blur faces' } })
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: 'Transcribe' }))

    expect(await screen.findByText('Transcribing…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Back to the start screen' })).toBeDisabled()
  })

  test('transcribe waits while a chat request runs', async () => {
    // Started then, the server would refuse it: the project does one thing at a time.
    const api: ApiClient = { ...mock(), sendRequest: () => new Promise(() => {}) }
    render(<App api={api} />)
    upload()
    await screen.findByText('street')
    const button = screen.getByRole('button', { name: 'Transcribe' })
    expect(button).toBeEnabled()

    fireEvent.change(screen.getByLabelText('Request'), { target: { value: 'blur faces' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(button).toBeDisabled())
  })

  test('transcript times round before splitting minutes', () => {
    const at = (start: number) => ({ start, end: start + 1, text: 'hi', words: [] })
    const transcript = { model: 'test', language: 'en', segments: [7.08, 59.997, 119.999].map(at) }
    render(
      <TranscriptPanel
        transcript={transcript}
        disabled={false}
        busy={false}
        error={null}
        onTranscribe={() => {}}
      />,
    )
    const times = within(screen.getByRole('list', { name: 'Transcript segments' }))
    for (const time of ['0:07.08', '1:00.00', '2:00.00']) {
      expect(times.getByText(time)).toBeInTheDocument()
    }
  })

  test('sending a request shows it and the reply', async () => {
    render(<App api={mock()} />)
    upload()
    const input = await screen.findByLabelText('Request')
    await screen.findByText('street')

    fireEvent.change(input, { target: { value: 'blur all faces' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    const messages = within(screen.getByRole('list', { name: 'Messages' }))
    expect(messages.getByText('blur all faces')).toBeInTheDocument()
    expect(await messages.findByText(/Got "blur all faces"/)).toBeInTheDocument()
    expect(input).toHaveValue('')
  })
})

describe('App wired to a backend', () => {
  const muted: TimelineState = {
    schema_version: 1,
    masks: {},
    cuts: [],
    mutes: [{ start: 0, end: 5, op_id: 'op_1', request_id: 'r_1', speaker: null }],
    audio_filters: [],
    rules: [],
  }
  const empty: TimelineState = { ...muted, mutes: [] }

  /** A backend that applies one mute, and can undo and redo it. */
  function backend(overrides: Partial<ApiClient> = {}): ApiClient {
    let state = empty
    const history = (): HistoryReply => ({
      requests: [
        {
          request_id: 'r_1',
          source: 'agent',
          status: state === muted ? 'applied' : 'undone',
          kinds: ['mute'],
          text: 'mute the first 5 seconds',
          reply: 'Muted 0:00–0:05.',
        },
      ],
      can_undo: state === muted,
      can_redo: state !== muted,
    })
    return {
      ...mock(),
      health: () => Promise.resolve({ status: 'ok', backend: 'stub' }),
      sendRequest: () => {
        state = muted
        return Promise.resolve({
          request_id: 'r_1',
          status: 'applied',
          reply: 'Muted 0:00–0:05.',
          steps: [{ step_id: 's1', tool: 'mute', args: {}, result: { op_ids: ['op_1'] }, ok: true }],
        })
      },
      getState: () => Promise.resolve(state),
      getHistory: () => Promise.resolve(state === empty ? { requests: [], can_undo: false, can_redo: false } : history()),
      undo: () => {
        state = { ...empty }
        return Promise.resolve(history())
      },
      redo: () => {
        state = muted
        return Promise.resolve(history())
      },
      exportVideo: () =>
        Promise.resolve({
          name: 'export_1.mp4',
          url: '/api/projects/prj_mock/exports/export_1.mp4',
          duration: 15,
          issues: [],
        }),
      ...overrides,
    }
  }

  async function sendMute() {
    upload()
    await screen.findByText('street')
    fireEvent.change(screen.getByLabelText('Request'), { target: { value: 'mute the first 5 seconds' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await screen.findByText('Muted 0:00–0:05.')
  }

  test('an applied request shows its reply, its steps and the new timeline', async () => {
    render(<App api={backend()} />)
    await sendMute()

    expect(screen.getByText('1 step')).toBeInTheDocument()
    expect(screen.getByTitle('mute all audio (op_1, r_1): 0:00–0:05')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Undo' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Redo' })).toBeDisabled()
  })

  test('undo takes the request back and redo restores it', async () => {
    render(<App api={backend()} />)
    await sendMute()

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(await screen.findByText('Undid: "mute the first 5 seconds"')).toBeInTheDocument()
    expect(screen.queryByTitle(/mute all audio/)).not.toBeInTheDocument()
    expect(screen.getByText('Undone.')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Redo' }))
    expect(await screen.findByText('Redid: "mute the first 5 seconds"')).toBeInTheDocument()
    expect(screen.getByTitle(/mute all audio/)).toBeInTheDocument()
  })

  test('a request the server fails is reported, and the chat stays usable', async () => {
    const failing = backend({
      sendRequest: () => Promise.reject(new Error('the planner is unavailable; try again.')),
    })
    render(<App api={failing} />)
    upload()
    await screen.findByText('street')
    fireEvent.change(screen.getByLabelText('Request'), { target: { value: 'blur faces' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText(/Request failed: the planner is unavailable/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Request'), { target: { value: 'again' } })
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()
  })

  test('export offers the file, then marks it stale once the timeline changes', async () => {
    render(<App api={backend()} />)
    upload()
    await screen.findByText('street')

    fireEvent.click(screen.getByRole('button', { name: 'Export' }))
    const link = await screen.findByRole('link', { name: 'Download export (0:15)' })
    expect(link).toHaveAttribute('href', '/api/projects/prj_mock/exports/export_1.mp4')

    fireEvent.change(screen.getByLabelText('Request'), { target: { value: 'mute the first 5 seconds' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText('The timeline changed since.')).toBeInTheDocument()
  })

  test('a failed export says why', async () => {
    const failing = backend({
      exportVideo: () => Promise.reject(new Error("the renderer can't apply audio filters yet")),
    })
    render(<App api={failing} />)
    upload()
    await screen.findByText('street')
    fireEvent.click(screen.getByRole('button', { name: 'Export' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "Export failed: the renderer can't apply audio filters yet",
    )
  })

  test('offers projects already on the server, and reopens one', async () => {
    const info = {
      storage_version: 1,
      project_id: 'prj_old',
      name: 'interview',
      created_at: '2026-11-13T00:00:00Z',
      video: { sha256: 'b'.repeat(64), ext: '.mp4', original_name: 'upload.mp4' },
    }
    const stored = backend({
      listProjects: () => Promise.resolve([info]),
      getProject: () =>
        Promise.resolve({
          info,
          video: { duration: 20, width: 640, height: 360, fps: 24, has_audio: true },
          analysed: false,
        }),
      videoUrl: () => '',
    })
    render(<App api={stored} />)
    fireEvent.click(await screen.findByRole('button', { name: 'interview' }))
    expect(await screen.findByText('interview', { selector: 'strong' })).toBeInTheDocument()
    expect(screen.getByLabelText('Request')).toBeEnabled()
  })
})
