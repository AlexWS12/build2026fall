import { expect, test, vi } from 'vitest'
import { createAutoClient } from '.'
import type { ApiClient } from './client'
import { createMockClient } from './mock'

const fake = (status: string, health?: () => Promise<never>): ApiClient => ({
  ...createMockClient({ delayMs: 0 }),
  health: health ?? (() => Promise.resolve({ status, backend: 'stub' })),
})

test('uses the backend when it answers', async () => {
  const demo = vi.fn(() => fake('mock'))
  const client = createAutoClient(fake('ok'), demo)
  expect((await client.health()).status).toBe('ok')
  expect(demo).not.toHaveBeenCalled()
})

test('falls back to demo data when the backend is down, and says so', async () => {
  const down = fake('ok', () => Promise.reject(new Error('offline')))
  const client = createAutoClient(down, () => fake('mock'))
  expect((await client.health()).status).toBe('mock')
})

test('decides once, so one session never mixes the two', async () => {
  const health = vi.fn(() => Promise.reject(new Error('offline')))
  const client = createAutoClient(fake('ok', health), () => fake('mock'))
  await Promise.all([client.health(), client.listProjects(), client.getState('prj_mock')])
  expect(health).toHaveBeenCalledTimes(1)
})
