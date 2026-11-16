import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, test } from 'vitest'
import type { HistoryEntry, HistoryReply } from '../api/types'
import { undoTarget } from '../state/session'
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

const history = (requests: HistoryEntry[], can_undo = true): HistoryReply => ({
  requests,
  can_undo,
  can_redo: requests.some((r) => r.status === 'undone'),
})

const three = history([
  entry('r_1', { text: 'mute the first 5 seconds' }),
  entry('r_2', { text: 'cut 0:10 to 0:15', kinds: ['cut'] }),
  entry('r_3', { text: 'blur the faces', kinds: ['add_mask', 'add_mask'], status: 'undone' }),
])

const rows = () => within(screen.getByRole('list', { name: 'Requests' })).getAllByRole('listitem')

describe('HistoryList', () => {
  test('lists every request, newest first', () => {
    render(<HistoryList history={three} />)
    expect(rows().map((row) => row.firstChild?.textContent)).toEqual([
      'blur the faces',
      'cut 0:10 to 0:15',
      'mute the first 5 seconds',
    ])
  })

  test('marks undone requests, and the one Undo would take back', () => {
    render(<HistoryList history={three} />)
    const [blur, cut, mute] = rows()
    expect(within(blur).getByText('Undone')).toBeInTheDocument()
    expect(within(cut).getByText('Undo next')).toBeInTheDocument()
    expect(within(mute).getByText('mute')).toBeInTheDocument()
    expect(screen.getAllByText('Undo next')).toHaveLength(1)
  })

  test('marks nothing as next when there is nothing to undo', () => {
    render(<HistoryList history={history([entry('r_1', { status: 'undone' })], false)} />)
    expect(screen.queryByText('Undo next')).not.toBeInTheDocument()
  })

  test('says so when no request has changed the video', () => {
    render(<HistoryList history={history([], false)} />)
    expect(screen.getByText(/No edits yet\./)).toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })

  test('keeps the full text of a long request reachable', () => {
    const long = 'blur everyone in the background except the man in the red shirt near the door'
    render(<HistoryList history={history([entry('r_1', { text: long })])} />)
    expect(screen.getByTitle(long)).toBeInTheDocument()
  })

  test('lists a long session in full', () => {
    const forty = history(Array.from({ length: 40 }, (_, i) => entry(`r_${i + 1}`)))
    render(<HistoryList history={forty} />)
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

describe('HistoryMenu', () => {
  const open = () => fireEvent.click(screen.getByRole('button', { name: 'History' }))
  const panel = () => screen.queryByRole('region', { name: 'Request history' })

  test('stays closed until the button is pressed, then shows the count', () => {
    render(<HistoryMenu history={three} />)
    expect(panel()).not.toBeInTheDocument()
    open()
    expect(panel()).toHaveTextContent('3 requests')
    expect(screen.getByRole('button', { name: 'History' })).toHaveAttribute('aria-expanded', 'true')
  })

  test('closes on Escape, on a press outside, and on the button again', () => {
    render(<HistoryMenu history={three} />)
    open()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(panel()).not.toBeInTheDocument()

    open()
    fireEvent.pointerDown(document.body)
    expect(panel()).not.toBeInTheDocument()

    open()
    open()
    expect(panel()).not.toBeInTheDocument()
  })

  test('a press inside the list keeps it open', () => {
    render(<HistoryMenu history={three} />)
    open()
    fireEvent.pointerDown(screen.getByText('cut 0:10 to 0:15'))
    expect(panel()).toBeInTheDocument()
  })
})
