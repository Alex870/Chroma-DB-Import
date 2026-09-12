import type { AppClient } from './client'
import type { DatabaseRecord, JobRecord, Preview, SelectionPolicy } from './types'

const policy: SelectionPolicy = { speaker_mode: 'all', excluded_speakers: [], allowlist_speakers: [], episode_overrides: {}, excluded_episode_ids: [], asset_filter: 'reviewed', asset_pattern: '' }
const now = new Date().toISOString()
const database: DatabaseRecord = { id: 'mock-db', display_name: 'Example library', source_kind: 'folder', source_ref: { path: 'C:/processed' }, target: { path: 'C:/exports/example' }, downstream_identity: { collection_name: 'whisper_rag_v2', profile: 'qwen3-embedding-4b-shadow' }, selection_policy: policy, settings_revision: 1, archived: false, last_check: null, created_at: now, updated_at: now }
const jobs: JobRecord[] = []
let latestPreview: Preview | null = null
const makeJob = (kind: string): JobRecord => ({ id: `mock-${Date.now()}`, kind, state: 'succeeded', stage: 'complete', database_id: null, preview_id: null, can_cancel: false, result: { episodes: 2, eligible_records: 24 }, error: null, created_at: now, started_at: now, completed_at: now })

export const mockClient: AppClient = {
  async handshake() { return { api_version: 'gui-api-v1', backend_version: 'mock' } },
  async echo(payload) { return payload },
  async syntheticProgress() { const job = makeJob('diagnostic'); jobs.unshift(job); return job },
  async listDatabases() { return [database] },
  async getDatabase() { return database },
  async getDatabaseHistory() { return { database_id: database.id, target: database.target.path, active_state: 'active', jobs: [] } },
  async getDatabaseContent() { return { database_id: database.id, target: database.target.path, speakers: ['Host', 'Guest'], shared_context_note: 'Shared context remains available when at least one selected speaker is included.', episodes: [{ episode_id: 'episode-1', title: 'Example episode', date: '2026-01-01', document_count: 24, speakers: ['Host', 'Guest'], excluded: false, override_speakers: [] }] } },
  async environmentReport() { return { assets_ready: true, pywebview: 'mock', cuda: { available: false } } },
  async migrationCandidates() { return { candidates: [], originals_unchanged: true } },
  async listJobs() { return jobs },
  async getJob(id) { return jobs.find((job) => job.id === id) ?? jobs[0] },
  async retryJob(id) { const current = await this.getJob(id); const job = { ...current, id: `${id}-retry`, state: 'queued', stage: 'checking' } as JobRecord; jobs.unshift(job); return job },
  async cancelJob(id) { const job = await this.getJob(id); job.state = 'cancelled'; return job },
  async getJobEvents() { return [] },
  async pickFolder() { return 'C:/processed' },
  async scanSource() { const job = makeJob('scan'); job.result = { episodes: 1, eligible_records: 2, excluded_files: [], date_range: { start: '2026-01-01', end: '2026-01-01' }, speakers: ['Host'], episode_inventory: [{ episode_id: 'episode-1', title: 'Example episode', date: '2026-01-01', document_count: 2, speakers: ['Host'] }] }; jobs.unshift(job); return job },
  async inspectExisting() { return { display_name: 'Existing library', target: { path: 'C:/exports/existing' }, identity_status: 'resolved', downstream_identity: { collection_name: 'whisper_rag_v2' } } },
  async registerExisting() { return database },
  async saveDraft(payload, draftId) { return { id: draftId ?? 'mock-draft', revision: 1, payload } },
  async getDraft() { return { id: 'mock-draft', revision: 1, payload: {} } },
  async createPreview(payload) { latestPreview = { preview_id: 'mock-preview', operation: (payload.operation as Preview['operation']) ?? 'create', database_id: null, status: 'ready', source_snapshot: {}, target_identity: { path: 'C:/exports/example' }, selection_policy: policy, representation: { profile: 'qwen3-embedding-4b-shadow' }, validation_findings: [], required_acknowledgments: [], effects: { episodes_total: 2, records_total: 24, writes: 24, insert_ids: { total: 24, items: [] }, replace_ids: { total: 0, items: [] }, metadata_only_ids: { total: 0, items: [] }, unchanged_ids: { total: 0, items: [] }, retained_missing_ids: { total: 0, items: [] }, delete_ids: { total: 0, items: [] }, episode_changes: [], delete_episodes: [], reasons: {} } }; const job = makeJob('preview'); job.result = { preview_id: latestPreview.preview_id }; jobs.unshift(job); return job },
  async createMaintenancePreview(databaseId, deleteIds) { const base = latestPreview ?? await this.getPreview('mock-preview'); latestPreview = { ...base, database_id: databaseId, operation: 'remove_outdated', required_acknowledgments: ['REMOVE_OUTDATED_RECORDS'], effects: { ...base.effects, writes: 0, delete_ids: { total: deleteIds.length, items: deleteIds } } }; return latestPreview },
  async checkDatabase() { return this.scanSource({}) },
  async getPreview() { if (latestPreview) return latestPreview; const preview: Preview = { preview_id: 'mock-preview', operation: 'update', database_id: database.id, status: 'ready', source_snapshot: {}, target_identity: { path: database.target.path }, selection_policy: policy, representation: { profile: 'qwen3-embedding-4b-shadow' }, validation_findings: [], required_acknowledgments: [], effects: { episodes_total: 2, records_total: 24, writes: 24, insert_ids: { total: 24, items: [] }, replace_ids: { total: 0, items: [] }, metadata_only_ids: { total: 0, items: [] }, unchanged_ids: { total: 0, items: [] }, retained_missing_ids: { total: 0, items: [] }, delete_ids: { total: 0, items: [] }, episode_changes: [], delete_episodes: [], reasons: {} } }; latestPreview = preview; return preview },
  async applyPreview() { return this.scanSource({}) },
  async archiveDatabase() { database.archived = true; return database },
  async renameDatabase(_id, name) { database.display_name = name; return database },
  async updateDatabaseSettings(_id, changes) { Object.assign(database, changes); return database },
  async startSourceAction() { return this.scanSource({}) },
  async openDatabaseFolder() { return database.target.path },
  async exportReport(report, reportId) { return { report_id: reportId ?? 'mock-report', path: 'C:/state/reports/mock-report.json', report } },
}
