import { useEffect, useMemo, useState } from 'react'
import type { AppClient } from '../api/client'
import { ReportView } from '../components/ReportView'
import type { ContextSummary, JobRecord, Report } from '../api/types'

type Tab = 'coverage' | 'assessment' | 'judge' | 'results'
type Values = Record<string, unknown>

function record(value: unknown): Values { return value && typeof value === 'object' ? value as Values : {} }

export function RedundancyAnalysis({ api }: { api: AppClient }) {
  const [contexts, setContexts] = useState<ContextSummary[]>([])
  const [context, setContext] = useState<ContextSummary | null>(null)
  const [releaseId, setReleaseId] = useState('')
  const [tab, setTab] = useState<Tab>('coverage')
  const [report, setReport] = useState<Report | null>(null)
  const [settings, setSettings] = useState<Values | null>(null)
  const [busy, setBusy] = useState(false)
  const [channels, setChannels] = useState<string[]>(['lexical', 'structural', 'dense'])
  const [status, setStatus] = useState<string | null>(null)
  const [judgeConfig, setJudgeConfig] = useState<Values>({ base_url: 'http://127.0.0.1:8000/v1', model: '', timeout_seconds: 60 })
  const [policyDraft, setPolicyDraft] = useState<Values>({ judge_enabled: false, judge_record_fraction: 0.05, judge_max_calls: 250, judge_max_neighbors: 5, judge_timeout_seconds: 30, judge_job_seconds: 900 })
  const [judgeProbe, setJudgeProbe] = useState<Values | null>(null)
  const [pilotReview, setPilotReview] = useState<Values | null>(null)
  const [bundle, setBundle] = useState<Values | null>(null)
  const [labelsPath, setLabelsPath] = useState('')
  const [queriesPath, setQueriesPath] = useState('')
  const [queryResultsPath, setQueryResultsPath] = useState('')

  useEffect(() => {
    let active = true
    const list = (api as Partial<AppClient>).listContexts
    if (typeof list !== 'function') { setContexts([]); return () => { active = false } }
    void list.call(api).then((rows) => {
      if (!active) return
      setContexts(rows)
      const selected = rows.find((item) => item.release_inventory.some((release) => release.analyzable)) ?? rows[0] ?? null
      setContext(selected)
      setReleaseId(String(selected?.release_inventory.find((release) => release.analyzable)?.upstream_release_id ?? ''))
    }).catch(() => { if (active) setContexts([]) })
    return () => { active = false }
  }, [api])

  useEffect(() => {
    if (!context) { setSettings(null); setPilotReview(null); return }
    let active = true
    void api.getRedundancySettings(context.ref).then((value) => {
      if (!active) return
      setSettings(value)
      setPolicyDraft(record(value.policy))
      const savedJudge = record(value.judge_config)
      setJudgeConfig((current) => ({ ...current, ...savedJudge }))
    }).catch(() => { if (active) setSettings(null) })
    return () => { active = false }
  }, [api, context])

  const analyzable = useMemo(() => context?.release_inventory.filter((release) => release.analyzable) ?? [], [context])
  const latest = analyzable.find((release) => String(release.upstream_release_id) === releaseId) ?? analyzable[0]

  const runJob = async (payload: Values): Promise<JobRecord> => {
    const job = await api.startRedundancyAction(payload)
    return waitForJob(api, job.id)
  }

  const run = async (action: 'preview' | 'assess') => {
    if (!context || !latest) { setStatus('Import a valid downstream export before analysis.'); return }
    if (action === 'assess' && channels.length === 0) { setStatus('Select at least one channel for this run.'); return }
    setBusy(true); setStatus(null)
    try {
      const complete = await runJob({ action, context: context.ref, upstream_release_id: latest.upstream_release_id, ...(action === 'assess' ? { channels } : {}) })
      if (complete.error) throw new Error(complete.error.message)
      if (complete.result?.report) setReport(complete.result.report as Report)
      setStatus(`${action === 'preview' ? 'Coverage preview' : 'Assessment'} completed.`)
    } catch (exc) { setStatus(exc instanceof Error ? exc.message : 'Redundancy operation failed.') }
    finally { setBusy(false) }
  }

  const savePolicy = async () => {
    if (!context || !settings) return
    setBusy(true)
    try {
      const saved = await api.saveRedundancyPolicy(context.ref, policyDraft, String(settings.policy_fingerprint ?? ''))
      setSettings({ ...settings, ...saved })
      setStatus('Analysis policy saved for this context.')
    } catch (exc) { setStatus(exc instanceof Error ? exc.message : 'Analysis policy could not be saved.') }
    finally { setBusy(false) }
  }

  const saveJudge = async () => {
    if (!context) return
    setBusy(true)
    try {
      const saved = await api.saveJudgeConfig(context.ref, judgeConfig, String(settings?.judge_config_fingerprint ?? ''))
      setSettings({ ...(settings ?? {}), ...saved, judge_config_fingerprint: saved.judge_config_fingerprint })
      setJudgeConfig(record(saved.judge_config))
      setStatus('Judge connection saved. No model request was made.')
    } catch (exc) { setStatus(exc instanceof Error ? exc.message : 'Judge configuration could not be saved.') }
    finally { setBusy(false) }
  }

  const probeJudge = async () => {
    if (!context || !String(judgeConfig.model ?? '').trim()) { setStatus('Enter the exact model ID served by your local vLLM endpoint first.'); return }
    setBusy(true); setStatus(null); setJudgeProbe(null)
    try {
      const result = await api.probeJudge(context.ref, judgeConfig)
      setJudgeProbe(result)
      setStatus(Boolean(result.model_available) ? 'The endpoint is reachable and the configured model is available.' : 'The endpoint is reachable, but the configured model ID was not listed by it.')
    } catch (exc) { setStatus(exc instanceof Error ? exc.message : 'The local judge endpoint could not be tested.') }
    finally { setBusy(false) }
  }

  const reviewPilot = async () => {
    if (!context || !latest || channels.length === 0) { setStatus('Select a release and at least one channel before reviewing a pilot.'); return }
    if (!Boolean(policyDraft.judge_enabled)) { setStatus('Enable the semantic judge in Judge settings before reviewing a pilot.'); setTab('judge'); return }
    setBusy(true); setStatus(null)
    try {
      setPilotReview(await api.reviewJudgePilot(context.ref, String(latest.upstream_release_id), channels))
      setStatus('Pilot review is frozen to the selected export, policy, channels, and judge settings.')
    } catch (exc) { setStatus(exc instanceof Error ? exc.message : 'Pilot review could not be prepared.') }
    finally { setBusy(false) }
  }

  const runPilot = async () => {
    if (!context || !pilotReview?.review_id) return
    setBusy(true); setStatus(null)
    try {
      const complete = await runJob({ action: 'pilot', context: context.ref, review_id: pilotReview.review_id, upstream_release_id: pilotReview.upstream_release_id, channels: pilotReview.channels })
      if (complete.error) throw new Error(complete.error.message)
      if (complete.result?.report) setReport(complete.result.report as Report)
      setPilotReview(null)
      setStatus('Judge pilot completed; inspect the bounded result before any downstream decision.')
    } catch (exc) { setStatus(exc instanceof Error ? exc.message : 'Judge pilot failed.') }
    finally { setBusy(false) }
  }

  const openBundle = async () => {
    const path = await api.pickFile('redundancy_bundle')
    if (!path) return
    setBusy(true); setStatus(null)
    try {
      const result = await api.openRedundancyBundle(path, context?.ref)
      setBundle({ ...result, path })
      setStatus('Bundle validated. Its scope and hashes are now fixed for review.')
    } catch (exc) { setStatus(exc instanceof Error ? exc.message : 'The redundancy bundle could not be opened.') }
    finally { setBusy(false) }
  }

  const exportLabels = async () => {
    const artifactId = String(bundle?.artifact_id ?? '')
    if (!artifactId) { setStatus('Open and validate a bundle before exporting labels.'); return }
    const path = await api.pickSaveFile('labels')
    if (!path) return
    setBusy(true); setStatus(null)
    try {
      const complete = await runJob({ action: 'label_export', context: context?.ref, artifact_id: artifactId, output_path: path })
      if (complete.error) throw new Error(complete.error.message)
      if (complete.result?.report) setReport(complete.result.report as Report)
      setStatus('Label review file exported.')
    } catch (exc) { setStatus(exc instanceof Error ? exc.message : 'Label export failed.') }
    finally { setBusy(false) }
  }

  const chooseLabels = async () => { const path = await api.pickFile('labels'); if (path) setLabelsPath(path) }
  const chooseQueries = async () => { const path = await api.pickFile('queries'); if (path) setQueriesPath(path) }
  const chooseQueryResults = async () => { const path = await api.pickFile('query_results'); if (path) setQueryResultsPath(path) }

  const evaluate = async () => {
    const artifactId = String(bundle?.artifact_id ?? '')
    if (!artifactId || !labelsPath) { setStatus('Open a validated bundle and choose the reviewed labels file first.'); return }
    const output = await api.pickSaveFile('evaluation')
    if (!output) return
    setBusy(true); setStatus(null)
    try {
      const complete = await runJob({ action: 'evaluate', context: context?.ref, artifact_id: artifactId, labels_path: labelsPath, ...(queriesPath ? { queries_path: queriesPath } : {}), ...(queryResultsPath ? { query_results_path: queryResultsPath } : {}), output_path: output })
      if (complete.error) throw new Error(complete.error.message)
      if (complete.result?.report) setReport(complete.result.report as Report)
      setStatus('Evaluation completed. Missing optional retrieval measurements remain explicitly unknown.')
    } catch (exc) { setStatus(exc instanceof Error ? exc.message : 'Evaluation failed.') }
    finally { setBusy(false) }
  }

  if (report) return <><header className="page-header"><div><p className="eyebrow">REDUNDANCY ANALYSIS</p><h1>Report</h1></div></header><ReportView report={report} onClose={() => setReport(null)} onSave={async (value) => { const saved = await api.exportReport(value); setStatus(`Report saved to ${String(saved.path ?? saved.report_id)}`) }} /></>

  return <>
    <header className="page-header"><div><p className="eyebrow">ADVANCED TOOLS</p><h1>Redundancy analysis</h1><p className="muted">Advisory analysis is version-bound and never activates, deletes, or changes database content. It does not approve deletion.</p></div></header>
    <section className="card redundancy-scope"><div className="input-row"><label className="field"><span>Context</span><select aria-label="Analysis context" value={context ? `${context.ref.connection_id}:${context.ref.partition_id}` : ''} onChange={(event) => { const next = contexts.find((item) => `${item.ref.connection_id}:${item.ref.partition_id}` === event.target.value) ?? null; setContext(next); setReleaseId(String(next?.release_inventory.find((release) => release.analyzable)?.upstream_release_id ?? '')); setBundle(null); setPilotReview(null) }}><option value="">Select a context</option>{contexts.map((item) => <option key={`${item.ref.connection_id}:${item.ref.partition_id}`} value={`${item.ref.connection_id}:${item.ref.partition_id}`}>{item.display_name} · {item.ref.partition_id}</option>)}</select></label><label className="field"><span>Release</span><select aria-label="Analysis release" disabled={!context} value={String(latest?.upstream_release_id ?? '')} onChange={(event) => setReleaseId(event.target.value)}><option value="">{latest ? `${latest.upstream_release_id} · latest analyzable` : 'No analyzable release'}</option>{analyzable.filter((release) => String(release.upstream_release_id) !== String(latest?.upstream_release_id)).map((release) => <option key={String(release.upstream_release_id)} value={String(release.upstream_release_id)}>{String(release.upstream_release_id)}</option>)}</select></label></div>{!context && <div className="info-box"><strong>Select a source context</strong><p>Analysis is available only for a validated context and readable downstream export.</p></div>}{context && !latest && <div className="info-box"><strong>Import required before analysis</strong><p>This context has no validated analyzable export. View source or import a ready release first.</p></div>}{status && <div className="info-box" role="status">{status}</div>}</section>
    <div className="tabs" role="tablist" aria-label="Redundancy views">{(['coverage', 'assessment', 'judge', 'results'] as Tab[]).map((item) => <button key={item} role="tab" aria-selected={tab === item} className={tab === item ? 'tab active' : 'tab'} onClick={() => setTab(item)}>{item[0].toUpperCase() + item.slice(1)}</button>)}</div>
    {tab === 'coverage' && <section className="card settings-card"><h2>Coverage</h2><p className="muted">Preview coverage is model-free and creates a report only; it does not create a frozen assessment job.</p><div className="button-row"><button className="button primary" disabled={busy || !latest} onClick={() => void run('preview')}>Preview coverage</button><button className="button secondary" onClick={() => setTab('assessment')}>Assessment settings</button></div>{settings && <details open><summary>Saved analysis settings</summary><p className="muted">Policy revision {String(settings.revision ?? 'Not available')} · {String(settings.policy_fingerprint ?? 'Not available')}</p><pre>{JSON.stringify(settings.policy ?? {}, null, 2)}</pre><button className="button secondary" disabled={busy} onClick={() => void savePolicy()}>Save policy</button></details>}</section>}
    {tab === 'assessment' && <section className="card settings-card"><h2>Assessment</h2><p className="muted">Channels are this run only. Saved judge permission never runs the judge during ordinary assessment.</p><div className="speaker-list">{['lexical', 'structural', 'dense'].map((channel) => <label key={channel}><input type="checkbox" checked={channels.includes(channel)} onChange={() => { setPilotReview(null); setChannels((current) => current.includes(channel) ? current.filter((item) => item !== channel) : [...current, channel]) }} />{channel === 'structural' ? 'Source and structure' : channel[0].toUpperCase() + channel.slice(1)} check</label>)}</div><button className="button primary" disabled={busy || !latest || channels.length === 0} onClick={() => void run('assess')}>Run assessment</button></section>}
     {tab === 'judge' && <section className="card settings-card"><h2>Semantic judge</h2><p className="muted">Optional, local, and explicitly reviewed. Database creation and model-free redundancy analysis do not require an LLM. A judge pilot only runs after you save these settings, freeze a review, and confirm the run.</p><div className="info-box"><strong>For a local vLLM server</strong><p>Use its OpenAI-compatible URL, usually <code>http://127.0.0.1:8000/v1</code>, and the exact model ID returned by <code>/v1/models</code>. The endpoint must be local to this computer.</p></div><label className="field"><span><input type="checkbox" checked={Boolean(policyDraft.judge_enabled)} onChange={(event) => { setPilotReview(null); setPolicyDraft({ ...policyDraft, judge_enabled: event.target.checked }) }} /> Enable semantic judge pilots for this context</span><small>Saving this permission never contacts the model and never runs a pilot automatically.</small></label><div className="detail-grid"><label className="field"><span>Base URL</span><input aria-label="Judge base URL" value={String(judgeConfig.base_url ?? '')} onChange={(event) => { setJudgeProbe(null); setJudgeConfig({ ...judgeConfig, base_url: event.target.value }) }} /></label><label className="field"><span>Served model ID</span><input aria-label="Judge model ID" value={String(judgeConfig.model ?? '')} placeholder="Exact ID from /v1/models" onChange={(event) => { setJudgeProbe(null); setJudgeConfig({ ...judgeConfig, model: event.target.value }) }} /></label><label className="field"><span>Request timeout seconds</span><input type="number" min="1" max="300" value={String(judgeConfig.timeout_seconds ?? 60)} onChange={(event) => setJudgeConfig({ ...judgeConfig, timeout_seconds: Number(event.target.value) })} /></label><label className="field"><span>Judge sample fraction</span><input type="number" min="0" max="1" step="0.01" value={String(policyDraft.judge_record_fraction ?? 0.05)} onChange={(event) => setPolicyDraft({ ...policyDraft, judge_record_fraction: Number(event.target.value) })} /><small>Fraction of eligible candidates, capped below.</small></label><label className="field"><span>Maximum judge calls</span><input type="number" min="0" max="10000" value={String(policyDraft.judge_max_calls ?? 250)} onChange={(event) => setPolicyDraft({ ...policyDraft, judge_max_calls: Number(event.target.value) })} /></label><label className="field"><span>Maximum neighbors</span><input type="number" min="1" max="20" value={String(policyDraft.judge_max_neighbors ?? 5)} onChange={(event) => setPolicyDraft({ ...policyDraft, judge_max_neighbors: Number(event.target.value) })} /></label><label className="field"><span>Job time limit seconds</span><input type="number" min="1" max="86400" value={String(policyDraft.judge_job_seconds ?? 900)} onChange={(event) => setPolicyDraft({ ...policyDraft, judge_job_seconds: Number(event.target.value) })} /></label></div><div className="button-row"><button className="button secondary" disabled={busy || !context} onClick={() => void savePolicy()}>Save judge policy</button><button className="button secondary" disabled={busy || !context || !String(judgeConfig.model ?? '').trim()} onClick={() => void saveJudge()}>Save connection</button><button className="button secondary" disabled={busy || !context || !String(judgeConfig.model ?? '').trim()} onClick={() => void probeJudge()}>Test endpoint</button><button className="button primary" disabled={busy || !latest || !Boolean(policyDraft.judge_enabled) || !String(judgeConfig.model ?? '').trim()} onClick={() => void reviewPilot()}>Review pilot</button></div>{judgeProbe && <div className="info-box" role="status"><strong>{Boolean(judgeProbe.model_available) ? 'Endpoint ready' : 'Model ID not found'}</strong><p>{String(judgeProbe.base_url)} · models reported: {Array.isArray(judgeProbe.models) ? judgeProbe.models.join(', ') || 'none' : 'unknown'}</p></div>}{pilotReview && <div className="review-box"><strong>Frozen pilot review</strong><p>{String(pilotReview.calculated_upper_bound ?? 'Bounded')} maximum judge calls · {String(pilotReview.upstream_release_id)} · channels: {Array.isArray(pilotReview.channels) ? pilotReview.channels.join(', ') : 'selected'}</p><code>{String(pilotReview.review_id)}</code><div className="button-row"><button className="button primary" disabled={busy || !Boolean(policyDraft.judge_enabled)} onClick={() => void runPilot()}>Run reviewed pilot</button><button className="button" disabled={busy} onClick={() => setPilotReview(null)}>Cancel</button></div></div>}</section>}
    {tab === 'results' && <section className="card settings-card"><h2>Results</h2><p className="muted">Open a validated bundle to display scoped results. Invalid or different-scope bundles remain explicit reports.</p><button className="button secondary" disabled={busy} onClick={() => void openBundle()}>Open bundle</button>{bundle && <><div className="review-box"><strong>Validated bundle</strong><p>Artifact ID: <code>{String(bundle.artifact_id ?? 'Unavailable')}</code></p><p className="muted">The bundle remains read-only; exports and evaluation do not alter the active database.</p>{Boolean(record(bundle.report).status) && <ReportView report={bundle.report as Report} originLabel="Bundle validation" onClose={() => setBundle(null)} />}</div><div className="detail-grid"><div><span className="field-label">Labels file</span><code>{labelsPath || 'Not selected'}</code><button className="button small" onClick={() => void chooseLabels()}>Choose labels</button></div><div><span className="field-label">Queries (optional)</span><code>{queriesPath || 'Not selected'}</code><button className="button small" onClick={() => void chooseQueries()}>Choose queries</button></div><div><span className="field-label">Query results (optional)</span><code>{queryResultsPath || 'Not selected'}</code><button className="button small" onClick={() => void chooseQueryResults()}>Choose results</button></div></div><div className="button-row"><button className="button secondary" disabled={busy || !bundle.artifact_id} onClick={() => void exportLabels()}>Export label review</button><button className="button primary" disabled={busy || !bundle.artifact_id || !labelsPath} onClick={() => void evaluate()}>Evaluate labels</button></div></>}</section>}
  </>
}

async function waitForJob(api: AppClient, id: string): Promise<JobRecord> { for (let attempt = 0; attempt < 120; attempt += 1) { const job = await api.getJob(id); if (!['queued', 'running'].includes(job.state)) return job; await new Promise((resolve) => window.setTimeout(resolve, 100)) } throw new Error('The analysis is taking longer than expected. Open Activity to continue watching it.') }

export default RedundancyAnalysis
