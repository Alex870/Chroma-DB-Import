import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './playwright',
  globalSetup: './playwright/global-setup.ts',
  fullyParallel: false,
  reporter: 'line',
  use: {
    baseURL: 'http://127.0.0.1:4173',
    headless: true,
    trace: 'off',
  },
})
