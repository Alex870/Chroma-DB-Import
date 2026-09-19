export type SourceKind = 'folder' | 'managed'
export type JobState = 'queued' | 'running' | 'succeeded' | 'succeeded_with_warnings' | 'failed' | 'interrupted' | 'cancelled'

export interface ContextRef { connection_id: string; partition_id: string }
export interface ExecutionOptions { embedding_device: string }
export interface ReportFinding { severity: 'info' | 'warning' | 'error'; code: string; message: string; field?: string; path?: string }
export interface Report {
  schema_version: 'gui-report-v1'
  report_id: string
  kind: string
  scope: { database_id?: string; context?: ContextRef; release_id?: string }
  generated_at: string
  status: 'pass' | 'warnings' | 'partial' | 'failed' | 'unavailable'
  summary: Record<string, string | number | boolean | null>
  findings: ReportFinding[]
  details: Record<string, unknown>
}
export interface EpisodeInspection {
  episode_id: string; title: string; date: string | null; source_file: string | null
  source_document_count: number | null; stored_document_count: number | null; included_document_count: number | null
  node_counts: { leaf_chunk: number | null; position_card: number | null; cluster_summary: number | null; episode_thesis: number | null }
  speakers: string[]; override_mode: 'inherit' | 'custom'; override_speakers: string[]
  comparison: 'new' | 'changed' | 'imported' | 'not_checked' | 'missing_source'; checked_at: string | null
}
export interface ContentInventory { database_id: string; episodes: EpisodeInspection[]; speakers: string[]; total: number; offset: number; limit: number; report: Report; policy_fingerprint: string; source_available: boolean; stored_available: boolean }
export interface SourceConnection { id: string; root: string; catalog_path: string; created_at: string; updated_at: string; archived: boolean }
export interface ManagedCreationDefaults {
  source_kind: 'managed'
  source_ref: { connection_id: string; source_root: string; partition_id: string; corpus_id: string; catalog_path: string; upstream_release_id: string }
  display_name: string
  target: { path: string; managed_output_root: string }
  selection_policy: SelectionPolicy
  execution_options: ExecutionOptions
  representation: Record<string, unknown>
  provenance: Record<string, string>
}
export interface ContextSummary { ref: ContextRef; display_name: string; context_type: string; corpus_id: string; workflow_profile: string; local_status: string; producer_status: string; source_root: string; catalog_path: string; source_status: Record<string, unknown>; active_database: Record<string, unknown>; release_inventory: Array<Record<string, unknown>>; matching_database_ids: string[]; profile_fingerprint?: string | null; capabilities: Record<string, boolean>; suggested_next_action: { action: string; reason: string }; creation_defaults?: ManagedCreationDefaults; tracking?: TrackingSummary }
export interface TrackingSummary { state: string; recommended_action: string; reason: string; checked_at: string; active_release_id: string | null; latest_release: Record<string, unknown> | null; last_applied_release_id: string | null; matching_databases: Array<Record<string, unknown>>; legacy_candidates: Array<Record<string, unknown>>; suggested_target: string; profile_fingerprint: string | null; history: Array<Record<string, unknown>>; change_counts?: { episodes_total: number; records_changed: number | null; requires_review: boolean } }

export interface SelectionPolicy {
  speaker_mode: 'all' | 'allowlist'
  excluded_speakers: string[]
  allowlist_speakers: string[]
  episode_overrides: Record<string, string[]>
  excluded_episode_ids: string[]
  asset_filter: string
  asset_pattern: string
}

export interface DatabaseRecord {
  id: string
  display_name: string
  source_kind: SourceKind
  source_ref: Record<string, unknown>
  target: { path: string; [key: string]: unknown }
  downstream_identity: Record<string, unknown> | null
  selection_policy: SelectionPolicy
  execution_options?: ExecutionOptions
  settings_revision: number
  archived: boolean
  last_check: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export interface JobRecord {
  id: string
  kind: string
  state: JobState
  stage: string
  database_id: string | null
  preview_id: string | null
  can_cancel: boolean
  result: Record<string, unknown> | null
  error: { code: string; message: string; active_database_state?: string } | null
  progress?: { stage?: string; message?: string; current?: number | null; total?: number | null; percent?: number | null; updated_at?: string }
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export interface PreviewEffects {
  episodes_total: number
  records_total: number
  writes: number
  insert_ids: { total: number; items: string[] }
  replace_ids: { total: number; items: string[] }
  metadata_only_ids: { total: number; items: string[] }
  unchanged_ids: { total: number; items: string[] }
  retained_missing_ids: { total: number; items: string[] }
  delete_ids: { total: number; items: string[] }
  episode_changes: Array<Record<string, unknown>>
  delete_episodes: Array<Record<string, unknown>>
  reasons: Record<string, string>
}

export interface Preview {
  preview_id: string
  operation: 'create' | 'update' | 'rebuild' | 'remove_outdated'
  database_id: string | null
  status: string
  source_snapshot: Record<string, unknown>
  target_identity: { path: string; [key: string]: unknown }
  selection_policy: SelectionPolicy
  representation: Record<string, unknown>
  validation_findings: Array<{ severity: string; code: string; message: string }>
  effects: PreviewEffects
  execution_options?: ExecutionOptions
  required_acknowledgments: string[]
  base_settings_hash?: string
}

export interface BridgeError { code: string; message: string; field?: string | null; details_id?: string | null }
export interface Envelope<T> { ok: boolean; data?: T; error?: BridgeError }
export interface Handshake { api_version: string; backend_version: string; capabilities?: string[] }
