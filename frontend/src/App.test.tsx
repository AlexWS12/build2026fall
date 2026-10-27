import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, test } from 'vitest'
import App from './App'
import type { ApiClient } from './api'
import { createMockClient } from './api/mock'

const mock = () => createMockClient({ delayMs: 0 })

function upload(name = 'street.mp4') {
  const input = screen.getByLabelText('Video file')
  fireEvent.change(input, { target: { files: [new File(['fake'], name, { type: 'video/mp4' })] } })
}

describe('App shell', () => {
  test('renders the four panels', () => {
    render(<App api={mock()} />)
    for (const name of ['Upload', 'Player', 'Chat', 'Timeline']) {
      expect(screen.getByRole('region', { name })).toBeInTheDocument()
    }
  })

  test('shows the backend status from /api/health', async () => {
    render(<App api={mock()} />)
    expect(await screen.findByText('Backend: ok (stub)')).toBeInTheDocument()
  })

  test('says so when the backend is unreachable', async () => {
    const down: ApiClient = { ...mock(), health: () => Promise.reject(new Error('offline')) }
    render(<App api={down} />)
    expect(await screen.findByText('Backend: unreachable')).toBeInTheDocument()
  })

  test('chat is disabled until a video is uploaded', () => {
    render(<App api={mock()} />)
    expect(screen.getByLabelText('Request')).toBeDisabled()
  })

  test('uploading creates a project and draws its timeline', async () => {
    render(<App api={mock()} />)
    upload('street.mp4')

    expect(await screen.findByText('street')).toBeInTheDocument()
    expect(screen.getByLabelText('Request')).toBeEnabled()
    const lanes = within(screen.getByRole('list', { name: 'Timeline lanes' }))
    for (const lane of ['trk_1', 'trk_2', 'Cuts', 'Mutes', 'Audio filters']) {
      expect(lanes.getByText(lane)).toBeInTheDocument()
    }
  })

  test('a failed upload shows an error', async () => {
    const failing: ApiClient = {
      ...mock(),
      createProject: () => Promise.reject(new Error('413 too large')),
    }
    render(<App api={failing} />)
    upload()
    expect(await screen.findByRole('alert')).toHaveTextContent('Upload failed: 413 too large')
  })

  test('sending a request shows it and the reply', async () => {
    render(<App api={mock()} />)
    upload()
    const input = await screen.findByLabelText('Request')
    await screen.findByText('street')

    fireEvent.change(input, { target: { value: 'blur all faces' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    const messages = within(screen.getByRole('list', { name: 'Messages' }))
    expect(messages.getByText('blur all faces')).toBeInTheDocument()
    expect(await messages.findByText(/Got "blur all faces"/)).toBeInTheDocument()
    expect(input).toHaveValue('')
  })
})
