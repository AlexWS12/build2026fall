import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { useRef } from 'react'
import { describe, expect, test } from 'vitest'
import type { HistoryEntry, HistoryReply } from '../api/types'
import { redoTarget, undoneForGood, undoTarget } from '../state/session'
import { requestLabel } from './history'
import { HistoryList, HistoryMenu } from './HistoryMenu'

const entry = (id: string, overrides: Partial<HistoryEntry> = {}): HistoryEntry => ({
  request_id: id,
  source: 'agent',
  status: 'applied',
  kinds: ['mute'],
  text: `request ${id}`,
  reply: null,
  ...overrides,
})

// By default Redo is possible when the newest request is undone, as it would be on the server.
const history = (
  requests: HistoryEntry[],
  can_undo = true,
  can_redo = requests.at(-1)?.status === 'undone',
): HistoryReply => ({ requests, can_undo, can_redo })

const three = history([
  entry('r_1', { text: 'mute the first 5 seconds' }),
  entry('r_2', { text: 'cut 0:10 to 0:15', kinds: ['cut'] }),
  entry('r_3', { text: 'blur the faces', kinds: ['add_mask', 'add_mask'], status: 'undone' }),
])

const rows = () => within(screen.getByRole('list', { name: 'Requests' })).getAllByRole('listitem')

describe('HistoryList', () => {
  test('lists every request, newest first', () => {
    render(<HistoryList history={three} canUndo canRedo />)
    expect(rows().map((row) => row.firstChild?.firstChild?.textContent)).toEqual([
      'blur the faces',
      'cut 0:10 to 0:15',
      'mute the first 5 seconds',
    ])
  })

  test('marks the requests Undo and Redo would act on', () => {
    render(<HistoryList history={three} canUndo canRedo />)
    const [blur, cut, mute] = rows()
    expect(within(blur).getByText('Redo next')).toBeInTheDocument()
    expect(within(cut).getByText('Undo next')).toBeInTheDocument()
    expect(within(mute).getByText('mute')).toBeInTheDocument()
    expect(screen.getAllByText('Undo next')).toHaveLength(1)
    expect(screen.getAllByText('Redo next')).toHaveLength(1)
  })

  test('marks undone requests a newer request has made permanent', () => {
    // r_1 undone, then r_2 made (clearing redo), then r_3 made and undone: Redo brings back r_3.
    const left = history([
      entry('r_1', { status: 'undone' }),
      entry('r_2'),
      entry('r_3', { status: 'undone' }),
    ])
    render(<HistoryList history={left} canUndo canRedo />)
    const [r3, r2, r1] = rows()
    expect(within(r3).getByText('Redo next')).toBeInTheDocument()
    expect(within(r2).getByText('Undo next')).toBeInTheDocument()
    expect(within(r1).getByText('Undone for good')).toHaveAttribute(
      'title',
      "A newer request came after this one, so Redo can't bring it back.",
    )
  })

  test('marks every undone request permanent when nothing can be redone', () => {
    const cleared = history([entry('r_1', { status: 'undone' }), entry('r_2', { status: 'undone' })], false, false)
    render(<HistoryList history={cleared} canUndo canRedo />)
    expect(screen.getAllByText('Undone for good')).toHaveLength(2)
    expect(screen.queryByText('Redo next')).not.toBeInTheDocument()
  })

  test("claims nothing about redo when the history can't tell", () => {
    // Both "r_1, undo r_1, r_2, undo r_2" and "r_1, r_2, undo r_2, undo r_1" read like this.
    const unclear = history([entry('r_1', { status: 'undone' }), entry('r_2', { status: 'undone' })], false)
    render(<HistoryList history={unclear} canUndo canRedo />)
    expect(screen.getAllByText('Undone')).toHaveLength(2)
    expect(screen.queryByText('Redo next')).not.toBeInTheDocument()
    expect(screen.queryByText('Undone for good')).not.toBeInTheDocument()
  })

  test('marks nothing as Redo next while Redo is disabled', () => {
    render(<HistoryList history={three} canUndo canRedo={false} />)
    expect(screen.queryByText('Redo next')).not.toBeInTheDocument()
    expect(within(rows()[0]).getByText('Undone')).toBeInTheDocument()
  })

  test('keeps the edit kinds on rows with a badge, each kind once', () => {
    render(<HistoryList history={three} canUndo canRedo />)
    const [blur, cut] = rows()
    expect(within(blur).getByText('add mask')).toBeInTheDocument()
    expect(within(cut).getByText('cut')).toBeInTheDocument()
  })

  test("doesn't repeat the kinds when they already name the request", () => {
    const hand = entry('r_1', { source: 'user', text: null, kinds: ['cut'] })
    render(<HistoryList history={history([hand])} canUndo canRedo />)
    expect(rows()[0]).toHaveTextContent(/^Hand edit: cutUndo next$/)
  })

  test('marks nothing as next when there is nothing to undo', () => {
    render(<HistoryList history={history([entry('r_1', { status: 'undone' })], false)} canUndo canRedo />)
    expect(screen.queryByText('Undo next')).not.toBeInTheDocument()
  })

  test('marks nothing as next while Undo is disabled, even when the server could undo', () => {
    // A request is planning, rendering or exporting: the toolbar disables Undo.
    render(<HistoryList history={three} canUndo={false} canRedo />)
    expect(three.can_undo).toBe(true)
    expect(screen.queryByText('Undo next')).not.toBeInTheDocument()
  })

  test('says so when no request has changed the video', () => {
    render(<HistoryList history={history([], false)} canUndo canRedo />)
    expect(screen.getByText(/No edits yet\./)).toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })

  test('keeps the full text of a long request reachable', () => {
    const long = 'blur everyone in the background except the man in the red shirt near the door'
    render(<HistoryList history={history([entry('r_1', { text: long })])} canUndo canRedo />)
    expect(screen.getByTitle(long)).toBeInTheDocument()
  })

  test('lists a long session in full', () => {
    const forty = history(Array.from({ length: 40 }, (_, i) => entry(`r_${i + 1}`)))
    render(<HistoryList history={forty} canUndo canRedo />)
    expect(rows()).toHaveLength(40)
    expect(rows()[0]).toHaveTextContent('request r_40')
    expect(rows()[39]).toHaveTextContent('request r_1')
  })
})

describe('requestLabel', () => {
  test('uses the chat message when there is one', () => {
    expect(requestLabel(entry('r_1', { text: 'mute him' }))).toBe('mute him')
  })

  test('names a hand edit by what it changed, each kind once', () => {
    const hand = entry('r_1', { source: 'user', text: null, kinds: ['add_mask', 'add_mask', 'cut'] })
    expect(requestLabel(hand)).toBe('Hand edit: add mask, cut')
  })

  test('falls back to the request id when nothing else is known', () => {
    expect(requestLabel(entry('r_9', { text: null, kinds: [] }))).toBe('r_9')
  })
})

describe('undoTarget', () => {
  test('is the latest request still applied', () => {
    expect(undoTarget(three)).toBe('r_2')
  })

  test('skips requests undone earlier and left behind by a newer one', () => {
    const mixed = history([entry('r_1'), entry('r_2', { status: 'undone' }), entry('r_3')])
    expect(undoTarget(mixed)).toBe('r_3')
  })

  test('is nothing when the server says undo is unavailable', () => {
    expect(undoTarget(history([entry('r_1')], false))).toBeNull()
  })
})

describe('redoTarget and undoneForGood', () => {
  const undone = (id: string) => entry(id, { status: 'undone' })

  test('one undone request after the latest applied one: Redo brings it back', () => {
    const h = history([entry('r_1'), undone('r_2')])
    expect(redoTarget(h)).toBe('r_2')
    expect(undoneForGood(h)).toEqual(new Set())
  })

  test('undone requests before the latest applied one are gone for good', () => {
    const h = history([undone('r_1'), entry('r_2'), undone('r_3')])
    expect(redoTarget(h)).toBe('r_3')
    expect(undoneForGood(h)).toEqual(new Set(['r_1']))
  })

  test('two or more undone after the latest applied one: no target, none marked permanent', () => {
    const h = history([entry('r_1'), undone('r_2'), undone('r_3')])
    expect(redoTarget(h)).toBeNull()
    expect(undoneForGood(h)).toEqual(new Set())
  })

  test('when the server says redo is unavailable, every undone request is gone for good', () => {
    const h = history([entry('r_1'), undone('r_2')], true, false)
    expect(redoTarget(h)).toBeNull()
    expect(undoneForGood(h)).toEqual(new Set(['r_2']))
  })

  test('nothing undone after the latest applied one: no target, even if the server says redo', () => {
    expect(redoTarget(history([undone('r_1'), entry('r_2')], true, true))).toBeNull()
  })
})

describe('HistoryMenu', () => {
  const open = () => fireEvent.click(screen.getByRole('button', { name: 'History' }))
  const panel = () => screen.queryByRole('region', { name: 'Request history' })

  test('stays closed until the button is pressed, then shows the count', () => {
    render(<HistoryMenu history={three} canUndo canRedo />)
    expect(panel()).not.toBeInTheDocument()
    open()
    expect(panel()).toHaveTextContent('3 requests')
    expect(screen.getByRole('button', { name: 'History' })).toHaveAttribute('aria-expanded', 'true')
  })

  test('closes on a press outside, and on the button again', () => {
    render(<HistoryMenu history={three} canUndo canRedo />)
    open()
    fireEvent.pointerDown(document.body)
    expect(panel()).not.toBeInTheDocument()

    open()
    open()
    expect(panel()).not.toBeInTheDocument()
  })

  test('a press inside the list keeps it open', () => {
    render(<HistoryMenu history={three} canUndo canRedo />)
    open()
    fireEvent.pointerDown(screen.getByText('cut 0:10 to 0:15'))
    expect(panel()).toBeInTheDocument()
  })

  test('Escape inside the menu closes it and puts focus back on the History button', () => {
    render(<HistoryMenu history={three} canUndo canRedo />)
    open()
    const list = screen.getByRole('list', { name: 'Requests' })
    act(() => list.focus())
    fireEvent.keyDown(list, { key: 'Escape' })
    expect(panel()).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'History' })).toHaveFocus()
  })

  test('Escape typed somewhere else, like the chat box, leaves it open', () => {
    render(
      <>
        <input aria-label="Request" />
        <HistoryMenu history={three} canUndo canRedo />
      </>,
    )
    open()
    const chat = screen.getByRole('textbox', { name: 'Request' })
    fireEvent.keyDown(chat, { key: 'Escape' })
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(panel()).toBeInTheDocument()
  })

  test('closes when focus moves out of it, as when tabbing away', () => {
    render(
      <>
        <HistoryMenu history={three} canUndo canRedo />
        <button type="button">Export</button>
      </>,
    )
    open()
    act(() => screen.getByRole('list', { name: 'Requests' }).focus())
    expect(panel()).toBeInTheDocument()
    act(() => screen.getByRole('button', { name: 'Export' }).focus())
    expect(panel()).not.toBeInTheDocument()
  })

  test('stays open when focus moves to an element it is told to keep open for', () => {
    function WithUndo() {
      const undo = useRef<HTMLButtonElement>(null)
      return (
        <>
          <button type="button" ref={undo}>
            Undo
          </button>
          <HistoryMenu history={three} canUndo canRedo keepOpen={[undo]} />
        </>
      )
    }
    render(<WithUndo />)
    open()
    act(() => screen.getByRole('button', { name: 'Undo' }).focus())
    expect(panel()).toBeInTheDocument()
  })
})
