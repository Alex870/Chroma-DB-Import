import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RedundancyAnalysis } from './RedundancyAnalysis'
import type { AppClient } from '../api/client'

const context = {
  ref: { connection_id: 'connection-1', partition_id: 'partition-1' },
  display_name: 'TFM Show', context_type: 'podcast', corpus_id: 'corpus-1', workflow_profile: 'podcast',
  local_status: 'active', producer_status: 'ready', source_root: 'C:/pipeline', catalog_path: 'C:/pipeline/catalog.sqlite3',
  source_status: { completed: 57, pending: 0, failed: 0, quarantined: 0 }, source_available: true,
  active_database: {}, release_inventory: [{ upstream_release_id: 'release-57', analyzable: true, export_path: 'C:/pipeline/export' }],
  matching_database_ids: [], capabilities: { import: true, analyze: true, archive: true }, suggested_next_action: { action: 'analyze' },
  tracking: { state: 'current', recommended_action: 'analyze', reason: 'Ready', checked_at: '2026-09-13T00:00:00Z', active_release_id: 'release-57', latest_release: { upstream_release_id: 'release-57' }, last_applied_release_id: 'release-57', matching_databases: [], legacy_candidates: [], suggested_target: 'C:/exports/tfm', profile_fingerprint: 'profile-1', history: [] },
} as any

function makeApi() {
  const completed = { id: 'job-1', kind: 'redundancy', state: 'succeeded' as const, stage: 'complete', database_id: null, preview_id: null, can_cancel: false, result: { report: { schema_version: 'gui-report-v1', report_id: 'report-1', kind: 'redundancy_assessment', scope: {}, generated_at: '2026-09-13T00:00:00Z', status: 'pass', summary: {}, findings: [], details: {} } }, error: null, progress: { stage: 'complete', message: 'Completed.', percent: 100 }, created_at: '', started_at: '', completed_at: '' }
  return {
    listContexts: vi.fn(async () => [context]),
    getRedundancySettings: vi.fn(async () => ({ policy: { lexical_enabled: true, structural_enabled: true, dense_enabled: true, judge_enabled: false, judge_record_fraction: 0.05, judge_max_calls: 250, judge_max_neighbors: 5, judge_timeout_seconds: 30, judge_job_seconds: 900 }, policy_fingerprint: 'policy-1', revision: 1, judge_config: null, judge_config_fingerprint: 'judge-1' })),
    saveRedundancyPolicy: vi.fn(async (_ref, changes) => ({ policy: changes, policy_fingerprint: 'policy-2', revision: 2 })),
    saveJudgeConfig: vi.fn(async (_ref, changes) => ({ judge_config: changes, judge_config_fingerprint: 'judge-2' })),
    probeJudge: vi.fn(async (_ref, changes) => ({ reachable: true, configured_model: String(changes?.model), model_available: true, models: [String(changes?.model)], base_url: String(changes?.base_url), checked_at: '' })),
    reviewJudgePilot: vi.fn(async (_ref, release, channels) => ({ review_id: 'review-1', upstream_release_id: release, channels, calculated_upper_bound: 20 })),
    startRedundancyAction: vi.fn(async () => completed),
    getJob: vi.fn(async () => completed),
  } as unknown as AppClient
}

describe('semantic judge workflow', () => {
  it('shows configuration, tests the endpoint, and requires a saved enablement before review', async () => {
    const api = makeApi()
    render(<RedundancyAnalysis api={api} />)
    await waitFor(() => expect(screen.getByRole('option', { name: /TFM Show/ })).toBeTruthy())
    fireEvent.click(screen.getByRole('tab', { name: 'Judge' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Judge model ID' }), { target: { value: 'Qwen/local-27b' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /Enable semantic judge/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Test endpoint' }))
    await waitFor(() => expect(screen.getByText('Endpoint ready')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Save judge policy' }))
    await waitFor(() => expect(api.saveRedundancyPolicy).toHaveBeenCalledWith(context.ref, expect.objectContaining({ judge_enabled: true }), 'policy-1'))
    fireEvent.click(screen.getByRole('button', { name: 'Save connection' }))
    await waitFor(() => expect(api.saveJudgeConfig).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: 'Review pilot' }))
    await waitFor(() => expect(screen.getByText('Frozen pilot review')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Run reviewed pilot' }))
    await waitFor(() => expect(api.startRedundancyAction).toHaveBeenCalledWith(expect.objectContaining({ action: 'pilot', review_id: 'review-1' })))
  })
})
