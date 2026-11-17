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

test('forwards the review queue to whichever backend answered', async () => {
  const queue = { analysed: true, items: [], held_seconds: 0, rules_not_audited: [] }
  const getReview = vi.fn(() => Promise.resolve(queue))
  const reviewItem = vi.fn(() => Promise.resolve(queue))
  const client = createAutoClient({ ...fake('ok'), getReview, reviewItem }, () => fake('mock'))
  expect((await client.getReview('p1')).analysed).toBe(true)
  await client.reviewItem('p1', 'rv_1', 'cut')
  expect(getReview).toHaveBeenCalledWith('p1')
  expect(reviewItem).toHaveBeenCalledWith('p1', 'rv_1', 'cut')
})

test('decides once, so one session never mixes the two', async () => {
  const health = vi.fn(() => Promise.reject(new Error('offline')))
  const client = createAutoClient(fake('ok', health), () => fake('mock'))
  await Promise.all([client.health(), client.listProjects(), client.getState('prj_mock')])
  expect(health).toHaveBeenCalledTimes(1)
})
