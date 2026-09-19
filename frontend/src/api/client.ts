import type { BridgeError, ContentInventory, ContextSummary, DatabaseRecord, Envelope, Handshake, JobRecord, Preview, Report, SourceConnection } from './types'
import { emitOperation, operationLabel, TRACKED_METHODS } from '../components/operationEvents'

export class ClientError extends Error {
  constructor(public readonly detail: BridgeError) { super(detail.message) }
}

export interface AppClient {
  handshake(): Promise<Handshake>
  echo(payload: Record<string, unknown>): Promise<Record<string, unknown>>
  syntheticProgress(seconds?: number): Promise<JobRecord>
  listDatabases(includeArchived?: boolean): Promise<DatabaseRecord[]>
  getDatabase(id: string): Promise<DatabaseRecord>
  getDatabaseHistory(id: string): Promise<Record<string, unknown>>
  getDatabaseContent(id: string, options?: { selection_policy?: Record<string, unknown>; offset?: number; limit?: number; search?: string }): Promise<ContentInventory>
  getDatabaseDetails(id: string): Promise<Report>
  inspectContent(payload: Record<string, unknown>): Promise<JobRecord>
  validateDatabase(id: string, scope?: 'source' | 'database' | 'both'): Promise<JobRecord>
  environmentReport(): Promise<Record<string, unknown>>
  previewEnvironmentRepair(): Promise<Record<string, unknown>>
  applyEnvironmentRepair(reviewId: string): Promise<JobRecord>
  migrationCandidates(): Promise<Record<string, unknown>>
  listJobs(): Promise<JobRecord[]>
  getJob(id: string): Promise<JobRecord>
  retryJob(id: string): Promise<JobRecord>
  cancelJob(id: string): Promise<JobRecord>
  getJobEvents(id: string, after?: number): Promise<Array<Record<string, unknown>>>
  pickFolder(): Promise<string | null>
  pickFile(purpose: string): Promise<string | null>
  pickSaveFile(purpose: string): Promise<string | null>
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
  restoreDatabase(id: string): Promise<DatabaseRecord>
  renameDatabase(id: string, displayName: string): Promise<DatabaseRecord>
  updateDatabaseSettings(id: string, changes: Record<string, unknown>): Promise<DatabaseRecord>
  startSourceAction(payload: Record<string, unknown>): Promise<JobRecord>
  openDatabaseFolder(id: string): Promise<string>
  exportReport(report: Report | Record<string, unknown>, reportId?: string): Promise<Record<string, unknown>>
  saveReportCopy(reportId: string, outputPath: string): Promise<Record<string, unknown>>
  getAppDefaults(): Promise<Record<string, unknown>>
  saveAppDefaults(changes: Record<string, unknown>, baseRevision?: number): Promise<Record<string, unknown>>
  exportSettings(scopeKind: string, reference?: Record<string, unknown>, includeRedundancy?: boolean): Promise<Record<string, unknown>>
  saveSettingsTransfer(transfer: Record<string, unknown>, outputPath: string): Promise<Record<string, unknown>>
  previewSettingsImport(path: string, scopeKind: string, reference?: Record<string, unknown>): Promise<Record<string, unknown>>
  applySettingsImport(path: string, proposal: Record<string, unknown>, selectedFields: string[], scopeKind: string, reference?: Record<string, unknown>): Promise<Record<string, unknown>>
  listSourceConnections(includeArchived?: boolean): Promise<SourceConnection[]>
  reconcileSources(): Promise<{ checked_at: string; connections: Array<Record<string, unknown>>; contexts: ContextSummary[]; source_observations: Array<Record<string, unknown>> }>
  adoptDatabaseLink(payload: Record<string, unknown>): Promise<Record<string, unknown>>
  selectDatabaseLink(payload: Record<string, unknown>): Promise<Record<string, unknown>>
  linkSource(sourceRoot: string): Promise<Record<string, unknown>>
  discoverContexts(connectionId: string): Promise<Record<string, unknown>>
  listContexts(includeArchived?: boolean): Promise<ContextSummary[]>
  getContext(context: { connection_id: string; partition_id: string }): Promise<ContextSummary & Record<string, unknown>>
  setContextArchived(context: { connection_id: string; partition_id: string }, archived: boolean): Promise<ContextSummary & Record<string, unknown>>
  saveContextImportDefaults(context: { connection_id: string; partition_id: string }, changes: Record<string, unknown>, baseRevision?: number): Promise<Record<string, unknown>>
  saveContextDefaults(context: { connection_id: string; partition_id: string }, baseProfileFingerprint: string | undefined, profileChanges: Record<string, unknown>, executionOptions?: Record<string, unknown>): Promise<Record<string, unknown>>
  previewContextDedup(context: { connection_id: string; partition_id: string }, upstreamReleaseId?: string): Promise<Report>
  reviewContextDedup(context: { connection_id: string; partition_id: string }, upstreamReleaseId?: string): Promise<Report>
  getContextDedupSettings(context: { connection_id: string; partition_id: string }): Promise<Record<string, unknown>>
  saveContextDedupPolicy(context: { connection_id: string; partition_id: string }, changes: Record<string, unknown>, baseProfileFingerprint?: string): Promise<Record<string, unknown>>
  openContextFolder(context: { connection_id: string; partition_id: string }): Promise<string>
  repairPartitionLock(partitionRoot: string, confirm?: boolean, graceTimeout?: number): Promise<Record<string, unknown>>
  getRedundancySettings(context: { connection_id: string; partition_id: string }): Promise<Record<string, unknown>>
  saveRedundancyPolicy(context: { connection_id: string; partition_id: string }, changes: Record<string, unknown>, baseFingerprint?: string): Promise<Record<string, unknown>>
  startRedundancyAction(payload: Record<string, unknown>): Promise<JobRecord>
  saveJudgeConfig(context: { connection_id: string; partition_id: string }, changes: Record<string, unknown>, baseFingerprint?: string): Promise<Record<string, unknown>>
  reviewJudgePilot(context: { connection_id: string; partition_id: string }, upstreamReleaseId: string, channels: string[]): Promise<Record<string, unknown>>
  probeJudge(context: { connection_id: string; partition_id: string }, changes?: Record<string, unknown>): Promise<Record<string, unknown>>
  openRedundancyBundle(path: string, context?: { connection_id: string; partition_id: string }): Promise<Record<string, unknown>>
  getSourceSuggestions(currentPath?: string): Promise<Array<{ label: string; path: string }>>
  listSourceFolders(root: string, relativePath?: string): Promise<Array<Record<string, unknown>>>
  inspectFolder(payload: { path: string; asset_filter: string; asset_pattern?: string }): Promise<Record<string, unknown>>
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
  const tracked = TRACKED_METHODS.has(method)
  const operationId = tracked ? `${method}-${Date.now()}-${Math.random().toString(36).slice(2)}` : undefined
  if (tracked) emitOperation({ status: 'start', method, label: operationLabel(method), operationId })
  try {
    const bridge = await getBridge()
    const fn = bridge[method]
    if (typeof fn !== 'function') throw bridgeUnavailable()
    const response = await fn(payload)
    if (!response?.ok) throw new ClientError(response?.error ?? { code: 'JOB_FAILED', message: 'The desktop connection returned an invalid response.', field: null, details_id: null })
    if (method === 'get_job') emitOperation({ status: 'job', method, label: operationLabel(method), data: response.data })
    else if (tracked) emitOperation({ status: 'finish', method, label: operationLabel(method), operationId, data: response.data })
    return response.data as T
  } catch (error) {
    if (tracked) emitOperation({ status: 'error', method, label: operationLabel(method), operationId, error: error instanceof Error ? error.message : 'The operation could not be completed.' })
    throw error
  }
}

export const client: AppClient = {
  handshake: () => call('handshake'),
  echo: (payload) => call('echo', payload),
  syntheticProgress: (seconds = 10) => call('synthetic_progress', { seconds }),
  listDatabases: (includeArchived = false) => call('list_databases', { include_archived: includeArchived }),
  getDatabase: (id) => call('get_database', { database_id: id }),
  getDatabaseHistory: (id) => call('get_database_history', { database_id: id }),
  getDatabaseContent: (id, options = {}) => call('get_database_content', { database_id: id, ...options }),
  getDatabaseDetails: (id) => call('get_database_details', { database_id: id }),
  inspectContent: (payload) => call('inspect_content', payload),
  validateDatabase: (id, scope = 'both') => call('validate_database', { database_id: id, scope }),
  environmentReport: () => call('environment_report'),
  previewEnvironmentRepair: () => call('preview_environment_repair'),
  applyEnvironmentRepair: (reviewId) => call('apply_environment_repair', { review_id: reviewId }),
  migrationCandidates: () => call('migration_candidates'),
  listJobs: () => call('list_jobs'),
  getJob: (id) => call('get_job', { job_id: id }),
  retryJob: (id) => call('retry_job', { job_id: id }),
  cancelJob: (id) => call('cancel_job', { job_id: id }),
  getJobEvents: (id, after = 0) => call('get_job_events', { job_id: id, after }),
  pickFolder: () => call('pick_folder'),
  pickFile: (purpose) => call('pick_file', { purpose }),
  pickSaveFile: (purpose) => call('pick_save_file', { purpose }),
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
  restoreDatabase: (id) => call('restore_database', { database_id: id }),
  renameDatabase: (id, displayName) => call('rename_database', { database_id: id, display_name: displayName }),
  updateDatabaseSettings: (id, changes) => call('update_database_settings', { database_id: id, changes }),
  startSourceAction: (payload) => call('start_source_action', payload),
  openDatabaseFolder: (id) => call('open_database_folder', { database_id: id }),
  exportReport: (report, reportId) => call('export_report', { report, report_id: reportId }),
  saveReportCopy: (reportId, outputPath) => call('save_report_copy', { report_id: reportId, output_path: outputPath }),
  getAppDefaults: () => call('get_app_defaults'),
  saveAppDefaults: (changes, baseRevision) => call('save_app_defaults', { changes, base_revision: baseRevision }),
  exportSettings: (scopeKind, reference, includeRedundancy = false) => call('export_settings', { scope_kind: scopeKind, reference, include_redundancy: includeRedundancy }),
  saveSettingsTransfer: (transfer, outputPath) => call('save_settings_transfer', { transfer, output_path: outputPath }),
  previewSettingsImport: (path, scopeKind, reference) => call('preview_settings_import', { path, scope_kind: scopeKind, reference }),
  applySettingsImport: (path, proposal, selectedFields, scopeKind, reference) => call('apply_settings_import', { path, proposal, selected_fields: selectedFields, scope_kind: scopeKind, reference }),
  listSourceConnections: (includeArchived = false) => call('list_source_connections', { include_archived: includeArchived }),
  reconcileSources: () => call('reconcile_sources'),
  adoptDatabaseLink: (payload) => call('adopt_database_link', payload),
  selectDatabaseLink: (payload) => call('select_database_link', payload),
  linkSource: (sourceRoot) => call('link_source', { source_root: sourceRoot }),
  discoverContexts: (connectionId) => call('discover_contexts', { connection_id: connectionId }),
  listContexts: (includeArchived = false) => call('list_contexts', { include_archived: includeArchived }),
  getContext: (context) => call('get_context', { context }),
  setContextArchived: (context, archived) => call('set_context_archived', { context, archived }),
  saveContextImportDefaults: (context, changes, baseRevision) => call('save_context_import_defaults', { context, changes, base_revision: baseRevision }),
  saveContextDefaults: (context, baseProfileFingerprint, profileChanges, executionOptions) => call('save_context_defaults', { context, base_profile_fingerprint: baseProfileFingerprint, profile_changes: profileChanges, execution_options: executionOptions }),
  previewContextDedup: (context, upstreamReleaseId) => call('preview_context_dedup', { context, upstream_release_id: upstreamReleaseId }),
  reviewContextDedup: (context, upstreamReleaseId) => call('review_context_dedup', { context, upstream_release_id: upstreamReleaseId }),
  getContextDedupSettings: (context) => call('get_context_dedup_settings', { context }),
  saveContextDedupPolicy: (context, changes, baseProfileFingerprint) => call('save_context_dedup_policy', { context, changes, base_profile_fingerprint: baseProfileFingerprint }),
  openContextFolder: (context) => call('open_context_folder', { context }),
  repairPartitionLock: (partitionRoot, confirm = false, graceTimeout = 10) => call('repair_partition_lock', { partition_root: partitionRoot, confirm, grace_timeout: graceTimeout }),
  getRedundancySettings: (context) => call('get_redundancy_settings', { context }),
  saveRedundancyPolicy: (context, changes, baseFingerprint) => call('save_redundancy_policy', { context, changes, base_fingerprint: baseFingerprint }),
  startRedundancyAction: (payload) => call('start_redundancy_action', payload),
  saveJudgeConfig: (context, changes, baseFingerprint) => call('save_judge_config', { context, changes, base_fingerprint: baseFingerprint }),
  reviewJudgePilot: (context, upstreamReleaseId, channels) => call('review_judge_pilot', { context, upstream_release_id: upstreamReleaseId, channels }),
  probeJudge: (context, changes = {}) => call('probe_judge', { context, changes }),
  openRedundancyBundle: (path, context) => call('open_redundancy_bundle', { path, context }),
  getSourceSuggestions: (currentPath) => call('get_source_suggestions', { current_path: currentPath }),
  listSourceFolders: (root, relativePath) => call('list_source_folders', { root, relative_path: relativePath }),
  inspectFolder: (payload) => call('inspect_folder', payload),
}
