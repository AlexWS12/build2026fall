import { describe, expect, test } from 'vitest'
import type { ChatReply, HistoryReply, TimelineState } from '../api/types'
import {
  busyLabel,
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
  ...overrides,
})

const applied: ChatReply = {
  request_id: 'r_1',
  status: 'applied',
  reply: 'Muted 0:00 to 0:05.',
  steps: [{ step_id: 's1', tool: 'mute', args: {}, result: { ops: 1 }, ok: true }],
}

const afterMute: Sync = { timeline: muted, history: history('applied') }

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
    { type: 'export_started' },
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
      { type: 'analysis_settled', analysed: true, tracks: [] },
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
        { type: 'history_settled', sync: { timeline: emptyTimeline, history: history('undone') } },
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
        { type: 'request_settled', reply: clarify, sync: { timeline: emptyTimeline, history: noHistory } },
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
