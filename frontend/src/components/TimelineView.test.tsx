import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import type { TimelineState } from '../api/types'
import { fallbackDuration } from './timeline'
import { TimelineView } from './TimelineView'

const empty: TimelineState = {
  schema_version: 1,
  masks: {},
  cuts: [],
  mutes: [],
  audio_filters: [],
  rules: [],
}

const blurFirstHalf: TimelineState = {
  ...empty,
  masks: {
    trk_1: [
      {
        start: 0,
        end: 15,
        op_id: 'op_1',
        request_id: 'r_1',
        effect: { type: 'blur', padding: 0.15, strength: null },
      },
    ],
  },
}

test('places spans by source time over the video duration', () => {
  render(<TimelineView state={blurFirstHalf} duration={30} />)
  const span = screen.getByTitle('blur (op_1, r_1): 0:00–0:15')
  expect(span).toHaveStyle({ left: '0%', width: '50%' })
})

test('an open-ended span runs to the end of the video', () => {
  const openEnded: TimelineState = {
    ...empty,
    cuts: [{ start: 20, end: null }],
  }
  render(<TimelineView state={openEnded} duration={40} />)
  expect(screen.getByTitle('cut: 0:20–end')).toHaveStyle({ left: '50%', width: '50%' })
})

test('without a duration, fits the latest time in the state', () => {
  expect(fallbackDuration(empty)).toBe(10)
  expect(fallbackDuration(blurFirstHalf)).toBe(17)
})

test('shows a placeholder before there is a state', () => {
  render(<TimelineView state={null} duration={null} />)
  expect(screen.getByText('No edits yet.')).toBeInTheDocument()
})
