import type { BridgeError, DatabaseRecord, Envelope, JobRecord, Preview } from './types'

export class ClientError extends Error {
  constructor(public readonly detail: BridgeError) { super(detail.message) }
}

export interface AppClient {
  handshake(): Promise<Record<string, string>>
  echo(payload: Record<string, unknown>): Promise<Record<string, unknown>>
  syntheticProgress(seconds?: number): Promise<JobRecord>
  listDatabases(): Promise<DatabaseRecord[]>
  getDatabase(id: string): Promise<DatabaseRecord>
  getDatabaseHistory(id: string): Promise<Record<string, unknown>>
  getDatabaseContent(id: string): Promise<Record<string, unknown>>
  environmentReport(): Promise<Record<string, unknown>>
  migrationCandidates(): Promise<Record<string, unknown>>
  listJobs(): Promise<JobRecord[]>
  getJob(id: string): Promise<JobRecord>
  retryJob(id: string): Promise<JobRecord>
  cancelJob(id: string): Promise<JobRecord>
  getJobEvents(id: string, after?: number): Promise<Array<Record<string, unknown>>>
  pickFolder(): Promise<string | null>
  scanSource(payload: Record<string, unknown>): Promise<JobRecord>
  inspectExisting(payload: Record<string, unknown>): Promise<Record<string, unknown>>
  registerExisting(payload: Record<string, unknown>): Promise<DatabaseRecord>
  saveDraft(payload: Record<string, unknown>, draftId?: string): Promise<Record<string, unknown>>
  getDraft(id: string): Promise<Record<string, unknown>>
  createPreview(payload: Record<string, unknown>): Promise<JobRecord>
  createMaintenancePreview(databaseId: string, deleteIds: string[]): Promise<Preview>
  checkDatabase(id: string): Promise<JobRecord>
  getPreview(id: string): Promise<Preview>
  applyPreview(id: string, acknowledgments?: string[]): Promise<JobRecord>
  archiveDatabase(id: string): Promise<DatabaseRecord>
  renameDatabase(id: string, displayName: string): Promise<DatabaseRecord>
  updateDatabaseSettings(id: string, changes: Record<string, unknown>): Promise<DatabaseRecord>
  startSourceAction(payload: Record<string, unknown>): Promise<JobRecord>
  openDatabaseFolder(id: string): Promise<string>
  exportReport(report: Record<string, unknown>, reportId?: string): Promise<Record<string, unknown>>
}

type BridgeApi = Record<string, (payload?: unknown) => Promise<Envelope<unknown>>>

const BRIDGE_TIMEOUT_MS = 5000
let bridgeReadyPromise: Promise<BridgeApi> | null = null

function getReadyBridge(): BridgeApi | null {
  const api = (window as Window & { pywebview?: { api?: BridgeApi } }).pywebview?.api
  return api && typeof api.handshake === 'function' ? api : null
}

function bridgeUnavailable(): ClientError {
  return new ClientError({ code: 'BRIDGE_UNAVAILABLE', message: 'The desktop connection is unavailable. Start the packaged desktop application.', field: null, details_id: null })
}

function getBridge(): Promise<BridgeApi> {
  const ready = getReadyBridge()
  if (ready) return Promise.resolve(ready)
  if (bridgeReadyPromise) return bridgeReadyPromise

  const readyPromise = new Promise<BridgeApi>((resolve, reject) => {
    let settled = false
    const finish = (bridge: BridgeApi | null, error?: Error) => {
      if (settled) return
      settled = true
      window.clearTimeout(timeout)
      window.removeEventListener('pywebviewready', onReady)
      if (bridge) resolve(bridge)
      else reject(error ?? bridgeUnavailable())
    }
    const onReady = () => finish(getReadyBridge(), bridgeUnavailable())
    const timeout = window.setTimeout(() => finish(null, bridgeUnavailable()), BRIDGE_TIMEOUT_MS)

    // pywebview injects its API asynchronously and announces completion with
    // this event. The initial check handles pages where injection already won
    // the race with React startup.
    window.addEventListener('pywebviewready', onReady, { once: true })
    const afterListener = getReadyBridge()
    if (afterListener) finish(afterListener)
  })
  // Do not leave a rejected promise cached forever. This also keeps a later
  // call able to recover if the host injects the bridge after the timeout.
  bridgeReadyPromise = readyPromise.catch((error: unknown) => {
    bridgeReadyPromise = null
    throw error
  })
  return bridgeReadyPromise
}

async function call<T>(method: string, payload: unknown = {}): Promise<T> {
  const bridge = await getBridge()
  const fn = bridge[method]
  if (typeof fn !== 'function') throw bridgeUnavailable()
  const response = await fn(payload)
  if (!response?.ok) throw new ClientError(response?.error ?? { code: 'JOB_FAILED', message: 'The desktop connection returned an invalid response.', field: null, details_id: null })
  return response.data as T
}

export const client: AppClient = {
  handshake: () => call('handshake'),
  echo: (payload) => call('echo', payload),
  syntheticProgress: (seconds = 10) => call('synthetic_progress', { seconds }),
  listDatabases: () => call('list_databases'),
  getDatabase: (id) => call('get_database', { database_id: id }),
  getDatabaseHistory: (id) => call('get_database_history', { database_id: id }),
  getDatabaseContent: (id) => call('get_database_content', { database_id: id }),
  environmentReport: () => call('environment_report'),
  migrationCandidates: () => call('migration_candidates'),
  listJobs: () => call('list_jobs'),
  getJob: (id) => call('get_job', { job_id: id }),
  retryJob: (id) => call('retry_job', { job_id: id }),
  cancelJob: (id) => call('cancel_job', { job_id: id }),
  getJobEvents: (id, after = 0) => call('get_job_events', { job_id: id, after }),
  pickFolder: () => call('pick_folder'),
  scanSource: (payload) => call('scan_source', payload),
  inspectExisting: (payload) => call('inspect_existing', payload),
  registerExisting: (payload) => call('register_existing', payload),
  saveDraft: (payload, draftId) => call('save_draft', { payload, draft_id: draftId }),
  getDraft: (id) => call('get_draft', { draft_id: id }),
  createPreview: (payload) => call('create_preview', payload),
  createMaintenancePreview: (databaseId, deleteIds) => call('create_maintenance_preview', { database_id: databaseId, delete_ids: deleteIds }),
  checkDatabase: (id) => call('check_database', { database_id: id }),
  getPreview: (id) => call('get_preview', { preview_id: id }),
  applyPreview: (id, acknowledgments = []) => call('apply_preview', { preview_id: id, acknowledgments }),
  archiveDatabase: (id) => call('archive_database', { database_id: id }),
  renameDatabase: (id, displayName) => call('rename_database', { database_id: id, display_name: displayName }),
  updateDatabaseSettings: (id, changes) => call('update_database_settings', { database_id: id, changes }),
  startSourceAction: (payload) => call('start_source_action', payload),
  openDatabaseFolder: (id) => call('open_database_folder', { database_id: id }),
  exportReport: (report, reportId) => call('export_report', { report, report_id: reportId }),
}
