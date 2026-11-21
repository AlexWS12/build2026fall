import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import type { TimelineState, Track } from '../api/types'
import { Player } from './Player'

// F-42: clicking a mask in the preview selects it, and Remove (or Delete) removes it.

const track = (id: string, box: [number, number, number, number]): Track => ({
  track_id: id,
  label: 'face',
  source: 'detector',
  keyframes: [{ t: 0, box, conf: 0.9, state: 'detected' }],
})

const span = (op: string) => ({
  start: 0,
  end: null,
  op_id: op,
  request_id: 'r_1',
  effect: { type: 'blur' as const, padding: 0, strength: null },
})

// A big head box with a small face inside it, both blurred.
const tracks = [track('trk_head', [0.2, 0.2, 0.8, 0.8]), track('trk_face', [0.4, 0.4, 0.6, 0.6])]
const state: TimelineState = {
  schema_version: 1,
  masks: { trk_head: [span('op_1')], trk_face: [span('op_2')] },
  reviewed: {},
  cuts: [],
  mutes: [],
  audio_filters: [],
  rules: [],
}

// jsdom lays nothing out: give every element a 100x100 box, so a click's clientX/Y are percent.
let rect: ReturnType<typeof vi.spyOn>
beforeEach(() => {
  rect = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, width: 100, height: 100, right: 100, bottom: 100,
    toJSON: () => ({}),
  } as DOMRect) // prettier-ignore
})
afterEach(() => rect.mockRestore())

function setup(editable = true) {
  const onRemoveMasks = vi.fn()
  render(
    <Player
      src="blob:video"
      state={state}
      tracks={tracks}
      onDuration={() => {}}
      onRemoveMasks={onRemoveMasks}
      editable={editable}
    />,
  )
  return onRemoveMasks
}

test('a click selects the smallest mask under it, and Remove removes that one', () => {
  const onRemoveMasks = setup()
  // Inside both boxes: the face is the smaller one, so it wins over the head box.
  fireEvent.click(screen.getByRole('button', { name: 'Select the mask on face 1' }), {
    clientX: 50,
    clientY: 50,
  })

  expect(screen.getByRole('group', { name: 'Selected mask' })).toHaveTextContent('face 2 selected')
  fireEvent.click(screen.getByRole('button', { name: 'Remove blur' }))
  expect(onRemoveMasks).toHaveBeenCalledWith(['trk_face'], 'face 2')
  expect(screen.queryByRole('group', { name: 'Selected mask' })).not.toBeInTheDocument()
})

test('a click on the head box outside the face selects the head box', () => {
  setup()
  fireEvent.click(screen.getByRole('button', { name: 'Select the mask on face 1' }), {
    clientX: 25,
    clientY: 25,
  })
  expect(screen.getByRole('group', { name: 'Selected mask' })).toHaveTextContent('face 1 selected')
})

test('Delete removes the selection and Escape clears it', () => {
  const onRemoveMasks = setup()
  const head = screen.getByRole('button', { name: 'Select the mask on face 1' })

  fireEvent.click(head, { clientX: 25, clientY: 25 })
  fireEvent.keyDown(window, { key: 'Escape' })
  expect(screen.queryByRole('group', { name: 'Selected mask' })).not.toBeInTheDocument()

  fireEvent.click(head, { clientX: 25, clientY: 25 })
  fireEvent.keyDown(window, { key: 'Delete' })
  expect(onRemoveMasks).toHaveBeenCalledWith(['trk_head'], 'face 1')
})

test("nothing is removed while another edit is in flight", () => {
  const onRemoveMasks = setup(false)
  fireEvent.click(screen.getByRole('button', { name: 'Select the mask on face 1' }), {
    clientX: 25,
    clientY: 25,
  })
  expect(screen.getByRole('button', { name: 'Remove blur' })).toBeDisabled()
  fireEvent.keyDown(window, { key: 'Delete' })
  expect(onRemoveMasks).not.toHaveBeenCalled()
})

test('without a remove handler, masks are just drawn', () => {
  render(<Player src="blob:video" state={state} tracks={tracks} onDuration={() => {}} />)
  expect(screen.queryByRole('button', { name: /Select the mask/ })).not.toBeInTheDocument()
})

test('Delete while typing in a text box removes nothing', () => {
  const onRemoveMasks = setup()
  fireEvent.click(screen.getByRole('button', { name: 'Select the mask on face 1' }), {
    clientX: 25,
    clientY: 25,
  })
  const box = document.createElement('textarea')
  document.body.append(box)
  fireEvent.keyDown(box, { key: 'Backspace' })
  expect(onRemoveMasks).not.toHaveBeenCalled()
  box.remove()
})
