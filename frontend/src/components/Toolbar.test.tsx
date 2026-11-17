import { fireEvent, render, screen } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { describe, expect, test } from 'vitest'
import type { HistoryReply } from '../api/types'
import { Toolbar } from './Toolbar'

const history: HistoryReply = {
  requests: [
    {
      request_id: 'r_1',
      source: 'agent',
      status: 'applied',
      kinds: ['mute'],
      text: 'mute the first 5 seconds',
      reply: null,
    },
  ],
  can_undo: true,
  can_redo: false,
}

const noop = () => {}

function renderToolbar(props: Partial<ComponentProps<typeof Toolbar>> = {}) {
  return render(
    <Toolbar
      history={history}
      canUndo
      canRedo={false}
      canExport
      exporting={false}
      exported={{ kind: 'none' }}
      onUndo={noop}
      onRedo={noop}
      onExport={noop}
      {...props}
    />,
  )
}

const openHistory = () => fireEvent.click(screen.getByRole('button', { name: 'History' }))
const panel = () => screen.queryByRole('region', { name: 'Request history' })

describe('Toolbar history list', () => {
  test('stays open on a press on Undo or Redo, to watch it change', () => {
    renderToolbar()
    openHistory()
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Undo' }))
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Redo' }))
    expect(panel()).toBeInTheDocument()
  })

  test('closes on a press on Export', () => {
    renderToolbar()
    openHistory()
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Export' }))
    expect(panel()).not.toBeInTheDocument()
  })
})
