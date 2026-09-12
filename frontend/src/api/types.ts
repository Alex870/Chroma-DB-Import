export type SourceKind = 'folder' | 'managed'
export type JobState = 'queued' | 'running' | 'succeeded' | 'succeeded_with_warnings' | 'failed' | 'interrupted' | 'cancelled'

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
  required_acknowledgments: string[]
  base_settings_hash?: string
}

export interface BridgeError { code: string; message: string; field?: string | null; details_id?: string | null }
export interface Envelope<T> { ok: boolean; data?: T; error?: BridgeError }
