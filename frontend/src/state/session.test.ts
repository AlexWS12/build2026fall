import { describe, expect, test } from 'vitest'
import type {
  ChatReply,
  HistoryReply,
  PreviewReply,
  ReviewReply,
  TimelineState,
} from '../api/types'
import {
  busyLabel,
  canDecide,
  canExport,
  canOpen,
  canRedo,
  canSend,
  canUndo,
  initialSession,
  isBusy,
  transition,
  type Session,
  type SessionEvent,
  type Snapshot,
  type Sync,
} from './session'

const emptyTimeline: TimelineState = {
  schema_version: 1,
  masks: {},
  reviewed: {},
  cuts: [],
  mutes: [],
  audio_filters: [],
  rules: [],
}

const muted: TimelineState = {
  ...emptyTimeline,
  mutes: [{ start: 0, end: 5, op_id: 'op_1', request_id: 'r_1', speaker: null }],
}

const noHistory: HistoryReply = { requests: [], can_undo: false, can_redo: false }

const history = (status: 'applied' | 'undone'): HistoryReply => ({
  requests: [
    {
      request_id: 'r_1',
      source: 'agent',
      status,
      kinds: ['mute'],
      text: 'mute the first 5 seconds',
      reply: 'Muted 0:00 to 0:05.',
    },
  ],
  can_undo: status === 'applied',
  can_redo: status === 'undone',
})

const snapshot = (overrides: Partial<Snapshot> = {}): Snapshot => ({
  detail: {
    info: {
      storage_version: 1,
      project_id: 'prj_1',
      name: 'street',
      created_at: '2026-11-14T00:00:00Z',
      video: { sha256: 'a'.repeat(64), ext: '.mp4', original_name: 'street.mp4' },
    },
    video: { duration: 20, width: 640, height: 360, fps: 24, has_audio: true },
    analysed: false,
  },
  videoUrl: 'blob:street',
  timeline: emptyTimeline,
  history: noHistory,
  tracks: [],
  analysed: false,
  messages: [],
  ...noReview,
  ...overrides,
})

const applied: ChatReply = {
  request_id: 'r_1',
  status: 'applied',
  reply: 'Muted 0:00 to 0:05.',
  steps: [{ step_id: 's1', tool: 'mute', args: {}, result: { ops: 1 }, ok: true }],
}

const noReview = { review: null, reviewError: null, pending: null }

const afterMute: Sync = { timeline: muted, history: history('applied'), ...noReview }

/** Runs events in order; every one of them must be accepted. */
function play(events: SessionEvent[], from: Session = initialSession): Session {
  return events.reduce((session, event) => {
    const next = transition(session, event)
    expect(next, `${event.type} was refused`).not.toBe(session)
    return next
  }, from)
}

const opened = (overrides?: Partial<Snapshot>) =>
  play([
    { type: 'open_started', label: 'Uploading street.mp4…' },
    { type: 'open_succeeded', snapshot: snapshot(overrides) },
  ])

const project = (session: Session) => {
  if (session.status !== 'open') throw new Error(`expected an open session, got ${session.status}`)
  return session.project
}

describe('opening a project', () => {
  test('empty → opening → open', () => {
    const opening = play([{ type: 'open_started', label: 'Uploading street.mp4…' }])
    expect(opening.status).toBe('opening')
    expect(busyLabel(opening)).toBe('Uploading street.mp4…')
    expect(isBusy(opening)).toBe(true)

    const open = play([{ type: 'open_succeeded', snapshot: snapshot() }], opening)
    expect(project(open).activity).toEqual({ kind: 'idle' })
    expect(canSend(open)).toBe(true)
  })

  test('a failed first upload goes back to empty with the error', () => {
    const failed = play([
      { type: 'open_started', label: 'Uploading…' },
      { type: 'open_failed', message: 'Upload failed: not a readable video' },
    ])
    expect(failed).toEqual({ status: 'empty', error: 'Upload failed: not a readable video' })
  })

  test('a failed upload over an open project keeps that project', () => {
    const open = opened()
    const failed = play(
      [
        { type: 'open_started', label: 'Uploading…' },
        { type: 'open_failed', message: 'Upload failed: too large' },
      ],
      open,
    )
    expect(failed.status).toBe('open')
    expect(project(failed)).toBe(project(open))
    expect(failed.status === 'open' && failed.error).toBe('Upload failed: too large')
  })

  test('restores the stored conversation', () => {
    const open = opened({
      history: history('applied'),
      timeline: muted,
      messages: [
        {
          request_id: 'r_1',
          text: 'mute the first 5 seconds',
          status: 'applied',
          reply: 'Muted 0:00 to 0:05.',
          steps: [],
          at: '2026-11-14T00:00:00Z',
        },
      ],
    })
    expect(project(open).chat.map((entry) => entry.kind)).toEqual(['user', 'agent'])
    expect(canUndo(open)).toBe(true)
  })
})

describe('one thing at a time', () => {
  const busyWith: SessionEvent[] = [
    { type: 'request_started', text: 'mute the first 5 seconds' },
    { type: 'analysis_started' },
    { type: 'transcript_started' },
    { type: 'export_started' },
    { type: 'review_started' },
  ]
  const starts: SessionEvent[] = [
    ...busyWith,
    { type: 'undo_started' },
    { type: 'redo_started' },
    { type: 'open_started', label: 'Uploading…' },
    { type: 'closed' },
  ]

  test.each(busyWith)('while $type is in flight, nothing else starts', (first) => {
    const busy = play([first], opened({ history: history('applied') }))
    expect(isBusy(busy)).toBe(true)
    expect([canSend(busy), canUndo(busy), canRedo(busy), canExport(busy), canOpen(busy)]).toEqual(
      Array(5).fill(false),
    )
    for (const event of starts) expect(transition(busy, event), event.type).toBe(busy)
  })

  test('nothing but opening is possible before a project is open', () => {
    for (const event of starts.filter((e) => e.type !== 'open_started')) {
      expect(transition(initialSession, event), event.type).toBe(initialSession)
    }
  })

  test('a result nobody is waiting for is ignored', () => {
    const open = opened()
    const strays: SessionEvent[] = [
      { type: 'request_settled', reply: applied, sync: afterMute },
      { type: 'history_settled', sync: afterMute },
      { type: 'analysis_settled', analysed: true, tracks: [], ...noReview },
      { type: 'review_settled', sync: afterMute, gone: false },
      { type: 'export_settled', result: { name: 'a.mp4', url: '/a.mp4', duration: 1, issues: [] } },
      { type: 'activity_failed', message: 'late' },
      { type: 'open_succeeded', snapshot: snapshot() },
    ]
    for (const event of strays) expect(transition(open, event), event.type).toBe(open)
  })
})

describe('requests', () => {
  test('an applied request adds the reply and takes the server timeline', () => {
    const done = play(
      [
        { type: 'request_started', text: 'mute the first 5 seconds' },
        { type: 'request_settled', reply: applied, sync: afterMute },
      ],
      opened(),
    )
    const { chat, timeline, activity } = project(done)
    expect(activity).toEqual({ kind: 'idle' })
    expect(timeline).toBe(muted)
    expect(chat.map((entry) => entry.kind)).toEqual(['user', 'agent'])
    expect(chat[1]).toMatchObject({ status: 'applied', requestId: 'r_1' })
    expect(canUndo(done)).toBe(true)
  })

  test('a failed request says so and frees the project', () => {
    const failed = play(
      [
        { type: 'request_started', text: 'blur faces' },
        { type: 'activity_failed', message: 'this project is busy with another request' },
      ],
      opened(),
    )
    const { chat } = project(failed)
    expect(chat[chat.length - 1]).toMatchObject({
      kind: 'error',
      text: 'Request failed: this project is busy with another request',
    })
    expect(canSend(failed)).toBe(true)
  })

  test('undo and redo need something to undo or redo', () => {
    const fresh = opened()
    expect(transition(fresh, { type: 'undo_started' })).toBe(fresh)
    expect(transition(fresh, { type: 'redo_started' })).toBe(fresh)
  })

  test('undo notes which request it took back', () => {
    const undone = play(
      [
        { type: 'undo_started' },
        { type: 'history_settled', sync: { timeline: emptyTimeline, history: history('undone'), ...noReview } },
      ],
      opened({ history: history('applied'), timeline: muted }),
    )
    const { chat, timeline } = project(undone)
    expect(timeline).toBe(emptyTimeline)
    expect(chat[chat.length - 1]).toEqual({
      kind: 'note',
      id: 'e0',
      text: 'Undid: "mute the first 5 seconds"',
    })
    expect(canRedo(undone)).toBe(true)
    expect(canUndo(undone)).toBe(false)
  })

  test('undo of a bare review confirmation says so in plain words', () => {
    const bare = (status: 'applied' | 'undone'): HistoryReply => ({
      requests: [{ request_id: 'r_2', source: 'user', status, kinds: ['confirm_mask'], text: null, reply: null }],
      can_undo: status === 'applied',
      can_redo: status === 'undone',
    })
    const undone = play(
      [
        { type: 'undo_started' },
        { type: 'history_settled', sync: { timeline: emptyTimeline, history: bare('undone'), ...noReview } },
      ],
      opened({ history: bare('applied') }),
    )
    const { chat } = project(undone)
    expect(chat[chat.length - 1]).toMatchObject({ kind: 'note', text: 'Undid: a review confirmation' })
  })
})

describe('exports', () => {
  const result = { name: 'export-1.mp4', url: '/api/projects/prj_1/exports/export-1.mp4', duration: 15, issues: [] }

  test('an export goes stale when the timeline changes after it', () => {
    const exported = play(
      [{ type: 'export_started' }, { type: 'export_settled', result }],
      opened(),
    )
    expect(project(exported).export).toEqual({ kind: 'ready', result, stale: false })

    const edited = play(
      [
        { type: 'request_started', text: 'mute the first 5 seconds' },
        { type: 'request_settled', reply: applied, sync: afterMute },
      ],
      exported,
    )
    expect(project(edited).export).toEqual({ kind: 'ready', result, stale: true })
  })

  test('a reply that changes nothing leaves the export fresh', () => {
    const clarify: ChatReply = { request_id: 'r_2', status: 'clarify', reply: 'Which part?', steps: [] }
    const asked = play(
      [
        { type: 'export_started' },
        { type: 'export_settled', result },
        { type: 'request_started', text: 'cut it' },
        { type: 'request_settled', reply: clarify, sync: { timeline: emptyTimeline, history: noHistory, ...noReview } },
      ],
      opened(),
    )
    expect(project(asked).export).toMatchObject({ kind: 'ready', stale: false })
  })

  test('a failed export is reported on the export, not in the chat', () => {
    const failed = play(
      [{ type: 'export_started' }, { type: 'activity_failed', message: 'nothing left to render' }],
      opened(),
    )
    expect(project(failed).export).toEqual({ kind: 'failed', message: 'nothing left to render' })
    expect(project(failed).chat).toEqual([])
  })
})

describe('the review queue', () => {
  const queue: ReviewReply = {
    analysed: true,
    items: [],
    held_seconds: 2,
    rules_not_audited: [],
  }
  const afterReview: Sync = { ...afterMute, review: queue, reviewError: null }

  test('opens with the queue, or with the reason it could not be read', () => {
    expect(project(opened({ review: queue })).review).toEqual(queue)
    const unread = project(opened({ review: null, reviewError: 'no analysis' }))
    expect(unread).toMatchObject({ review: null, reviewError: 'no analysis' })
  })

  test('confirming or cutting is one activity that ends with the fresh timeline and queue', () => {
    const started = play([{ type: 'review_started' }], opened())
    expect(busyLabel(started)).toBe('Applying review…')
    const settled = play([{ type: 'review_settled', sync: afterReview, gone: false }], started)
    expect(project(settled)).toMatchObject({
      activity: { kind: 'idle' },
      timeline: muted,
      review: queue,
      chat: [],
    })
  })

  test('a spot that was already handled leaves a note with the refreshed queue', () => {
    const settled = play(
      [
        { type: 'review_started' },
        { type: 'review_settled', sync: afterReview, gone: true },
      ],
      opened(),
    )
    expect(project(settled).review).toEqual(queue)
    expect(project(settled).chat).toEqual([
      {
        kind: 'note',
        id: 'e0',
        text: 'That stretch was already handled. The review queue was refreshed.',
      },
    ])
  })

  test('a failed review says so, frees the project and keeps what the server answered', () => {
    const failed = play(
      [
        { type: 'review_started' },
        { type: 'activity_failed', message: 'busy', sync: afterReview },
      ],
      opened(),
    )
    expect(project(failed).activity).toEqual({ kind: 'idle' })
    expect(project(failed).review).toEqual(queue)
    expect(project(failed).chat).toEqual([{ kind: 'error', id: 'e0', text: 'Review failed: busy' }])
  })

  test('an analysis refreshes the queue', () => {
    const analysed = play(
      [
        { type: 'analysis_started' },
        { type: 'analysis_settled', analysed: true, tracks: [], review: queue, reviewError: null },
      ],
      opened(),
    )
    expect(project(analysed).review).toEqual(queue)
  })
})

describe('a cut waiting for a go-ahead (F-44)', () => {
  const cutTimeline: TimelineState = {
    ...emptyTimeline,
    cuts: [{ start: 2, end: 4 }],
  }
  const proposed: ChatReply = {
    request_id: 'r_cut',
    status: 'preview',
    reply: 'Cut 0:02–0:04. Nothing is changed until you apply it.',
    steps: [],
    preview: { duration_before: 20, duration_after: 18, removed: [{ start: 2, end: 4 }] },
  }
  const pending: PreviewReply = {
    request_id: 'r_cut',
    text: 'cut 0:02 to 0:04',
    reply: 'Cut 0:02–0:04.',
    steps: [],
    state: cutTimeline,
    preview: proposed.preview!,
    problem: null,
  }
  const waiting = () =>
    play(
      [
        { type: 'request_started', text: 'cut 0:02 to 0:04' },
        {
          type: 'request_settled',
          reply: proposed,
          sync: { timeline: emptyTimeline, history: noHistory, ...noReview, pending },
        },
      ],
      opened(),
    )

  test('a proposal keeps the real timeline and holds the candidate beside it', () => {
    const state = project(waiting())
    expect(state.timeline).toEqual(emptyTimeline)
    expect(state.pending).toEqual(pending)
    expect(state.chat.at(-1)).toMatchObject({ kind: 'agent', status: 'preview' })
  })

  test('while it waits, only accept or reject can start', () => {
    const session = waiting()
    expect(canSend(session)).toBe(false)
    expect(canUndo(session)).toBe(false)
    expect(canRedo(session)).toBe(false)
    expect(canDecide(session)).toBe(true)
    expect(transition(session, { type: 'request_started', text: 'mute it' })).toBe(session)
    expect(transition(session, { type: 'undo_started' })).toBe(session)
    expect(transition(session, { type: 'review_started' })).toBe(session)
  })

  test('nothing waiting: nothing to decide', () => {
    expect(canDecide(opened())).toBe(false)
    expect(transition(opened(), { type: 'accept_started' })).toEqual(opened())
  })

  test('accepting settles the same chat entry and clears the proposal', () => {
    const accepted: ChatReply = { ...proposed, status: 'applied', reply: 'Cut 0:02–0:04.' }
    const session = play(
      [
        { type: 'accept_started' },
        {
          type: 'preview_settled',
          reply: accepted,
          sync: { timeline: cutTimeline, history: history('applied'), ...noReview },
        },
      ],
      waiting(),
    )
    const state = project(session)
    expect(state.pending).toBeNull()
    expect(state.timeline).toEqual(cutTimeline)
    expect(state.chat.filter((entry) => entry.kind === 'agent')).toHaveLength(1)
    expect(state.chat.at(-1)).toMatchObject({ status: 'applied', text: 'Cut 0:02–0:04.' })
    expect(canSend(session)).toBe(true)
  })

  test('rejecting leaves the timeline alone and says nothing changed', () => {
    const rejected: ChatReply = {
      request_id: 'r_cut',
      status: 'not_applied',
      reply: 'Discarded the preview. Nothing was changed.',
      steps: [],
    }
    const session = play(
      [
        { type: 'reject_started' },
        {
          type: 'preview_settled',
          reply: rejected,
          sync: { timeline: emptyTimeline, history: noHistory, ...noReview },
        },
      ],
      waiting(),
    )
    expect(project(session).timeline).toEqual(emptyTimeline)
    expect(project(session).pending).toBeNull()
    expect(project(session).chat.at(-1)).toMatchObject({ status: 'not_applied' })
  })

  test('a failed accept resyncs: the server says whether it is still waiting', () => {
    const session = play(
      [
        { type: 'accept_started' },
        {
          type: 'activity_failed',
          message: 'disk full',
          sync: { timeline: emptyTimeline, history: noHistory, ...noReview, pending },
        },
      ],
      waiting(),
    )
    expect(project(session).pending).toEqual(pending)
    expect(canDecide(session)).toBe(true)
    expect(project(session).chat.at(-1)).toMatchObject({
      kind: 'error',
      text: 'Applying the preview failed: disk full',
    })
  })

  test('a reload brings the waiting proposal back', () => {
    const session = opened({ pending })
    expect(project(session).pending).toEqual(pending)
    expect(canDecide(session)).toBe(true)
  })

  test('a proposal the timeline no longer allows can only be discarded', () => {
    const stale = { ...pending, problem: 'cut_range: changed' }
    const session = opened({ pending: stale })
    expect(canDecide(session)).toBe(true)
    expect(transition(session, { type: 'accept_started' })).toBe(session)
    expect(transition(session, { type: 'reject_started' })).not.toBe(session)
  })
})
