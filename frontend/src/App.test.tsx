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
    reconcileSources: vi.fn(async () => ({ checked_at: '2026-09-07T00:00:00Z', connections: [], contexts: [], source_observations: [] })),
    listContexts: vi.fn(async () => []),
    getContext: vi.fn(async (ref: Record<string, unknown>) => ({ ref, detail: { context_settings: { settings: {} } } })),
    listDatabases: vi.fn(async () => []),
    getDatabase: vi.fn(),
    restoreDatabase: vi.fn(async (id: string) => ({ id, display_name: 'Restored database', source_kind: 'managed', source_ref: {}, target: { path: 'C:/exports/restored' }, downstream_identity: null, selection_policy: { speaker_mode: 'all', excluded_speakers: [], allowlist_speakers: [], episode_overrides: {}, excluded_episode_ids: [], asset_filter: 'reviewed_speaker_transcript', asset_pattern: '' }, settings_revision: 1, archived: false, last_check: null, created_at: '2026-09-07T00:00:00Z', updated_at: '2026-09-07T00:00:00Z' })),
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
    adoptDatabaseLink: vi.fn(async (payload: Record<string, unknown>) => ({ ...payload, state: 'linked', origin: 'adopted' })),
    selectDatabaseLink: vi.fn(async (payload: Record<string, unknown>) => ({ ...payload, state: 'linked', selected: true })),
    setContextArchived: vi.fn(async (ref: Record<string, string>, archived: boolean) => ({ ref, display_name: 'Mock context', context_type: 'podcast', corpus_id: 'mock', workflow_profile: 'podcast', local_status: archived ? 'archived' : 'active', producer_status: 'unknown', source_root: '', catalog_path: '', source_status: {}, active_database: {}, release_inventory: [], matching_database_ids: [], capabilities: { import: false, analyze: false, archive: true }, suggested_next_action: { action: archived ? 'restore' : 'refresh_status', reason: 'Mock context' }, tracking: { state: 'source_unavailable', recommended_action: 'refresh_status', reason: 'Mock context', checked_at: '2026-09-07T00:00:00Z', active_release_id: null, latest_release: null, last_applied_release_id: null, matching_databases: [], legacy_candidates: [], suggested_target: '', profile_fingerprint: null, history: [] } })),
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
import type { ContextSummary, DatabaseRecord } from './api/types'

function pipelineContext(overrides: Partial<ContextSummary> & { tracking: NonNullable<ContextSummary['tracking']> }): ContextSummary {
  return {
    ref: { connection_id: 'connection-pipeline', partition_id: 'partition-pipeline' }, display_name: 'Pipeline partition', context_type: 'podcast', corpus_id: 'podcast', workflow_profile: 'podcast', local_status: 'active', producer_status: 'active', source_root: 'C:/Podcast RAG', catalog_path: 'C:/state/context_catalog.sqlite3', source_status: { completed: 2, declared_episodes: 2, pending: 0, failed: 0, interrupted: 0, quarantined: 0 }, active_database: { status: 'not_linked' }, release_inventory: [], matching_database_ids: [], capabilities: { import: true, analyze: false, archive: true }, suggested_next_action: { action: 'create_database', reason: 'Ready' }, ...overrides,
  }
}

describe('modern database creation flow', () => {
  afterEach(() => { cleanup(); window.history.replaceState({}, '', '/') })

  it('shows registered databases before slow source reconciliation completes', async () => {
    const database: DatabaseRecord = {
      id: 'db-immediate', display_name: 'Immediate database', source_kind: 'folder',
      source_ref: { path: 'C:/processed' }, target: { path: 'C:/exports/immediate' }, downstream_identity: null,
      selection_policy: { speaker_mode: 'all', excluded_speakers: [], allowlist_speakers: [], episode_overrides: {}, excluded_episode_ids: [], asset_filter: 'reviewed_speaker_transcript', asset_pattern: '' },
      settings_revision: 1, archived: false, last_check: null, created_at: '2026-09-07T00:00:00Z', updated_at: '2026-09-07T00:00:00Z',
    }
    let finishReconciliation!: (value: { checked_at: string; connections: []; contexts: []; source_observations: [] }) => void
    const reconciliation = new Promise<{ checked_at: string; connections: []; contexts: []; source_observations: [] }>((resolve) => { finishReconciliation = resolve })
    ;(testClient.reconcileSources as ReturnType<typeof vi.fn>).mockReturnValueOnce(reconciliation)
    ;(testClient.listDatabases as ReturnType<typeof vi.fn>).mockResolvedValueOnce([database])
    render(<App />)

    await waitFor(() => expect(screen.getByText('Immediate database')).toBeTruthy())
    expect(testClient.reconcileSources).toHaveBeenCalled()
    finishReconciliation({ checked_at: '2026-09-07T00:00:00Z', connections: [], contexts: [], source_observations: [] })
  })

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

  it('shows a ready pipeline partition in an empty library and routes Create with saved context', async () => {
    const context = {
      ref: { connection_id: 'connection-tfm', partition_id: 'partition-tfm' }, display_name: 'TFM Show', context_type: 'podcast', corpus_id: 'podcast', workflow_profile: 'podcast', local_status: 'active', producer_status: 'active', source_root: 'C:/Podcast RAG', catalog_path: 'C:/state/context_catalog.sqlite3', source_status: { ready_to_publish: true, completed: 57, declared_episodes: 57, pending: 0, failed: 0, interrupted: 0, quarantined: 0, active_release_id: 'release-tfm' }, active_database: { status: 'not_linked' }, release_inventory: [{ upstream_release_id: 'release-tfm' }], matching_database_ids: [], capabilities: { import: true, analyze: false, archive: true }, suggested_next_action: { action: 'create_database', reason: 'Ready' }, tracking: { state: 'ready_to_create', recommended_action: 'create_database', reason: 'A ready source release is available and no linked database exists.', checked_at: '2026-09-07T00:00:00Z', active_release_id: 'release-tfm', latest_release: { upstream_release_id: 'release-tfm' }, last_applied_release_id: null, matching_databases: [], legacy_candidates: [], suggested_target: 'C:/exports', profile_fingerprint: 'profile-tfm', history: [], change_counts: { episodes_total: 57, records_changed: null, requires_review: true } },
    }
    ;(testClient.reconcileSources as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ checked_at: context.tracking.checked_at, connections: [], contexts: [context], source_observations: [] })
    ;(testClient.listDatabases as ReturnType<typeof vi.fn>).mockResolvedValueOnce([])
    render(<App />)
    await waitFor(() => expect(screen.getByText('Available partitions')).toBeTruthy())
    expect(screen.getByText(/57\s+completed/)).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'No registered databases yet' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Create database for TFM Show' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Choose content' })).toBeTruthy())
    expect(screen.getByDisplayValue('C:/Podcast RAG')).toBeTruthy()
    expect(screen.getByDisplayValue('partition-tfm')).toBeTruthy()
    expect(screen.getByText('PARTITION-SCOPED CREATION')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(screen.getByDisplayValue('TFM Show')).toBeTruthy()
    expect(screen.getByDisplayValue('C:/exports/partitions/partition-tfm')).toBeTruthy()
    expect(screen.getByDisplayValue('Automatic')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    fireEvent.change(screen.getByLabelText('Podcast-RAG source root'), { target: { value: 'C:/New Podcast RAG' } })
    expect((screen.getByRole('button', { name: 'Continue' }) as HTMLButtonElement).disabled).toBe(true)
    const inspected = job('managed-scan-refresh', 'scan', { ready_to_publish: true, latest_release_id: 'release-new', catalog_path: 'C:/state/context_catalog.sqlite3', corpus_id: 'podcast' })
    jobs.unshift(inspected)
    ;(testClient.scanSource as ReturnType<typeof vi.fn>).mockResolvedValueOnce(inspected)
    fireEvent.click(screen.getByRole('button', { name: 'Inspect managed source' }))
    await waitFor(() => expect((screen.getByRole('button', { name: 'Continue' }) as HTMLButtonElement).disabled).toBe(false))
  })

  it('gives each ready partition its own populated create entry point', async () => {
    const contexts = ['one', 'two'].map((id, index) => ({
      ref: { connection_id: `connection-${id}`, partition_id: `partition-${id}` }, display_name: `Show ${id}`, context_type: 'podcast', corpus_id: `corpus-${id}`, workflow_profile: 'podcast', local_status: 'active', producer_status: 'active', source_root: 'C:/Podcast RAG', catalog_path: 'C:/state/context_catalog.sqlite3', source_status: { ready_to_publish: true, completed: index + 1, pending: 0, failed: 0, interrupted: 0, quarantined: 0, active_release_id: `release-${id}` }, active_database: { status: 'not_linked' }, release_inventory: [{ upstream_release_id: `release-${id}` }], matching_database_ids: [], capabilities: { import: true, analyze: false }, suggested_next_action: { action: 'create_database', reason: 'Ready' }, tracking: { state: 'ready_to_create', recommended_action: 'create_database', reason: 'Ready to create.', checked_at: '2026-09-07T00:00:00Z', active_release_id: `release-${id}`, latest_release: { upstream_release_id: `release-${id}` }, last_applied_release_id: null, matching_databases: [], legacy_candidates: [], suggested_target: '', profile_fingerprint: null, history: [], change_counts: { episodes_total: index + 1, records_changed: null, requires_review: true } },
    }))
    ;(testClient.reconcileSources as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ checked_at: '2026-09-07T00:00:00Z', connections: [], contexts, source_observations: [] })
    ;(testClient.listDatabases as ReturnType<typeof vi.fn>).mockResolvedValueOnce([])
    render(<App />)
    await waitFor(() => expect(screen.getByText('Available partitions')).toBeTruthy())
    expect(screen.getByRole('button', { name: 'Create database for Show one' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Create database for Show two' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Create database for Show two' }))
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(screen.getByDisplayValue('Show two')).toBeTruthy()
    expect(screen.getByDisplayValue('C:/Podcast RAG/exports/partitions/partition-two')).toBeTruthy()
  })

  it('routes one matching pipeline database to a source-pinned supplement review', async () => {
    const database: DatabaseRecord = {
      id: 'db-tfm', display_name: 'TFM database', source_kind: 'managed', source_ref: { connection_id: 'connection-tfm', source_root: 'C:/Podcast RAG', partition_id: 'partition-tfm' }, target: { path: 'C:/exports/tfm' }, downstream_identity: { profile: 'qwen3-embedding-4b-shadow' },
      selection_policy: { speaker_mode: 'all', excluded_speakers: [], allowlist_speakers: [], episode_overrides: {}, excluded_episode_ids: [], asset_filter: 'reviewed_speaker_transcript', asset_pattern: '' }, settings_revision: 1, archived: false, last_check: null, created_at: '2026-09-07T00:00:00Z', updated_at: '2026-09-07T00:00:00Z',
    }
    const context = {
      ref: { connection_id: 'connection-tfm', partition_id: 'partition-tfm' }, display_name: 'TFM Show', context_type: 'podcast', corpus_id: 'podcast', workflow_profile: 'podcast', local_status: 'active', producer_status: 'active', source_root: 'C:/Podcast RAG', catalog_path: 'C:/state/context_catalog.sqlite3', source_status: { ready_to_publish: true, completed: 57, declared_episodes: 57, pending: 0, failed: 0, interrupted: 0, quarantined: 0, active_release_id: 'release-two' }, active_database: { status: 'linked' }, release_inventory: [{ upstream_release_id: 'release-two' }], matching_database_ids: ['db-tfm'], capabilities: { import: true, analyze: false, archive: true }, suggested_next_action: { action: 'supplement_database', reason: 'New release' }, tracking: { state: 'update_available', recommended_action: 'supplement_database', reason: 'A newer ready source release is available for this linked database.', checked_at: '2026-09-07T00:00:00Z', active_release_id: 'release-two', latest_release: { upstream_release_id: 'release-two' }, last_applied_release_id: 'release-one', matching_databases: [{ database_id: 'db-tfm', display_name: 'TFM database', target: { path: 'C:/exports/tfm' } }], legacy_candidates: [], suggested_target: 'C:/exports', profile_fingerprint: 'profile-tfm', history: [], change_counts: { episodes_total: 57, records_changed: null, requires_review: true } },
    }
    ;(testClient.reconcileSources as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ checked_at: context.tracking.checked_at, connections: [], contexts: [context], source_observations: [] })
    ;(testClient.listDatabases as ReturnType<typeof vi.fn>).mockResolvedValueOnce([database])
    ;(testClient.createPreview as ReturnType<typeof vi.fn>).mockClear()
    render(<App />)
    await waitFor(() => expect(screen.getByText('TFM database')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Review supplement' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'TFM database' })).toBeTruthy())
    await waitFor(() => expect(testClient.createPreview).toHaveBeenCalledWith(expect.objectContaining({ database_id: 'db-tfm', operation: 'update', upstream_release_id: 'release-two' })))
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

  it('opens redundancy analysis as an explicit advisory advanced area', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByText('No databases yet')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Redundancy analysis/ }))
    expect(screen.getByRole('heading', { name: 'Redundancy analysis' })).toBeTruthy()
    expect(screen.getByText('Select a source context')).toBeTruthy()
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

  it('preserves database state across sidebar navigation without refreshing the pipeline', async () => {
    const database: DatabaseRecord = {
      id: 'db-navigation', display_name: 'Navigation database', source_kind: 'folder',
      source_ref: { path: 'C:/processed' }, target: { path: 'C:/exports/navigation' }, downstream_identity: { profile: 'qwen3-embedding-4b-shadow' },
      selection_policy: { speaker_mode: 'all', excluded_speakers: [], allowlist_speakers: [], episode_overrides: {}, excluded_episode_ids: [], asset_filter: 'reviewed_speaker_transcript', asset_pattern: '' },
      settings_revision: 1, archived: false, last_check: null, created_at: '2026-09-07T00:00:00Z', updated_at: '2026-09-07T00:00:00Z',
    }
    const context = {
      ref: { connection_id: 'connection-navigation', partition_id: 'partition-navigation' }, display_name: 'Navigation pipeline', context_type: 'podcast', corpus_id: 'corpus-navigation', workflow_profile: 'podcast', local_status: 'active', producer_status: 'active', source_root: 'C:/Podcast RAG', catalog_path: 'C:/state/context_catalog.sqlite3', source_status: { completed: 1, pending: 0, failed: 0, quarantined: 0 }, active_database: { status: 'linked' }, release_inventory: [], matching_database_ids: ['db-navigation'], capabilities: { import: true, analyze: false, archive: true }, suggested_next_action: { action: 'review_database', reason: 'Current' }, tracking: { state: 'current', recommended_action: 'review_database', reason: 'Current.', checked_at: '2026-09-07T00:00:00Z', active_release_id: null, latest_release: null, last_applied_release_id: null, matching_databases: [{ database_id: 'db-navigation', display_name: 'Navigation database' }], legacy_candidates: [], suggested_target: '', profile_fingerprint: null, history: [], change_counts: { episodes_total: 1, records_changed: 0, requires_review: false } },
    }
    const reconcileSources = testClient.reconcileSources as ReturnType<typeof vi.fn>
    const listDatabases = testClient.listDatabases as ReturnType<typeof vi.fn>
    const listJobs = testClient.listJobs as ReturnType<typeof vi.fn>
    reconcileSources.mockClear().mockResolvedValue({ checked_at: '2026-09-07T00:00:00Z', connections: [], contexts: [context], source_observations: [] })
    listDatabases.mockClear().mockResolvedValue([database])
    listJobs.mockClear().mockResolvedValue([])

    render(<App />)
    await waitFor(() => expect(screen.getByText('Available partitions')).toBeTruthy())
    expect(reconcileSources).toHaveBeenCalledTimes(1)
    expect(listDatabases).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: 'Navigation database' }))
    fireEvent.click(screen.getByRole('tab', { name: 'Content' }))
    expect(screen.getByRole('tab', { name: 'Content' }).getAttribute('aria-selected')).toBe('true')

    fireEvent.click(screen.getByRole('button', { name: /Source connections/ }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Source connections' })).toBeTruthy())
    expect(reconcileSources).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: /Databases/ }))
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Content' }).getAttribute('aria-selected')).toBe('true'))
    expect(reconcileSources).toHaveBeenCalledTimes(1)
    expect(listDatabases).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: /^Refresh$/ }))
    await waitFor(() => expect(reconcileSources).toHaveBeenCalledTimes(2))
  })

  it('removes an unavailable partition locally, restores it from Archived, and does not refresh the pipeline', async () => {
    const context = pipelineContext({ display_name: 'Unavailable partition', producer_status: 'unavailable', tracking: { state: 'source_unavailable', recommended_action: 'refresh_status', reason: 'The source root is unavailable.', checked_at: '2026-09-07T00:00:00Z', active_release_id: null, latest_release: null, last_applied_release_id: null, matching_databases: [], legacy_candidates: [], suggested_target: '', profile_fingerprint: null, history: [] } })
    const reconcileSources = testClient.reconcileSources as ReturnType<typeof vi.fn>
    const setContextArchived = testClient.setContextArchived as ReturnType<typeof vi.fn>
    reconcileSources.mockClear().mockResolvedValue({ checked_at: context.tracking!.checked_at, connections: [], contexts: [context], source_observations: [] })
    setContextArchived.mockClear().mockImplementation(async (_ref: Record<string, string>, archived: boolean) => ({ ...context, local_status: archived ? 'archived' : 'active' }))
    ;(testClient.listDatabases as ReturnType<typeof vi.fn>).mockClear().mockResolvedValue([])
    ;(testClient.listJobs as ReturnType<typeof vi.fn>).mockClear().mockResolvedValue([])
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<App />)
    await waitFor(() => expect(screen.getByText('Unavailable partition')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Remove partition Unavailable partition' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Remove partition Unavailable partition' })).toBeNull())
    expect(confirm).toHaveBeenCalled()
    expect(setContextArchived).toHaveBeenCalledWith(context.ref, true)
    expect(reconcileSources).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: /Source connections/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Archived' }))
    await waitFor(() => expect(screen.getByRole('button', { name: /^Unavailable partition/ })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /^Unavailable partition/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Restore partition' }))
    await waitFor(() => expect(screen.getByRole('button', { name: /Databases/ })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Databases/ }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Remove partition Unavailable partition' })).toBeTruthy())
    expect(setContextArchived).toHaveBeenLastCalledWith(context.ref, false)
    expect(reconcileSources).toHaveBeenCalledTimes(1)
    confirm.mockRestore()
  })

  it('offers create or use-existing actions for one eligible managed export', async () => {
    const context = pipelineContext({ display_name: 'Linkable partition', tracking: { state: 'legacy_adoption_available', recommended_action: 'link_existing', reason: 'A managed export needs confirmation.', checked_at: '2026-09-07T00:00:00Z', active_release_id: 'release-one', latest_release: { upstream_release_id: 'release-one' }, last_applied_release_id: null, matching_databases: [], legacy_candidates: [{ database_id: 'db-existing', status: 'adoption_available', target: { path: 'C:/exports/existing' }, reason: 'Validated identity.' }], suggested_target: 'C:/exports', profile_fingerprint: 'profile-one', history: [] } })
    const database: DatabaseRecord = { id: 'db-existing', display_name: 'Existing managed database', source_kind: 'managed', source_ref: { source_root: 'C:/Podcast RAG', partition_id: 'partition-pipeline', corpus_id: 'podcast' }, target: { path: 'C:/exports/existing' }, downstream_identity: { profile: 'qwen3-embedding-4b-shadow' }, selection_policy: { speaker_mode: 'all', excluded_speakers: [], allowlist_speakers: [], episode_overrides: {}, excluded_episode_ids: [], asset_filter: 'reviewed_speaker_transcript', asset_pattern: '' }, settings_revision: 1, archived: false, last_check: null, created_at: '2026-09-07T00:00:00Z', updated_at: '2026-09-07T00:00:00Z' }
    const reconcileSources = testClient.reconcileSources as ReturnType<typeof vi.fn>
    const adoptDatabaseLink = testClient.adoptDatabaseLink as ReturnType<typeof vi.fn>
    reconcileSources.mockClear().mockResolvedValue({ checked_at: context.tracking!.checked_at, connections: [], contexts: [context], source_observations: [] })
    adoptDatabaseLink.mockClear().mockResolvedValue({ database_id: database.id, state: 'linked', origin: 'adopted' })
    ;(testClient.getContext as ReturnType<typeof vi.fn>).mockResolvedValue(context)
    ;(testClient.listDatabases as ReturnType<typeof vi.fn>).mockClear().mockResolvedValue([database])
    ;(testClient.listJobs as ReturnType<typeof vi.fn>).mockClear().mockResolvedValue([])
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<App />)
    await waitFor(() => expect(screen.getByText('Linkable partition')).toBeTruthy())
    expect(screen.getByRole('button', { name: 'Create database for Linkable partition' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Use existing database for Linkable partition' }))
    await waitFor(() => expect(adoptDatabaseLink).toHaveBeenCalledWith({ database_id: 'db-existing', connection_id: 'connection-pipeline', partition_id: 'partition-pipeline' }))
    expect(confirm).toHaveBeenCalled()
    expect(reconcileSources).toHaveBeenCalledTimes(1)
    confirm.mockRestore()
  })

  it('does not offer update at parity, but offers it for a newer partition release', async () => {
    const current = pipelineContext({ ref: { connection_id: 'connection-current', partition_id: 'partition-current' }, display_name: 'Current partition', matching_database_ids: ['db-current'], active_database: { status: 'linked' }, tracking: { state: 'current', recommended_action: 'view_history', reason: 'The linked database already contains the active source release.', checked_at: '2026-09-07T00:00:00Z', active_release_id: 'release-one', latest_release: { upstream_release_id: 'release-one' }, last_applied_release_id: 'release-one', matching_databases: [{ database_id: 'db-current', compatible: true, archived: false, last_source_release_id: 'release-one' }], legacy_candidates: [], suggested_target: '', profile_fingerprint: null, history: [], change_counts: { episodes_total: 2, records_changed: 0, requires_review: false } } })
    const updated = pipelineContext({ ref: { connection_id: 'connection-updated', partition_id: 'partition-updated' }, display_name: 'Updated partition', matching_database_ids: ['db-updated'], active_database: { status: 'linked' }, tracking: { state: 'update_available', recommended_action: 'supplement_database', reason: 'A newer ready source release is available.', checked_at: '2026-09-07T00:00:00Z', active_release_id: 'release-two', latest_release: { upstream_release_id: 'release-two' }, last_applied_release_id: 'release-one', matching_databases: [{ database_id: 'db-updated', compatible: true, archived: false, last_source_release_id: 'release-one' }], legacy_candidates: [], suggested_target: '', profile_fingerprint: null, history: [], change_counts: { episodes_total: 2, records_changed: null, requires_review: true } } })
    const database = (id: string, name: string): DatabaseRecord => ({ id, display_name: name, source_kind: 'managed', source_ref: {}, target: { path: `C:/exports/${id}` }, downstream_identity: null, selection_policy: { speaker_mode: 'all', excluded_speakers: [], allowlist_speakers: [], episode_overrides: {}, excluded_episode_ids: [], asset_filter: 'reviewed_speaker_transcript', asset_pattern: '' }, settings_revision: 1, archived: false, last_check: null, created_at: '2026-09-07T00:00:00Z', updated_at: '2026-09-07T00:00:00Z' })
    const reconcileSources = testClient.reconcileSources as ReturnType<typeof vi.fn>
    reconcileSources.mockClear().mockResolvedValue({ checked_at: '2026-09-07T00:00:00Z', connections: [], contexts: [current, updated], source_observations: [] })
    ;(testClient.listDatabases as ReturnType<typeof vi.fn>).mockClear().mockResolvedValue([database('db-current', 'Current database'), database('db-updated', 'Updated database')])
    ;(testClient.listJobs as ReturnType<typeof vi.fn>).mockClear().mockResolvedValue([])

    render(<App />)
    await waitFor(() => expect(screen.getByText('Current partition')).toBeTruthy())
    expect(screen.getByRole('button', { name: 'Review database' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Review supplement' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Update database/ })).toBeNull()
  })

  it('shows restore and separate-create actions for an archived matching database, never update', async () => {
    const context = pipelineContext({ display_name: 'Archived match', matching_database_ids: ['db-archived'], tracking: { state: 'ambiguous_match', recommended_action: 'restore_database', reason: 'The matching database is archived; restore it or create a separate database.', checked_at: '2026-09-07T00:00:00Z', active_release_id: 'release-two', latest_release: { upstream_release_id: 'release-two' }, last_applied_release_id: 'release-one', matching_databases: [{ database_id: 'db-archived', compatible: true, archived: true, last_source_release_id: 'release-one' }], legacy_candidates: [], suggested_target: '', profile_fingerprint: null, history: [], change_counts: { episodes_total: 2, records_changed: null, requires_review: false } } })
    const database: DatabaseRecord = { id: 'db-archived', display_name: 'Archived database', source_kind: 'managed', source_ref: {}, target: { path: 'C:/exports/archived' }, downstream_identity: null, selection_policy: { speaker_mode: 'all', excluded_speakers: [], allowlist_speakers: [], episode_overrides: {}, excluded_episode_ids: [], asset_filter: 'reviewed_speaker_transcript', asset_pattern: '' }, settings_revision: 1, archived: true, last_check: null, created_at: '2026-09-07T00:00:00Z', updated_at: '2026-09-07T00:00:00Z' }
    const reconcileSources = testClient.reconcileSources as ReturnType<typeof vi.fn>
    reconcileSources.mockClear().mockResolvedValue({ checked_at: '2026-09-07T00:00:00Z', connections: [], contexts: [context], source_observations: [] })
    ;(testClient.listDatabases as ReturnType<typeof vi.fn>).mockClear().mockResolvedValue([database])
    ;(testClient.listJobs as ReturnType<typeof vi.fn>).mockClear().mockResolvedValue([])

    render(<App />)
    await waitFor(() => expect(screen.getByText('Archived match')).toBeTruthy())
    expect(screen.getByRole('button', { name: 'Restore database' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Create separate database for Archived match' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Review supplement|Update database/ })).toBeNull()
  })

  it('labels multiple linked databases as an update-destination choice', async () => {
    const context = pipelineContext({ display_name: 'Ambiguous match', matching_database_ids: ['db-one', 'db-two'], tracking: { state: 'ambiguous_match', recommended_action: 'select_database', reason: 'Multiple databases are linked.', checked_at: '2026-09-07T00:00:00Z', active_release_id: 'release-two', latest_release: { upstream_release_id: 'release-two' }, last_applied_release_id: null, matching_databases: [{ database_id: 'db-one' }, { database_id: 'db-two' }], legacy_candidates: [], suggested_target: '', profile_fingerprint: null, history: [], change_counts: { episodes_total: 2, records_changed: null, requires_review: false } } })
    const reconcileSources = testClient.reconcileSources as ReturnType<typeof vi.fn>
    reconcileSources.mockClear().mockResolvedValue({ checked_at: context.tracking!.checked_at, connections: [], contexts: [context], source_observations: [] })
    ;(testClient.listDatabases as ReturnType<typeof vi.fn>).mockClear().mockResolvedValue([])
    render(<App />)
    await waitFor(() => expect(screen.getByText('Ambiguous match')).toBeTruthy())
    expect(screen.getByRole('button', { name: 'Choose update database' })).toBeTruthy()
  })
})
