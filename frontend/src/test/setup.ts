import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Vitest globals are off, so Testing Library can't register its own cleanup.
afterEach(() => {
  cleanup()
})

// jsdom has no object URLs; the player only needs a string to put in <video src>.
URL.createObjectURL ??= () => 'blob:test'
URL.revokeObjectURL ??= () => {}
