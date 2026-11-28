// F-14: the session's `targeting` activity (click or draw a box in the player).
import { expect, test } from 'vitest'
import type { HistoryReply, TargetReply, TimelineState, Track } from '../api/types'
import {
  busyLabel,
  canSend,
  initialSession,
  transition,
  type Session,
  type SessionEvent,
  type Sync,
} from './session'

const empty: TimelineState = {
  schema_version: 1,
  masks: {},
  reviewed: {},
  cuts: [],
  mutes: [],
  audio_filters: [],
  rules: [],
}

const masked: TimelineState = {
  ...empty,
  masks: {
    usr_1: [
      {
        start: 0,
        end: null,
        op_id: 'op_1',
        request_id: 'r_1',
        effect: { type: 'blur', padding: 0.15, strength: null },
      },
    ],
  },
}

const history: HistoryReply = {
  requests: [
    {
      request_id: 'r_1',
      source: 'user',
      status: 'applied',
      kinds: ['mask_targets'],
      text: null,
      reply: null,
    },
  ],
  can_undo: true,
  can_redo: false,
}

const drawn: Track = {
  track_id: 'usr_1',
  label: 'object',
  source: 'user',
  keyframes: [{ t: 1, box: [0.2, 0.2, 0.4, 0.4], conf: 1, state: 'user' }],
}

const reply: TargetReply = {
  request_id: 'r_1',
  how: 'followed',
  track_id: 'usr_1',
  label: 'object',
  followed: { start: 0, end: 3 },
  lost_before: null,
  lost_after: null,
  reply: 'Blurred your box and followed it from 0:00 to 0:03.',
}

const afterTarget: Sync = { timeline: masked, history, review: null, reviewError: null }

function play(events: SessionEvent[], from: Session): Session {
  return events.reduce((session, event) => {
    const next = transition(session, event)
    expect(next, `${event.type} was refused`).not.toBe(session)
    return next
  }, from)
}

function opened(): Session {
  return play(
    [
      { type: 'open_started', label: 'Opening…' },
      {
        type: 'open_succeeded',
        snapshot: {
          detail: {
            info: {
              storage_version: 1,
              project_id: 'prj_1',
              name: 'street',
              created_at: '2026-11-26T00:00:00Z',
              video: { sha256: 'a'.repeat(64), ext: '.mp4', original_name: 'street.mp4' },
            },
            video: { duration: 3, width: 640, height: 360, fps: 20, has_audio: false },
            analysed: false,
          },
          videoUrl: 'blob:street',
          timeline: empty,
          history: { requests: [], can_undo: false, can_redo: false },
          tracks: [],
          analysed: false,
          messages: [],
          review: null,
          reviewError: null,
        },
      },
    ],
    initialSession,
  )
}

const project = (session: Session) => {
  if (session.status !== 'open') throw new Error(`expected an open session, got ${session.status}`)
  return session.project
}

test('a target takes the server timeline, the new track and says what happened', () => {
  const started = play([{ type: 'target_started' }], opened())
  expect(busyLabel(started)).toBe('Following your target…')
  expect(canSend(started)).toBe(false)

  const done = play([{ type: 'target_settled', reply, sync: afterTarget, tracks: [drawn] }], started)

  expect(project(done)).toMatchObject({
    activity: { kind: 'idle' },
    timeline: masked,
    history,
    tracks: [drawn],
  })
  expect(project(done).chat).toEqual([{ kind: 'note', id: expect.any(String), text: reply.reply }])
  expect(canSend(done)).toBe(true)
})

test('a refused target is said in the chat and frees the project', () => {
  const failed = play(
    [
      { type: 'target_started' },
      { type: 'activity_failed', message: 'nothing is followed there; drag a box instead' },
    ],
    opened(),
  )
  const { chat } = project(failed)
  expect(chat[chat.length - 1]).toMatchObject({
    kind: 'error',
    text: "Couldn't mask that: nothing is followed there; drag a box instead",
  })
  expect(canSend(failed)).toBe(true)
})

test('a target never starts while something else is in flight, and stray replies are ignored', () => {
  const busy = play([{ type: 'request_started', text: 'blur faces' }], opened())
  expect(transition(busy, { type: 'target_started' })).toBe(busy)

  const idle = opened()
  const stray: SessionEvent = { type: 'target_settled', reply, sync: afterTarget, tracks: [drawn] }
  expect(transition(idle, stray)).toBe(idle)
})
