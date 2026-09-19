import { useMemo, useState } from 'react'
import type { Report } from '../api/types'

export interface ReportViewProps {
  report: Report
  onClose?: () => void
  onSave?: (report: Report) => Promise<void> | void
  originLabel?: string
}

const statusLabel: Record<Report['status'], string> = { pass: 'Passed', warnings: 'Warnings', partial: 'Partial coverage', failed: 'Failed', unavailable: 'Not available' }

export function ReportView({ report, onClose, onSave, originLabel = 'Report' }: ReportViewProps) {
  const [copied, setCopied] = useState(false)
  const [saving, setSaving] = useState(false)
  const raw = useMemo(() => JSON.stringify(report, null, 2), [report])
  const copy = async () => {
    try { await navigator.clipboard.writeText(raw); setCopied(true); window.setTimeout(() => setCopied(false), 1600) }
    catch { setCopied(false) }
  }
  const save = async () => { if (!onSave) return; setSaving(true); try { await onSave(report) } finally { setSaving(false) } }
  return <section className="card report-view" aria-label={originLabel}>
    <div className="report-heading"><div><p className="eyebrow">{originLabel}</p><h2>{report.kind.replaceAll('_', ' ')}</h2><p className="muted">{report.scope.database_id ?? report.scope.release_id ?? 'Scoped operation'} · {report.generated_at}</p></div><span className={`status status-${report.status === 'pass' ? 'good' : report.status === 'failed' || report.status === 'unavailable' ? 'danger' : 'warning'}`}>{statusLabel[report.status]}</span></div>
    {Object.keys(report.summary).length > 0 && <div className="metric-grid">{Object.entries(report.summary).map(([key, value]) => <div className="metric" key={key}><strong>{value === null || value === undefined ? 'Not available' : String(value)}</strong><span>{key.replaceAll('_', ' ')}</span></div>)}</div>}
    {report.findings.length > 0 ? <div className="report-findings"><h3>Findings</h3>{report.findings.map((finding, index) => <div className={`finding finding-${finding.severity}`} key={`${finding.code}-${index}`}><strong>{finding.severity.toUpperCase()} · {finding.code}</strong><p>{finding.message}</p>{(finding.path || finding.field) && <code>{finding.path ?? finding.field}</code>}</div>)}</div> : <p className="info-box">No findings were reported.</p>}
    <details className="report-details"><summary>Technical details</summary><pre>{JSON.stringify(report.details, null, 2)}</pre></details>
    <div className="button-row"><button className="button secondary" onClick={() => void copy()}>{copied ? 'Copied' : 'Copy report'}</button>{onSave && <button className="button secondary" disabled={saving} onClick={() => void save()}>{saving ? 'Saving…' : 'Save report'}</button>}{onClose && <button className="button" onClick={onClose}>Back</button>}</div>
  </section>
}

export default ReportView
