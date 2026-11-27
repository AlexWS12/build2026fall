import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import type { TimelineState } from '../api/types'
import { fallbackDuration } from './timeline'
import { TimelineView } from './TimelineView'

const empty: TimelineState = {
  schema_version: 1,
  masks: {},
  reviewed: {},
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

// --- Trim handles --------------------------------------------------------------------------

test('there are no trim handles without onTrim', () => {
  render(<TimelineView state={empty} duration={10} />)
  expect(screen.queryByLabelText('Trim start')).not.toBeInTheDocument()
})

test('moving the handles offers the trim, and Trim sends the kept range', async () => {
  const { fireEvent } = await import('@testing-library/react')
  const { vi } = await import('vitest')
  const onTrim = vi.fn()
  render(<TimelineView state={empty} duration={10} onTrim={onTrim} />)
  expect(screen.getByRole('button', { name: 'Drag to trim' })).toBeDisabled()

  fireEvent.change(screen.getByLabelText('Trim start'), { target: { value: '2' } })
  fireEvent.change(screen.getByLabelText('Trim end'), { target: { value: '8' } })
  fireEvent.click(screen.getByRole('button', { name: 'Trim to 0:02–0:08' }))

  expect(onTrim).toHaveBeenCalledWith(2, 8)
})

test('the handles never cross, and Reset puts them back', async () => {
  const { fireEvent } = await import('@testing-library/react')
  render(<TimelineView state={empty} duration={10} onTrim={() => {}} />)
  fireEvent.change(screen.getByLabelText('Trim end'), { target: { value: '4' } })
  fireEvent.change(screen.getByLabelText('Trim start'), { target: { value: '9' } })

  expect(screen.getByLabelText('Trim start')).toHaveValue('3.9')
  fireEvent.click(screen.getByRole('button', { name: 'Reset' }))
  expect(screen.getByLabelText('Trim start')).toHaveValue('0')
  expect(screen.getByLabelText('Trim end')).toHaveValue('10')
})

test('while the project is busy the handles and Trim wait', () => {
  render(<TimelineView state={empty} duration={10} onTrim={() => {}} trimDisabled />)
  expect(screen.getByLabelText('Trim start')).toBeDisabled()
  expect(screen.getByLabelText('Trim end')).toBeDisabled()
})

// --- What was trimmed, and the playhead ---------------------------------------------------

const trimmed: TimelineState = {
  ...empty,
  cuts: [
    { start: 0, end: 2 },
    { start: 8, end: null },
  ],
}

test('keptRange is what the cuts at both ends leave, ignoring cuts in the middle', async () => {
  const { keptRange } = await import('./timeline')
  expect(keptRange(empty, 10)).toEqual([0, 10])
  expect(keptRange(trimmed, 10)).toEqual([2, 8])
  const middle = { ...empty, cuts: [{ start: 4, end: 5 }] }
  expect(keptRange(middle, 10)).toEqual([0, 10])
  const twoAtTheStart = {
    ...empty,
    cuts: [
      { start: 0, end: 1 },
      { start: 1, end: 3 },
    ],
  }
  expect(keptRange(twoAtTheStart, 10)).toEqual([3, 10])
})

test('after a trim the handles sit on what was kept, and say how to get the rest back', () => {
  render(<TimelineView state={trimmed} duration={10} onTrim={() => {}} />)
  expect(screen.getByLabelText('Trim start')).toHaveValue('2')
  expect(screen.getByLabelText('Trim end')).toHaveValue('8')
  expect(screen.getByText('Kept 0:02–0:08. Undo brings the rest back.')).toBeInTheDocument()
})

test('the handles stay inside what was kept: cut footage only comes back with undo', async () => {
  const { fireEvent } = await import('@testing-library/react')
  render(<TimelineView state={trimmed} duration={10} onTrim={() => {}} />)
  fireEvent.change(screen.getByLabelText('Trim start'), { target: { value: '0' } })
  fireEvent.change(screen.getByLabelText('Trim end'), { target: { value: '10' } })
  expect(screen.getByLabelText('Trim start')).toHaveValue('2')
  expect(screen.getByLabelText('Trim end')).toHaveValue('8')

  fireEvent.change(screen.getByLabelText('Trim start'), { target: { value: '3' } })
  expect(screen.getByRole('button', { name: 'Trim to 0:03–0:08' })).toBeEnabled()
})

test('the playhead moves with the keyboard', async () => {
  const { fireEvent } = await import('@testing-library/react')
  const { vi } = await import('vitest')
  const onSeek = vi.fn()
  render(<TimelineView state={empty} duration={10} time={4} onSeek={onSeek} />)
  const playhead = screen.getByRole('slider', { name: 'Playhead' })
  expect(playhead).toHaveAttribute('aria-valuenow', '4')

  fireEvent.keyDown(playhead, { key: 'ArrowRight' })
  fireEvent.keyDown(playhead, { key: 'ArrowLeft' })
  fireEvent.keyDown(playhead, { key: 'End' })
  fireEvent.keyDown(playhead, { key: 'Home' })

  expect(onSeek.mock.calls.map(([t]) => t)).toEqual([5, 3, 10, 0])
})

test('pressing and dragging over the timeline moves the playhead until the pointer is let go', async () => {
  const { fireEvent } = await import('@testing-library/react')
  const { vi } = await import('vitest')
  if (!('PointerEvent' in window)) {
    Object.assign(window, { PointerEvent: MouseEvent })
  }
  // The track area is 200 px wide, starting 100 px from the left.
  const rect = vi
    .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
    .mockReturnValue({ left: 100, width: 200, top: 0, height: 20, right: 300, bottom: 20, x: 100, y: 0, toJSON: () => ({}) })
  const onSeek = vi.fn()
  render(<TimelineView state={empty} duration={10} time={0} onSeek={onSeek} />)
  const playhead = screen.getByRole('slider', { name: 'Playhead' })

  fireEvent.pointerDown(playhead, { button: 0, clientX: 200, pointerId: 1 })
  fireEvent.pointerMove(playhead, { clientX: 250, pointerId: 1 })
  fireEvent.pointerMove(playhead, { clientX: 400, pointerId: 1 }) // past the end: the end
  fireEvent.pointerUp(playhead, { pointerId: 1 })
  fireEvent.pointerMove(playhead, { clientX: 150, pointerId: 1 }) // not pressed: nothing

  expect(onSeek.mock.calls.map(([t]) => t)).toEqual([5, 7.5, 10])
  rect.mockRestore()
})
