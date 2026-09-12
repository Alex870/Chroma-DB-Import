import { expect, test, type Page } from '@playwright/test'

async function installTestBridge(page: Page) {
  await page.addInitScript(() => {
    const jobs: Array<Record<string, unknown>> = []
    const now = '2026-09-07T00:00:00Z'
    const envelope = (data: unknown) => ({ ok: true, data })
    const makeJob = (id: string, kind: string, result: Record<string, unknown> = {}) => ({
      id, kind, state: 'succeeded', stage: 'complete', database_id: null, preview_id: null,
      can_cancel: false, result, error: null, created_at: now, started_at: now, completed_at: now,
    })
    const preview = {
      preview_id: 'preview-1', operation: 'create', database_id: null, status: 'ready', source_snapshot: {},
      target_identity: { path: 'C:/exports/example' }, selection_policy: {
        speaker_mode: 'all', excluded_speakers: [], allowlist_speakers: [], episode_overrides: {},
        excluded_episode_ids: [], asset_filter: 'reviewed_speaker_transcript', asset_pattern: '',
      }, representation: { profile: 'qwen3-embedding-4b-shadow' }, validation_findings: [],
      required_acknowledgments: [], effects: {
        episodes_total: 1, records_total: 2, writes: 2,
        insert_ids: { total: 2, items: ['record-one', 'record-two'] },
        replace_ids: { total: 0, items: [] }, metadata_only_ids: { total: 0, items: [] },
        unchanged_ids: { total: 0, items: [] }, retained_missing_ids: { total: 0, items: [] },
        delete_ids: { total: 0, items: [] }, episode_changes: [],
      },
    }
    const api: Record<string, (payload?: any) => Promise<unknown>> = {
      handshake: async () => envelope({ api_version: 'gui-api-v1', backend_version: 'browser-test' }),
      list_databases: async () => envelope([]),
      list_jobs: async () => envelope(jobs),
      get_job: async (payload) => envelope(jobs.find((item) => item.id === payload.job_id) ?? null),
      get_job_events: async () => envelope([]),
      pick_folder: async () => envelope('C:/processed'),
      save_draft: async (payload) => envelope({ id: payload.draft_id ?? 'draft-1', revision: 1, payload: payload.payload }),
      get_draft: async (payload) => envelope({ id: payload.draft_id, revision: 1, payload: {} }),
      scan_source: async () => {
        const item = makeJob('scan-1', 'scan', { episodes: 1, eligible_records: 2, speakers: ['Host'], excluded_files: [], date_range: { start: '2026-01-01', end: '2026-01-01' }, episode_inventory: [{ episode_id: 'episode-1', title: 'Example episode', date: '2026-01-01', document_count: 2, speakers: ['Host'] }] })
        jobs.unshift(item)
        return envelope(item)
      },
      create_preview: async () => {
        const item = makeJob('preview-1', 'preview', { preview_id: 'preview-1' })
        jobs.unshift(item)
        return envelope(item)
      },
      get_preview: async () => envelope(preview),
      apply_preview: async () => {
        const item = makeJob('import-1', 'import')
        jobs.unshift(item)
        return envelope(item)
      },
      get_database: async () => envelope(null),
      get_database_history: async () => envelope({ jobs: [] }),
      get_database_content: async () => envelope({ episodes: [], speakers: [], shared_context_note: '' }),
      environment_report: async () => envelope({ assets_ready: true, pywebview: 'browser-test' }),
      migration_candidates: async () => envelope({ candidates: [], originals_unchanged: true }),
      echo: async (payload) => envelope(payload),
      synthetic_progress: async () => envelope(makeJob('diagnostic-1', 'diagnostic')),
      retry_job: async () => envelope(makeJob('retry-1', 'preview')),
      cancel_job: async (payload) => envelope(jobs.find((item) => item.id === payload.job_id)),
      inspect_existing: async () => envelope({}),
      register_existing: async () => envelope({}),
      create_maintenance_preview: async () => envelope(preview),
      check_database: async () => envelope(makeJob('check-1', 'preview', { preview_id: 'preview-1' })),
      archive_database: async () => envelope({}),
      rename_database: async () => envelope({}),
      update_database_settings: async () => envelope({}),
      start_source_action: async () => envelope(makeJob('source-1', 'source')),
      open_database_folder: async () => envelope('C:/exports/example'),
      export_report: async () => envelope({ report_id: 'report-1', path: 'C:/reports/report-1.json' }),
    }
    ;(window as Window & { pywebview?: { api: typeof api } }).pywebview = { api }
  })
}

test('runs the three-step creation journey through a browser bridge', async ({ page }) => {
  await installTestBridge(page)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'No databases yet' })).toBeVisible()
  await page.getByRole('button', { name: /Create database/i }).first().click()
  await page.getByRole('button', { name: 'Choose folder' }).click()
  await expect(page.locator('input').first()).toHaveValue('C:/processed')
  await page.getByRole('button', { name: 'Scan source' }).click()
  await expect(page.getByText('1 episodes', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByLabel('Database name').fill('Example')
  await page.getByLabel('Storage location').fill('C:/exports/example')
  await page.getByRole('button', { name: 'Review' }).click()
  await expect(page.getByText('Ready to create', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Create database' }).click()
  await expect(page.getByRole('heading', { name: 'Activity' })).toBeVisible()
  await expect(page.getByText('Database import', { exact: true })).toBeVisible()
})

test('opens Source connections for the managed workspace', async ({ page }) => {
  await installTestBridge(page)
  await page.goto('/?workspace=contexts')
  await expect(page.getByRole('heading', { name: 'Source connections' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Inspect source' })).toBeVisible()
})

test('shows a connection error when the privileged bridge is absent', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('The desktop connection is unavailable', { timeout: 7000 })
})
