// F-14: click or draw a box to target, in the player.
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import type { TimelineState, Track } from '../api/types'
import { Player } from './Player'
import { TargetBar, TargetLayer } from './TargetLayer'
import { targetsAt } from './targets'

const person: Track = {
  track_id: 'trk_1',
  label: 'person',
  source: 'detector',
  keyframes: [
    { t: 1, box: [0.1, 0.1, 0.3, 0.5], conf: 0.9, state: 'detected' },
    { t: 2, box: [0.2, 0.1, 0.4, 0.5], conf: 0.9, state: 'detected' },
  ],
}

const drawn: Track = {
  track_id: 'usr_1',
  label: 'object',
  source: 'user',
  keyframes: [{ t: 5, box: [0.6, 0.6, 0.8, 0.8], conf: 1, state: 'user' }],
}

/** jsdom lays nothing out: give the layer a 200 × 100 box at (0, 0). */
function sized(element: Element) {
  element.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 200, height: 100, right: 200, bottom: 100 }) as DOMRect
  return element
}

function layer(props: Partial<Parameters<typeof TargetLayer>[0]> = {}) {
  const onPoint = vi.fn()
  const onBox = vi.fn()
  render(
    <TargetLayer tracks={[person, drawn]} time={1.5} onPoint={onPoint} onBox={onBox} {...props} />,
  )
  const surface = sized(screen.getByRole('application'))
  return { surface, onPoint, onBox }
}

function drag(surface: Element, from: [number, number], to: [number, number]) {
  fireEvent.pointerDown(surface, { button: 0, pointerId: 1, clientX: from[0], clientY: from[1] })
  fireEvent.pointerMove(surface, { pointerId: 1, clientX: to[0], clientY: to[1] })
  fireEvent.pointerUp(surface, { pointerId: 1, clientX: to[0], clientY: to[1] })
}

describe('targetsAt', () => {
  test('lists what is on screen, with its box then', () => {
    const [{ track, box }] = targetsAt([person, drawn], 1.5)
    expect(track.track_id).toBe('trk_1')
    expect(box[0]).toBeCloseTo(0.15)
  })

  test('a target is still listed a moment past its keyframes, not long after', () => {
    expect(targetsAt([person], 0.6).map((t) => t.track.track_id)).toEqual(['trk_1'])
    expect(targetsAt([person], 2.4).map((t) => t.track.track_id)).toEqual(['trk_1'])
    expect(targetsAt([person], 3)).toEqual([])
  })
})

describe('TargetLayer', () => {
  test('outlines and names the targets on screen now', () => {
    const { surface } = layer()
    const outlines = surface.querySelectorAll('[data-track]')
    expect([...outlines].map((o) => o.getAttribute('data-track'))).toEqual(['trk_1'])
    expect(outlines[0]).toHaveTextContent('person')
  })

  test('a click sends the point, normalized', () => {
    const { surface, onPoint, onBox } = layer()
    drag(surface, [50, 30], [50, 30])
    expect(onPoint).toHaveBeenCalledWith([0.25, 0.3])
    expect(onBox).not.toHaveBeenCalled()
  })

  test('a tiny wobble is still a click', () => {
    const { surface, onPoint, onBox } = layer()
    drag(surface, [50, 30], [51, 30.5])
    expect(onPoint).toHaveBeenCalledWith([0.25, 0.3])
    expect(onBox).not.toHaveBeenCalled()
  })

  test('a drag sends the box, whichever way it was drawn', () => {
    const { surface, onBox } = layer()
    drag(surface, [150, 80], [100, 40]) // up and to the left
    expect(onBox).toHaveBeenCalledWith([0.5, 0.4, 0.75, 0.8])
  })

  test('shows the box while it is drawn', () => {
    const { surface } = layer()
    fireEvent.pointerDown(surface, { button: 0, pointerId: 1, clientX: 20, clientY: 10 })
    fireEvent.pointerMove(surface, { pointerId: 1, clientX: 120, clientY: 60 })
    expect(screen.getByTestId('drawing')).toHaveStyle({ left: '10%', top: '10%', width: '50%' })
    fireEvent.pointerUp(surface, { pointerId: 1, clientX: 120, clientY: 60 })
    expect(screen.queryByTestId('drawing')).toBeNull()
  })

  test('a drag past the picture stays inside it', () => {
    const { surface, onBox } = layer()
    drag(surface, [180, 50], [260, 140])
    expect(onBox).toHaveBeenCalledWith([0.9, 0.5, 1, 1])
  })

  test('takes nothing while busy', () => {
    const { surface, onPoint, onBox } = layer({ disabled: true })
    drag(surface, [50, 30], [50, 30])
    drag(surface, [10, 10], [100, 80])
    expect(onPoint).not.toHaveBeenCalled()
    expect(onBox).not.toHaveBeenCalled()
  })
})

describe('TargetBar', () => {
  test('picks the effect and leaves target mode', () => {
    const onEffect = vi.fn()
    const onDone = vi.fn()
    render(<TargetBar effect="blur" busy={false} onEffect={onEffect} onDone={onDone} />)
    expect(screen.getByRole('button', { name: 'Blur' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Black box' }))
    expect(onEffect).toHaveBeenCalledWith('box')
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(onDone).toHaveBeenCalled()
  })
})

const state: TimelineState = {
  schema_version: 1,
  masks: {},
  reviewed: {},
  cuts: [],
  mutes: [],
  audio_filters: [],
  rules: [],
}

describe('Player target mode', () => {
  function player(props: Partial<Parameters<typeof Player>[0]> = {}) {
    const onTarget = vi.fn()
    render(
      <Player
        src="/clip.mp4"
        state={state}
        tracks={[person]}
        onDuration={() => {}}
        onTarget={onTarget}
        editable
        {...props}
      />,
    )
    return { onTarget }
  }

  test('has no Target button without a handler', () => {
    player({ onTarget: undefined })
    expect(screen.queryByRole('button', { name: 'Target' })).toBeNull()
  })

  test('the Target button opens the layer; Done and Escape close it', () => {
    player()
    const toggle = screen.getByRole('button', { name: 'Target' })
    expect(screen.queryByRole('application')).toBeNull()
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('application')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.queryByRole('application')).toBeNull()

    fireEvent.click(toggle)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('application')).toBeNull()
  })

  test('a box goes out with the frame time and the effect picked', () => {
    const { onTarget } = player()
    fireEvent.click(screen.getByRole('button', { name: 'Target' }))
    fireEvent.click(screen.getByRole('button', { name: 'Pixelate' }))
    const surface = sized(screen.getByRole('application'))
    const video = document.querySelector('video') as HTMLVideoElement
    video.currentTime = 1.25
    drag(surface, [20, 10], [100, 60])
    expect(onTarget).toHaveBeenCalledWith({
      t: 1.25,
      effect: 'pixelate',
      box: [0.1, 0.1, 0.5, 0.6],
    })
  })

  test('nothing goes out while the project is busy', () => {
    const { onTarget } = player({ editable: false })
    fireEvent.click(screen.getByRole('button', { name: 'Target' }))
    drag(sized(screen.getByRole('application')), [20, 10], [100, 60])
    expect(onTarget).not.toHaveBeenCalled()
    expect(screen.getByText('Working on it…')).toBeInTheDocument()
  })
})
