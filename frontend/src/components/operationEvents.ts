export type OperationEvent = {
  status: 'start' | 'finish' | 'error' | 'job'
  operationId?: string
  method: string
  label: string
  data?: unknown
  error?: string
}

export const OPERATION_EVENT = 'chroma-ui-operation'

// Bridge calls that can scan, inspect, analyze, write, or wait on external
// work. Lightweight list/poll calls intentionally remain quiet.
export const TRACKED_METHODS = new Set([
  'synthetic_progress', 'get_database', 'get_database_history', 'get_database_content', 'get_database_details',
  'inspect_content', 'validate_database', 'environment_report', 'preview_environment_repair', 'apply_environment_repair',
  'migration_candidates', 'retry_job', 'cancel_job', 'scan_source', 'inspect_existing', 'register_existing',
  'save_draft', 'get_draft', 'create_preview', 'create_maintenance_preview', 'check_database', 'apply_preview',
  'archive_database', 'restore_database', 'rename_database', 'update_database_settings', 'start_source_action',
  'open_database_folder', 'export_report', 'save_report_copy', 'save_app_defaults', 'export_settings',
  'save_settings_transfer', 'preview_settings_import', 'apply_settings_import', 'reconcile_sources',
  'adopt_database_link', 'link_source', 'discover_contexts', 'get_context', 'set_context_archived',
  'save_context_import_defaults', 'save_context_defaults', 'preview_context_dedup', 'review_context_dedup',
  'save_context_dedup_policy', 'open_context_folder', 'get_redundancy_settings', 'save_redundancy_policy',
  'start_redundancy_action', 'save_judge_config', 'probe_judge', 'review_judge_pilot', 'open_redundancy_bundle',
  'inspect_folder',
])

const LABELS: Record<string, string> = {
  synthetic_progress: 'Checking desktop responsiveness',
  reconcile_sources: 'Refreshing pipeline and database status',
  scan_source: 'Scanning the selected source',
  inspect_content: 'Inspecting content',
  validate_database: 'Validating the database',
  create_preview: 'Preparing the review',
  apply_preview: 'Applying the reviewed database operation',
  start_source_action: 'Processing the source action',
  start_redundancy_action: 'Running redundancy analysis',
  probe_judge: 'Testing the local semantic judge',
  review_judge_pilot: 'Freezing the judge pilot review',
  save_judge_config: 'Saving judge connection settings',
  save_redundancy_policy: 'Saving analysis policy',
  environment_report: 'Checking the local environment',
  preview_environment_repair: 'Preparing the environment repair review',
  apply_environment_repair: 'Applying the environment repair',
  open_redundancy_bundle: 'Validating the redundancy bundle',
}

export function operationLabel(method: string): string {
  return LABELS[method] ?? method.replaceAll('_', ' ').replace(/^./, (value) => value.toUpperCase())
}

export function emitOperation(event: OperationEvent): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent<OperationEvent>(OPERATION_EVENT, { detail: event }))
}
