import { defineConfig } from '@playwright/test'

/**
 * End-to-end, against the running console and API.
 *
 * Not a unit suite: it needs vite on :5173, the API on :18090, MongoDB and a
 * worker, and it creates real documents named with a stamp so they can be
 * found and removed afterwards. It exists so the whole path - project, round,
 * workflow, input, run, result, notice, repair - is walked the way a person
 * walks it, with a screenshot at every step for the docs.
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE ?? 'http://localhost:5173',
    viewport: { width: 1440, height: 900 },
    locale: 'ko-KR',
    screenshot: 'only-on-failure',
  },
})
