import { expect, test } from 'vitest'
import type { TimelineState, Track } from '../api/types'
import { boxAt, cutAt, masksAt, mutedAt, outputDuration } from './preview'

const empty: TimelineState = {
  schema_version: 1,
  masks: {},
  cuts: [],
  mutes: [],
  audio_filters: [],
  rules: [],
}

const face: Track = {
  track_id: 'trk_1',
  label: 'face',
  source: 'detector',
  keyframes: [
    { t: 1, box: [0.1, 0.1, 0.2, 0.2], conf: 0.9, state: 'detected' },
    { t: 2, box: [0.3, 0.1, 0.4, 0.2], conf: 0.9, state: 'detected' },
    { t: 6, box: [0.7, 0.5, 0.8, 0.6], conf: 0.9, state: 'detected' },
  ],
}

const close = (actual: number[], expected: number[]) =>
  actual.forEach((value, i) => expect(value).toBeCloseTo(expected[i]))

test('a box moves in a straight line between nearby keyframes', () => {
  close(boxAt(face, 1.5), [0.2, 0.1, 0.3, 0.2])
})

test('past either end of a track, the nearest box is held', () => {
  expect(boxAt(face, 0)).toEqual([0.1, 0.1, 0.2, 0.2])
  expect(boxAt(face, 60)).toEqual([0.7, 0.5, 0.8, 0.6])
})

test('across a long gap the box covers both keyframes and the space between', () => {
  expect(boxAt(face, 4)).toEqual([0.3, 0.1, 0.8, 0.6])
})

const blurred: TimelineState = {
  ...empty,
  masks: {
    trk_1: [
      {
        start: 0,
        end: 3,
        op_id: 'op_1',
        request_id: 'r_1',
        effect: { type: 'blur', padding: 0.5, strength: null },
      },
    ],
    trk_gone: [
      {
        start: 0,
        end: null,
        op_id: 'op_2',
        request_id: 'r_2',
        effect: { type: 'box', padding: 0, strength: null },
      },
    ],
  },
}

test('masks in effect are padded, and end when their span ends', () => {
  const [mask] = masksAt(blurred, [face], 1)
  expect(mask.trackId).toBe('trk_1')
  close(mask.box!, [0.05, 0.05, 0.25, 0.25])
  expect(masksAt(blurred, [face], 3).map((m) => m.trackId)).toEqual(['trk_gone'])
})

test('a masked track with no geometry covers the whole frame', () => {
  const masks = masksAt(blurred, [face], 1)
  expect(masks.find((m) => m.trackId === 'trk_gone')?.box).toBeNull()
})

const cutAndMuted: TimelineState = {
  ...empty,
  cuts: [
    { start: 0, end: 5 },
    { start: 50, end: null },
  ],
  mutes: [
    { start: 10, end: 20, op_id: 'op_3', request_id: 'r_3', speaker: null },
    { start: 30, end: 40, op_id: 'op_4', request_id: 'r_4', speaker: 'spk_1' },
  ],
}

test('finds the cut a time falls in; ranges exclude their end', () => {
  expect(cutAt(cutAndMuted, 2)).toEqual({ start: 0, end: 5 })
  expect(cutAt(cutAndMuted, 5)).toBeNull()
  expect(cutAt(cutAndMuted, 55)).toEqual({ start: 50, end: null })
})

test('only an all-audio mute silences the preview', () => {
  expect(mutedAt(cutAndMuted, 15)).toBe(true)
  expect(mutedAt(cutAndMuted, 35)).toBe(false)
  expect(mutedAt(cutAndMuted, 25)).toBe(false)
})

test('the output is the source without its cuts', () => {
  expect(outputDuration(cutAndMuted, 60)).toBe(45)
  expect(outputDuration(empty, 60)).toBe(60)
})
