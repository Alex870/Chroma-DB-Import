import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const jobs: Array<Record<string, unknown>> = []
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
    reasons: {},
  },
}

function job(id: string, kind: string, result: Record<string, unknown> = {}): Record<string, unknown> {
  return { id, kind, state: 'succeeded', stage: 'complete', database_id: null, preview_id: null,
    can_cancel: false, result, error: null, created_at: '2026-09-07T00:00:00Z',
    started_at: '2026-09-07T00:00:00Z', completed_at: '2026-09-07T00:00:00Z' }
}

vi.mock('./api/client', () => {
  const api = {
    handshake: vi.fn(async () => ({ api_version: 'gui-api-v1', backend_version: 'test' })),
    listDatabases: vi.fn(async () => []),
    getDatabase: vi.fn(),
    getDatabaseHistory: vi.fn(async () => ({ jobs: [] })),
    environmentReport: vi.fn(async () => ({ assets_ready: true, pywebview: '6.2.1' })),
    migrationCandidates: vi.fn(async () => ({ candidates: [], originals_unchanged: true })),
    listJobs: vi.fn(async () => jobs),
    getJob: vi.fn(async (id: string) => jobs.find((item) => item.id === id)),
    getDatabaseContent: vi.fn(async () => ({ episodes: [], speakers: [], shared_context_note: '' })),
    cancelJob: vi.fn(),
    getJobEvents: vi.fn(async () => []),
    pickFolder: vi.fn(async () => 'C:/processed'),
    scanSource: vi.fn(async () => { const item = job('scan-1', 'scan', { episodes: 1, eligible_records: 2, speakers: ['Host'], excluded_files: [], date_range: { start: '2026-01-01', end: '2026-01-01' }, episode_inventory: [{ episode_id: 'episode-1', title: 'Example episode', date: '2026-01-01', document_count: 2, speakers: ['Host'] }] }); jobs.unshift(item); return item }),
    inspectExisting: vi.fn(),
    registerExisting: vi.fn(),
    saveDraft: vi.fn(async (payload: Record<string, unknown>, draftId?: string) => ({ id: draftId ?? 'draft-1', revision: 1, payload })),
    getDraft: vi.fn(async (id: string) => ({ id, revision: 1, payload: {
      source_kind: 'folder', source_ref: { path: 'C:/processed' }, display_name: 'New database',
      target: { path: '' }, selection_policy: { speaker_mode: 'all', asset_filter: 'reviewed_speaker_transcript' },
    } })),
    createPreview: vi.fn(async () => { const item = job('preview-job-1', 'preview', { preview_id: 'preview-1' }); jobs.unshift(item); return item }),
    createMaintenancePreview: vi.fn(),
    checkDatabase: vi.fn(),
    getPreview: vi.fn(async () => preview),
    applyPreview: vi.fn(async () => { const item = job('import-1', 'import'); jobs.unshift(item); return item }),
    archiveDatabase: vi.fn(),
    renameDatabase: vi.fn(),
    updateDatabaseSettings: vi.fn(),
    startSourceAction: vi.fn(),
    openDatabaseFolder: vi.fn(),
  }
  return { client: api, ClientError: class extends Error { detail: unknown; constructor(detail: unknown) { super(String((detail as { message?: string }).message ?? '')); this.detail = detail } } }
})

vi.mock('./api/mockClient', () => ({ mockClient: {} }))

import App from './App'
import { client as testClient } from './api/client'
import type { DatabaseRecord } from './api/types'

describe('modern database creation flow', () => {
  afterEach(() => { cleanup(); window.history.replaceState({}, '', '/') })

  it('persists and reuses the draft while moving through review', async () => {
    window.localStorage.clear()
    jobs.splice(0)
    render(<App />)

    await waitFor(() => expect(screen.getByText('No databases yet')).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Create database/i })[0])
    fireEvent.click(screen.getByRole('button', { name: 'Choose folder' }))
    await waitFor(() => expect(screen.getByDisplayValue('C:/processed')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Scan source' }))
    await waitFor(() => expect(screen.getByText('1 episodes')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))

    fireEvent.change(screen.getByLabelText('Database name'), { target: { value: 'Example' } })
    fireEvent.change(screen.getByLabelText('Storage location'), { target: { value: 'C:/exports/example' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review' }))
    await waitFor(() => expect(screen.getByText('Ready to create')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    await waitFor(() => {
      const calls = (testClient.saveDraft as ReturnType<typeof vi.fn>).mock.calls
      expect(calls.length).toBeGreaterThanOrEqual(2)
      expect(calls.at(-1)?.[1]).toBe('draft-1')
    })
  })

  it('opens the managed source connections area for the contexts workspace', async () => {
    window.history.replaceState({}, '', '/?workspace=contexts')
    render(<App />)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Source connections' })).toBeTruthy())
  })

  it('offers registration when creation finds an existing destination', async () => {
    ;(testClient.createPreview as ReturnType<typeof vi.fn>).mockImplementationOnce(async () => {
      const item = job('preview-job-existing', 'preview')
      item.state = 'failed'
      item.error = { code: 'TARGET_EXISTS', message: 'The destination already exists.' }
      jobs.unshift(item)
      return item
    })
    render(<App />)
    await waitFor(() => expect(screen.getByText('No databases yet')).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Create database/i })[0])
    fireEvent.click(screen.getByRole('button', { name: 'Choose folder' }))
    await waitFor(() => expect(screen.getByDisplayValue('C:/processed')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Scan source' }))
    await waitFor(() => expect(screen.getByText('1 episodes')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    fireEvent.change(screen.getByLabelText('Database name'), { target: { value: 'Existing' } })
    fireEvent.change(screen.getByLabelText('Storage location'), { target: { value: 'C:/exports/existing' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Destination already exists' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Use existing database' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Register a database' })).toBeTruthy())
    expect(screen.getByDisplayValue('C:/exports/existing')).toBeTruthy()
  })

  it('offers an explicit supported launcher repair command', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByText('No databases yet')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Settings & help/ }))
    expect(screen.getByRole('button', { name: 'Copy repair command' })).toBeTruthy()
    expect(screen.getByText(/Repair is explicit and uses the supported launcher/)).toBeTruthy()
  })

  it('offers a non-persistent content selection for an update review', async () => {
    const database: DatabaseRecord = {
      id: 'db-update', display_name: 'Example database', source_kind: 'folder',
      source_ref: { path: 'C:/processed' }, target: { path: 'C:/exports/example' }, downstream_identity: null,
      selection_policy: { speaker_mode: 'all', excluded_speakers: [], allowlist_speakers: [], episode_overrides: {}, excluded_episode_ids: [], asset_filter: 'reviewed_speaker_transcript', asset_pattern: '' },
      settings_revision: 1, archived: false, last_check: null, created_at: '2026-09-07T00:00:00Z', updated_at: '2026-09-07T00:00:00Z',
    }
    ;(testClient.listDatabases as ReturnType<typeof vi.fn>).mockResolvedValueOnce([database])
    ;(testClient.getDatabaseContent as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ episodes: [{ episode_id: 'episode-1', title: 'Example episode', document_count: 2, speakers: ['Host'] }], speakers: ['Host'], shared_context_note: 'Shared context remains available.' })
    ;(testClient.checkDatabase as ReturnType<typeof vi.fn>).mockResolvedValueOnce(job('check-update', 'preview', { preview_id: 'preview-1' }))
    ;(testClient.getJob as ReturnType<typeof vi.fn>).mockResolvedValue(job('check-update', 'preview', { preview_id: 'preview-1' }))
    render(<App />)
    await waitFor(() => expect(screen.getByText('Example database')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Update' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Change content for this update' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Change content for this update' }))
    expect(screen.getByRole('heading', { name: 'Change content for this update' })).toBeTruthy()
    expect(screen.getByText(/will not change the saved database policy/)).toBeTruthy()
  })

  it('shows source readiness counts separately from active database health', async () => {
    const sourceJob = job('source-inspect', 'source', { status: {
      partition_id: 'part-one', display_name: 'Podcast source', ready_to_publish: false,
      pending: 1, failed: 2, interrupted: 0, quarantined: 1, completed: 3,
      active_release_id: 'release-1', warnings: ['One item needs attention.'],
    } })
    ;(testClient.startSourceAction as ReturnType<typeof vi.fn>).mockResolvedValueOnce(sourceJob)
    ;(testClient.getJob as ReturnType<typeof vi.fn>).mockResolvedValue(sourceJob)
    window.history.replaceState({}, '', '/?workspace=contexts')
    render(<App />)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Source connections' })).toBeTruthy())
    fireEvent.change(screen.getByLabelText('Podcast-RAG source root'), { target: { value: 'C:/Podcast RAG' } })
    fireEvent.change(screen.getByLabelText('Partition ID'), { target: { value: 'part-one' } })
    fireEvent.click(screen.getByRole('button', { name: 'Inspect source' }))
    await waitFor(() => expect(screen.getByRole('region', { name: 'Source readiness report' })).toBeTruthy())
    expect(screen.getByText('Completed')).toBeTruthy()
    expect(screen.getByText('Pending')).toBeTruthy()
    expect(screen.getByText('Failed')).toBeTruthy()
    expect(screen.getByText('Quarantined')).toBeTruthy()
    expect(screen.getByText(/These counts describe source readiness/)).toBeTruthy()
  })

  it('routes a prepared source back to the database update review', async () => {
    const database: DatabaseRecord = {
      id: 'db-prepared', display_name: 'Prepared database', source_kind: 'managed',
      source_ref: { source_root: 'C:/Podcast RAG', partition_id: 'part-one' }, target: { path: 'C:/exports/prepared' }, downstream_identity: null,
      selection_policy: { speaker_mode: 'all', excluded_speakers: [], allowlist_speakers: [], episode_overrides: {}, excluded_episode_ids: [], asset_filter: 'reviewed_speaker_transcript', asset_pattern: '' },
      settings_revision: 1, archived: false, last_check: null, created_at: '2026-09-07T00:00:00Z', updated_at: '2026-09-07T00:00:00Z',
    }
    const inspectJob = job('inspect-source', 'source', { status: { partition_id: 'part-one', ready_to_publish: true, pending: 0, failed: 0, interrupted: 0, quarantined: 0, completed: 3 } })
    const prepareJob = job('prepare-source', 'source', { release: { upstream_release_id: 'release-2' } })
    const reviewJob = job('check-prepared', 'preview', { preview_id: 'preview-1' })
    ;(testClient.listDatabases as ReturnType<typeof vi.fn>).mockResolvedValueOnce([database])
    ;(testClient.startSourceAction as ReturnType<typeof vi.fn>).mockResolvedValueOnce(inspectJob).mockResolvedValueOnce(prepareJob)
    ;(testClient.checkDatabase as ReturnType<typeof vi.fn>).mockResolvedValueOnce(reviewJob)
    ;(testClient.getJob as ReturnType<typeof vi.fn>).mockImplementation(async (id: string) => id === inspectJob.id ? inspectJob : id === prepareJob.id ? prepareJob : reviewJob)
    render(<App />)
    await waitFor(() => expect(screen.getByText('Prepared database')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Prepared database' }))
    fireEvent.click(screen.getByRole('button', { name: /Source connections/ }))
    fireEvent.change(screen.getByLabelText('Podcast-RAG source root'), { target: { value: 'C:/Podcast RAG' } })
    fireEvent.change(screen.getByLabelText('Partition ID'), { target: { value: 'part-one' } })
    fireEvent.click(screen.getByRole('button', { name: 'Inspect source' }))
    await waitFor(() => expect(screen.getByRole('region', { name: 'Source readiness report' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Prepare source release' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Apply update' })).toBeTruthy())
  })

  it('opens redundancy analysis as an explicit CLI-only advanced area', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByText('No databases yet')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Redundancy analysis/ }))
    expect(screen.getByRole('heading', { name: 'Redundancy analysis' })).toBeTruthy()
    expect(screen.getByText('CLI-only workflow')).toBeTruthy()
    expect(screen.getByText(/does not approve deletion/)).toBeTruthy()
  })

  it('shows field-level migration proposals before any acceptance', async () => {
    ;(testClient.migrationCandidates as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ candidates: [{ kind: 'legacy_ui_state', status: 'proposal', path: 'C:/state/ui_state.json', changes: { display_name: 'Legacy Show', source_kind: 'folder' } }], originals_unchanged: true })
    render(<App />)
    await waitFor(() => expect(screen.getByText('No databases yet')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Settings & help/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Review migration candidates' }))
    await waitFor(() => expect(screen.getByText('View proposed field changes')).toBeTruthy())
    fireEvent.click(screen.getByText('View proposed field changes'))
    expect(screen.getByText(/Legacy Show/)).toBeTruthy()
    expect(screen.getByText(/Original state and managed catalog files remain unchanged/)).toBeTruthy()
  })

  it('shows actual operation totals and active-database state in Activity', async () => {
    jobs.splice(0, jobs.length, job('import-summary', 'import', { actual_writes: 4, imported_episodes: 2, skipped_episodes: 1, retained_records: 3, active_database_state: 'new_version_active', warnings: ['One warning.'] }))
    render(<App />)
    await waitFor(() => expect(screen.getByText('No databases yet')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Activity/ }))
    await waitFor(() => expect(screen.getByRole('region', { name: 'Operation result summary' })).toBeTruthy())
    expect(screen.getByText('Writes')).toBeTruthy()
    expect(screen.getByText('new_version_active')).toBeTruthy()
    expect(screen.getByText(/One warning/)).toBeTruthy()
    jobs.splice(0)
  })

  it('shows exact maintenance records and recovery scope before deletion', async () => {
    const database: DatabaseRecord = {
      id: 'db-maintenance', display_name: 'Maintenance database', source_kind: 'folder',
      source_ref: { path: 'C:/processed' }, target: { path: 'C:/exports/maintenance' }, downstream_identity: { profile: 'qwen3-embedding-4b-shadow' },
      selection_policy: { speaker_mode: 'all', excluded_speakers: [], allowlist_speakers: [], episode_overrides: {}, excluded_episode_ids: [], asset_filter: 'reviewed_speaker_transcript', asset_pattern: '' },
      settings_revision: 1, archived: false, last_check: null, created_at: '2026-09-07T00:00:00Z', updated_at: '2026-09-07T00:00:00Z',
    }
    const retainedReview = { ...preview, preview_id: 'retained-review', database_id: database.id, operation: 'update', effects: { ...preview.effects, writes: 0, retained_missing_ids: { total: 2, items: ['record-old-1', 'record-old-2'] }, episode_changes: [{ episode_id: 'episode-1', title: 'Example episode', records: 2, added: 0, changed: 0, metadata_only: 0, unchanged: 2 }], reasons: { 'record-old-1': 'retained by ordinary update', 'record-old-2': 'retained by ordinary update' } } }
    const removalReview = { ...retainedReview, preview_id: 'removal-review', operation: 'remove_outdated', required_acknowledgments: ['REMOVE_OUTDATED_RECORDS'], effects: { ...retainedReview.effects, retained_missing_ids: { total: 0, items: [] }, delete_ids: { total: 2, items: ['record-old-1', 'record-old-2'] }, reasons: { 'record-old-1': 'Explicitly accepted as outdated', 'record-old-2': 'Explicitly accepted as outdated' } } }
    const checkJob = job('check-maintenance', 'preview', { preview_id: retainedReview.preview_id })
    ;(testClient.listDatabases as ReturnType<typeof vi.fn>).mockResolvedValueOnce([database])
    ;(testClient.checkDatabase as ReturnType<typeof vi.fn>).mockResolvedValueOnce(checkJob)
    ;(testClient.getJob as ReturnType<typeof vi.fn>).mockResolvedValue(checkJob)
    ;(testClient.getPreview as ReturnType<typeof vi.fn>).mockResolvedValueOnce(retainedReview)
    ;(testClient.createMaintenancePreview as ReturnType<typeof vi.fn>).mockResolvedValueOnce(removalReview)
    render(<App />)
    await waitFor(() => expect(screen.getByText('Maintenance database')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Maintenance database' }))
    fireEvent.click(screen.getByRole('tab', { name: 'Maintenance' }))
    fireEvent.click(screen.getByRole('button', { name: 'Review remove outdated records' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Review removal of these records' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Review removal of these records' }))
    await waitFor(() => expect(screen.getByText('Exact records and reasons (2)')).toBeTruthy())
    expect(screen.getByText('record-old-1')).toBeTruthy()
    expect(screen.getAllByText(/Explicitly accepted as outdated/).length).toBe(2)
  })

  it('shows a prescribed recovery action when the update source is unavailable', async () => {
    const database: DatabaseRecord = {
      id: 'db-unavailable', display_name: 'Unavailable source', source_kind: 'folder',
      source_ref: { path: 'C:/disconnected/processed' }, target: { path: 'C:/exports/unavailable' }, downstream_identity: { profile: 'qwen3-embedding-4b-shadow' },
      selection_policy: { speaker_mode: 'all', excluded_speakers: [], allowlist_speakers: [], episode_overrides: {}, excluded_episode_ids: [], asset_filter: 'reviewed_speaker_transcript', asset_pattern: '' },
      settings_revision: 1, archived: false, last_check: null, created_at: '2026-09-07T00:00:00Z', updated_at: '2026-09-07T00:00:00Z',
    }
    const failedCheck = job('check-unavailable', 'preview')
    failedCheck.state = 'failed'
    failedCheck.error = { code: 'SOURCE_UNAVAILABLE', message: 'The source folder is unavailable.' }
    ;(testClient.listDatabases as ReturnType<typeof vi.fn>).mockResolvedValueOnce([database])
    ;(testClient.checkDatabase as ReturnType<typeof vi.fn>).mockResolvedValueOnce(failedCheck)
    ;(testClient.getJob as ReturnType<typeof vi.fn>).mockResolvedValue(failedCheck)
    render(<App />)
    await waitFor(() => expect(screen.getByText('Unavailable source')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Update' }))
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
    expect(screen.getByText(/active database has not been changed/i)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Open Source connections' })).toBeTruthy()
  })

  it('blocks a profile mismatch with a separate-database action and compatibility details', async () => {
    const database: DatabaseRecord = {
      id: 'db-profile', display_name: 'Profile-bound database', source_kind: 'folder',
      source_ref: { path: 'C:/processed' }, target: { path: 'C:/exports/profile' }, downstream_identity: { profile: 'qwen3-embedding-4b-shadow' },
      selection_policy: { speaker_mode: 'all', excluded_speakers: [], allowlist_speakers: [], episode_overrides: {}, excluded_episode_ids: [], asset_filter: 'reviewed_speaker_transcript', asset_pattern: '' },
      settings_revision: 1, archived: false, last_check: null, created_at: '2026-09-07T00:00:00Z', updated_at: '2026-09-07T00:00:00Z',
    }
    const profilePreview = { ...preview, database_id: database.id, representation: { profile: 'legacy-bge' }, validation_findings: [{ severity: 'error', code: 'PROFILE_MISMATCH', message: 'The selected representation cannot update this database in place.' }] }
    const checkJob = job('check-profile', 'preview', { preview_id: profilePreview.preview_id })
    ;(testClient.listDatabases as ReturnType<typeof vi.fn>).mockResolvedValueOnce([database])
    ;(testClient.checkDatabase as ReturnType<typeof vi.fn>).mockResolvedValueOnce(checkJob)
    ;(testClient.getJob as ReturnType<typeof vi.fn>).mockResolvedValue(checkJob)
    ;(testClient.getPreview as ReturnType<typeof vi.fn>).mockResolvedValueOnce(profilePreview)
    render(<App />)
    await waitFor(() => expect(screen.getByText('Profile-bound database')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Update' }))
    await waitFor(() => expect(screen.getByText('Mismatch · legacy-bge')).toBeTruthy())
    expect(screen.getByText(/selected representation cannot update/i)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Create separate database' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Apply update' })).toBeNull()
  })
})
