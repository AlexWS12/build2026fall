import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import App from './App'
import type { ApiClient } from './api'
import type { HistoryReply, ReviewItem, ReviewReply, TimelineState } from './api/types'
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
    reviewed: {},
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

  test('the history lists the request, and follows undo', async () => {
    render(<App api={backend()} />)
    await sendMute()

    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    const history = within(screen.getByRole('region', { name: 'Request history' }))
    expect(history.getByText('1 request')).toBeInTheDocument()
    expect(history.getByText('mute the first 5 seconds')).toBeInTheDocument()
    expect(history.getByText('Undo next')).toBeInTheDocument()

    // A real press on Undo: the list stays open and changes in place, and Redo would bring it back.
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Undo' }))
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(await history.findByText('Redo next')).toBeInTheDocument()
    expect(history.queryByText('Undo next')).not.toBeInTheDocument()

    fireEvent.pointerDown(screen.getByRole('region', { name: 'Player' }))
    expect(screen.queryByRole('region', { name: 'Request history' })).not.toBeInTheDocument()
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

  describe('the review panel', () => {
    const item: ReviewItem = {
      item_id: 'rv_1',
      kind: 'lost',
      track_id: 'trk_1',
      label: 'face',
      start: 2,
      end: 5,
      effect: { type: 'blur', padding: 0.15 },
      op_id: 'op_1',
      request_id: 'r_1',
    }
    const queue = (over: Partial<ReviewReply> = {}): ReviewReply => ({
      analysed: true,
      items: [item],
      held_seconds: 0,
      rules_not_audited: [],
      ...over,
    })

    /** A backend whose queue loses the item once it has been confirmed. */
    function reviewing(over: Partial<ApiClient> = {}) {
      let current = queue()
      const reviewItem = vi.fn((_id: string, _item: string, _action: 'confirm' | 'cut') => {
        current = queue({ items: [] })
        return Promise.resolve(current)
      })
      return { reviewItem, api: backend({ getReview: () => Promise.resolve(current), reviewItem, ...over }) }
    }

    async function open(api: ApiClient) {
      render(<App api={api} />)
      upload()
      await screen.findByText('street')
      return screen.getByRole('region', { name: 'Review' })
    }

    test('lists a stretch with its kind, track and range', async () => {
      const panel = within(await open(reviewing().api))
      const items = within(await panel.findByRole('list', { name: 'Review items' }))
      expect(items.getByText('Target lost')).toBeInTheDocument()
      expect(items.getByText('face')).toBeInTheDocument()
      expect(items.getByText('0:02–0:05')).toBeInTheDocument()
    })

    test('clicking a stretch moves the player to its start', async () => {
      const panel = within(await open(reviewing().api))
      fireEvent.click(await panel.findByRole('button', { name: /Go to 0:02/ }))
      expect(document.querySelector('video')?.currentTime).toBe(2)
    })

    test('confirming sends the action, and the stretch leaves the queue', async () => {
      const { api, reviewItem } = reviewing()
      const panel = within(await open(api))
      fireEvent.click(await panel.findByRole('button', { name: 'Confirm Target lost, face, 0:02–0:05' }))
      await waitFor(() => expect(reviewItem).toHaveBeenCalledWith('prj_mock', 'rv_1', 'confirm'))
      expect(await panel.findByText('Nothing to check.')).toBeInTheDocument()
      expect(panel.queryByRole('list', { name: 'Review items' })).not.toBeInTheDocument()
    })

    test('the Cut button shows how long a stretch it removes', async () => {
      const panel = within(await open(reviewing().api))
      const cut = await panel.findByRole('button', { name: /^Cut 3\.0 s:/ })
      expect(cut).toHaveTextContent('Cut 3.0 s')
    })

    test.each([
      [2, 2.000001, 'Cut <0.1 s'],
      [1, 5.6, 'Cut 4.6 s'],
      [0, 0.05, 'Cut 0.1 s'],
    ])('the Cut button for a stretch of %s to %s reads %s', async (start, end, text) => {
      const api = backend({ getReview: () => Promise.resolve(queue({ items: [{ ...item, start, end }] })) })
      const panel = within(await open(api))
      const cut = await panel.findByRole('button', { name: /^Cut /})
      expect(cut).toHaveTextContent(text)
    })

    test('the buttons wait while the project is busy', async () => {
      const { api } = reviewing({ sendRequest: () => new Promise(() => {}) })
      const panel = within(await open(api))
      const confirm = await panel.findByRole('button', { name: 'Confirm Target lost, face, 0:02–0:05' })
      expect(confirm).toBeEnabled()

      fireEvent.change(screen.getByLabelText('Request'), { target: { value: 'blur faces' } })
      fireEvent.click(screen.getByRole('button', { name: 'Send' }))

      await waitFor(() => expect(confirm).toBeDisabled())
      expect(panel.getByRole('button', { name: 'Cut 3.0 s: Target lost, face, 0:02–0:05' })).toBeDisabled()
    })

    test('an empty queue says so, and mentions held boxes and standing rules', async () => {
      const api = backend({
        getReview: () =>
          Promise.resolve(queue({ items: [], held_seconds: 1.25, rules_not_audited: ['rule_1'] })),
      })
      const panel = within(await open(api))
      expect(await panel.findByText('Nothing to check.')).toBeInTheDocument()
      expect(panel.getByText(/1\.3 s of boxes held/)).toBeInTheDocument()
      expect(panel.getByText("Standing rules can't be checked here.")).toBeInTheDocument()
    })

    test('a video that was not analysed says what that means for the masks', async () => {
      const api = backend({ getReview: () => Promise.resolve(queue({ analysed: false, items: [] })) })
      const panel = within(await open(api))
      expect(await panel.findByText(/hasn't been analysed/)).toBeInTheDocument()
      expect(panel.queryByText('Nothing to check.')).not.toBeInTheDocument()
    })

    test('a queue that could not be read shows why', async () => {
      const api = backend({ getReview: () => Promise.reject(new Error('503')) })
      const panel = within(await open(api))
      expect(await panel.findByRole('alert')).toHaveTextContent(
        "Couldn't load the review queue: 503",
      )
    })
  })
})

describe('preview before apply (F-44)', () => {
  const empty: TimelineState = {
    schema_version: 1,
    masks: {},
    reviewed: {},
    cuts: [],
    mutes: [],
    audio_filters: [],
    rules: [],
  }
  const cut: TimelineState = { ...empty, cuts: [{ start: 2, end: 4 }] }
  const summary = { duration_before: 10, duration_after: 8, removed: [{ start: 2, end: 4 }] }

  /** A backend that proposes a cut and keeps it waiting until accepted or rejected. */
  function proposing(overrides: Partial<ApiClient> = {}) {
    let waiting = false
    let applied = false
    const pending = (problem: string | null = null) => ({
      request_id: 'r_cut',
      text: 'cut 0:02 to 0:04',
      reply: 'Cut 0:02–0:04.',
      steps: [],
      state: cut,
      preview: summary,
      problem,
    })
    const api: ApiClient = {
      ...mock(),
      health: () => Promise.resolve({ status: 'ok', backend: 'stub' }),
      sendRequest: () => {
        waiting = true
        return Promise.resolve({
          request_id: 'r_cut',
          status: 'preview' as const,
          reply: 'Cut 0:02–0:04. Nothing is changed until you apply it.',
          steps: [],
          preview: summary,
        })
      },
      getPreview: () => Promise.resolve(waiting ? pending() : null),
      acceptPreview: () => {
        waiting = false
        applied = true
        return Promise.resolve({
          request_id: 'r_cut',
          status: 'applied' as const,
          reply: 'Cut 0:02–0:04.',
          steps: [],
        })
      },
      rejectPreview: () => {
        waiting = false
        return Promise.resolve({
          request_id: 'r_cut',
          status: 'not_applied' as const,
          reply: 'Discarded the preview. Nothing was changed.',
          steps: [],
        })
      },
      getState: () => Promise.resolve(applied ? cut : empty),
      ...overrides,
    }
    return api
  }

  async function proposeCut() {
    upload()
    await screen.findByText('street')
    fireEvent.change(screen.getByLabelText('Request'), { target: { value: 'cut 0:02 to 0:04' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await screen.findByRole('group', { name: 'Preview of the proposed edit' })
  }

  test('a proposed cut is previewed, not applied, and the request box waits', async () => {
    render(<App api={proposing()} />)
    await proposeCut()

    expect(screen.getByText(/nothing is applied yet/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Apply' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Discard' })).toBeEnabled()
    expect(screen.getByLabelText('Request')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Undo' })).toBeDisabled()
  })

  test('Apply applies it and gives the request box back', async () => {
    render(<App api={proposing()} />)
    await proposeCut()

    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))

    await waitFor(() =>
      expect(screen.queryByRole('group', { name: 'Preview of the proposed edit' })).toBeNull(),
    )
    expect(screen.getAllByText('Cut 0:02–0:04.')).toHaveLength(1) // one chat line per request
    expect(screen.getByLabelText('Request')).toBeEnabled()
  })

  test('Discard throws it away and says nothing was changed', async () => {
    render(<App api={proposing()} />)
    await proposeCut()

    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))

    expect(await screen.findByText(/Discarded the preview/)).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Preview of the proposed edit' })).toBeNull()
    expect(screen.getByLabelText('Request')).toBeEnabled()
  })

  test('a failed Apply says so, and the proposal is still there to settle', async () => {
    const api = proposing({ acceptPreview: () => Promise.reject(new Error('disk full')) })
    render(<App api={api} />)
    await proposeCut()

    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))

    expect(await screen.findByText(/Applying the preview failed: disk full/)).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Preview of the proposed edit' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Discard' })).toBeEnabled()
  })

  test('a proposal the timeline no longer allows can only be discarded', async () => {
    const api = proposing()
    const stale = {
      request_id: 'r_cut',
      text: 'cut 0:02 to 0:04',
      reply: 'x',
      steps: [],
      state: empty,
      preview: { duration_before: 10, duration_after: 10, removed: [] },
      problem: 'I couldn’t do that: it changed.',
    }
    render(<App api={{ ...api, getPreview: () => Promise.resolve(stale) }} />)
    upload()
    await screen.findByRole('group', { name: 'Preview of the proposed edit' })

    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Discard' })).toBeEnabled()
    expect(screen.getByRole('alert')).toHaveTextContent('it changed')
  })
})
