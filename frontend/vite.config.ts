/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig, searchForWorkspaceRoot } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // The browser talks to one address: /api goes to the backend (docs/ARCHITECTURE.md, "Code layout").
    proxy: {
      '/api': 'http://localhost:8000',
    },
    fs: {
      // The mock API serves the contract's example states straight from the backend.
      allow: [searchForWorkspaceRoot(process.cwd()), '../backend/src/vibe/contract/examples'],
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
})
