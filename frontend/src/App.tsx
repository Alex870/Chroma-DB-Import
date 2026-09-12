import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { client, ClientError, type AppClient } from './api/client'
import { mockClient } from './api/mockClient'
import type { DatabaseRecord, JobRecord, Preview, SelectionPolicy } from './api/types'

type Area = 'databases' | 'activity' | 'advanced' | 'settings'
type DatabaseTab = 'overview' | 'content' | 'history' | 'settings' | 'maintenance'

const useMock = import.meta.env.VITE_USE_MOCK_BRIDGE === 'true'

function Status({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'good' | 'warning' | 'danger' }) {
  return <span className={`status status-${tone}`}><span aria-hidden="true" />{children}</span>
}

function Empty({ title, body, action }: { title: string; body: string; action?: React.ReactNode }) {
  return <section className="empty"><div className="empty-icon" aria-hidden="true">◫</div><h2>{title}</h2><p>{body}</p>{action}</section>
}

function App() {
  const api: AppClient = useMock ? mockClient : client
  const initialArea: Area = new URLSearchParams(window.location.search).get('workspace') === 'contexts' ? 'advanced' : 'databases'
  const [area, setArea] = useState<Area>(initialArea)
  const [databases, setDatabases] = useState<DatabaseRecord[]>([])
  const [jobs, setJobs] = useState<JobRecord[]>([])
  const [selected, setSelected] = useState<DatabaseRecord | null>(null)
  const [tab, setTab] = useState<DatabaseTab>('overview')
  const [advancedView, setAdvancedView] = useState<'source' | 'redundancy'>('source')
  const [query, setQuery] = useState('')
  const [view, setView] = useState<'library' | 'create' | 'update' | 'add'>('library')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [existingTarget, setExistingTarget] = useState('')
  const [existingSource, setExistingSource] = useState('')

  const refresh = async () => {
    try { const version = await api.handshake(); if (version.api_version !== 'gui-api-v1') throw new ClientError({ code: 'BRIDGE_VERSION_MISMATCH', message: 'The desktop bridge is older than this frontend. Rebuild the packaged UI before continuing.', field: null, details_id: null }); setDatabases(await api.listDatabases()); setJobs(await api.listJobs()); setError(null) }
    catch (exc) { setError(exc instanceof ClientError ? exc.message : 'The desktop connection is unavailable.') }
  }
  useEffect(() => { void refresh() }, [])
  useEffect(() => { if (!jobs.some((job) => job.state === 'queued' || job.state === 'running')) return; const id = window.setInterval(() => void refresh(), 1000); return () => window.clearInterval(id) }, [jobs])

  const filtered = useMemo(() => databases.filter((db) => db.display_name.toLowerCase().includes(query.toLowerCase())), [databases, query])
  const running = jobs.filter((job) => job.state === 'queued' || job.state === 'running')

  const choose = (db: DatabaseRecord) => { setSelected(db); setView('library'); setArea('databases'); setTab('overview') }
  const startUpdate = (db: DatabaseRecord) => { setSelected(db); setView('update'); setPreview(null); setError(null) }
  const archive = async (db: DatabaseRecord) => { if (!window.confirm('Hide this library entry? Database files remain.')) return; await api.archiveDatabase(db.id); setMessage('Hidden from this library; database files remain.'); await refresh() }

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark">C</div><div><strong>Chroma DB Import</strong><small>Database manager</small></div></div>
      <nav aria-label="Primary navigation">
        <button className={area === 'databases' ? 'nav-item active' : 'nav-item'} onClick={() => { setArea('databases'); setView('library') }}>▦ <span>Databases</span></button>
        <button className={area === 'activity' ? 'nav-item active' : 'nav-item'} onClick={() => setArea('activity')}>◷ <span>Activity</span>{running.length > 0 && <b>{running.length}</b>}</button>
        <div className="nav-section">ADVANCED TOOLS</div>
        <button className={area === 'advanced' && advancedView === 'source' ? 'nav-item active' : 'nav-item'} onClick={() => { setArea('advanced'); setAdvancedView('source') }}>⌁ <span>Source connections</span></button>
        <button className={area === 'advanced' && advancedView === 'redundancy' ? 'nav-item active' : 'nav-item'} onClick={() => { setArea('advanced'); setAdvancedView('redundancy') }}>◌ <span>Redundancy analysis</span></button>
        <div className="nav-spacer" />
        <button className={area === 'settings' ? 'nav-item active' : 'nav-item'} onClick={() => setArea('settings')}>⚙ <span>Settings & help</span></button>
      </nav>
    </aside>
    <main className="main-content">
      {error && <div className="alert alert-danger" role="alert"><strong>Connection or workflow issue.</strong> {error}<button onClick={() => setError(null)} aria-label="Dismiss">×</button></div>}
      {message && <div className="alert alert-good" role="status">{message}<button onClick={() => setMessage(null)} aria-label="Dismiss">×</button></div>}
      {running.length > 0 && <button className="job-strip" onClick={() => setArea('activity')}><span className="spinner" />{running.length} operation{running.length > 1 ? 's' : ''} in progress <span>View activity →</span></button>}
      {area === 'databases' && view === 'library' && <Library databases={filtered} query={query} setQuery={setQuery} onCreate={() => { setView('create'); setSelected(null) }} onAdd={() => setView('add')} onSelect={choose} onUpdate={startUpdate} onArchive={archive} />}
      {area === 'databases' && view === 'create' && <Create api={api} onCancel={() => setView('library')} onUseExisting={(targetPath, sourcePath) => { setExistingTarget(targetPath); setExistingSource(sourcePath ?? ''); setView('add') }} onDone={async () => { await refresh(); setView('library'); setArea('activity') }} setError={setError} />}
      {area === 'databases' && view === 'add' && <AddExisting api={api} initialTarget={existingTarget} initialSource={existingSource} onCancel={() => setView('library')} onDone={async () => { setExistingTarget(''); setExistingSource(''); await refresh(); setView('library') }} setError={setError} />}
      {area === 'databases' && view === 'update' && selected && <Update api={api} db={selected} preview={preview} setPreview={setPreview} onBack={() => setView('library')} onSourceConnections={() => { setArea('advanced'); setAdvancedView('source') }} onRepairIdentity={() => { setExistingTarget(selected.target.path); setExistingSource(String(selected.source_ref.path ?? selected.source_ref.source_root ?? '')); setView('add') }} onCreateSeparate={() => { setView('create'); setSelected(null) }} onApplied={async () => { await refresh(); setArea('activity') }} setError={setError} />}
      {area === 'databases' && view === 'library' && selected && <DatabaseDetail api={api} db={selected} tab={tab} setTab={setTab} onUpdate={() => void startUpdate(selected)} onApplied={async () => { await refresh(); setArea('activity') }} onSaved={async () => { await refresh(); const latest = await api.getDatabase(selected.id); setSelected(latest) }} setError={setError} />}
      {area === 'activity' && <Activity jobs={jobs} api={api} onRefresh={refresh} onViewDatabase={(id) => { const db = databases.find((item) => item.id === id); if (db) choose(db) }} />}
      {area === 'advanced' && advancedView === 'source' && <Advanced api={api} selected={selected} onPrepared={(db) => { setArea('databases'); startUpdate(db) }} />}
      {area === 'advanced' && advancedView === 'redundancy' && <Redundancy />}
      {area === 'settings' && <><Settings api={api} /><BridgeShellCheck api={api} /></>}
    </main>
  </div>
}

function Library({ databases, query, setQuery, onCreate, onAdd, onSelect, onUpdate, onArchive }: { databases: DatabaseRecord[]; query: string; setQuery: (v: string) => void; onCreate: () => void; onAdd: () => void; onSelect: (db: DatabaseRecord) => void; onUpdate: (db: DatabaseRecord) => void; onArchive: (db: DatabaseRecord) => void }) {
  return <><header className="page-header"><div><p className="eyebrow">LIBRARY</p><h1>Databases</h1><p className="muted">Choose a database to review content, update it, or inspect its technical details.</p></div><div className="header-actions"><button className="button secondary" onClick={onAdd}>Add existing</button><button className="button primary" onClick={onCreate}>＋ Create database</button></div></header>
    <div className="toolbar"><label className="search">⌕<input aria-label="Search databases" placeholder="Search databases…" value={query} onChange={(e) => setQuery(e.target.value)} /></label><span className="muted">{databases.length} {databases.length === 1 ? 'database' : 'databases'}</span></div>
    {databases.length === 0 ? <Empty title="No databases yet" body="Create a database from processed podcast or meeting content, or add an existing export." action={<><button className="button primary" onClick={onCreate}>Create database</button><button className="button secondary" onClick={onAdd}>Add existing database</button></>} /> : <section className="card table-card"><table><thead><tr><th>Database</th><th>Source</th><th>Status</th><th>Last checked</th><th aria-label="Actions" /></tr></thead><tbody>{databases.map((db) => <tr key={db.id} onClick={() => onSelect(db)}><td><button className="link-button" onClick={() => onSelect(db)}>{db.display_name}</button><small>{db.target.path}</small></td><td>{db.source_kind === 'managed' ? 'Podcast-RAG source' : 'Processed files folder'}</td><td>{db.last_check ? <Status tone={db.last_check.status === 'up_to_date' ? 'good' : 'warning'}>{String(db.last_check.label ?? 'Needs review')}</Status> : <Status>Not checked</Status>}</td><td>{db.last_check ? String(db.last_check.checked_at ?? 'Unknown') : '—'}</td><td><div className="row-actions"><button className="button small" onClick={(e) => { e.stopPropagation(); void onUpdate(db) }}>Update</button><button className="icon-button" aria-label={`Archive ${db.display_name}`} onClick={(e) => { e.stopPropagation(); void onArchive(db) }}>⋯</button></div></td></tr>)}</tbody></table></section>}
  </>
}

function DatabaseDetail({ api, db, tab, setTab, onUpdate, onApplied, onSaved, setError }: { api: AppClient; db: DatabaseRecord; tab: DatabaseTab; setTab: (tab: DatabaseTab) => void; onUpdate: () => void; onApplied: () => Promise<void>; onSaved: () => Promise<void>; setError: (value: string | null) => void }) {
  return <section className="detail card"><div className="detail-heading"><div><p className="eyebrow">DATABASE</p><h2>{db.display_name}</h2><p className="muted">{db.source_kind === 'managed' ? 'Podcast-RAG source' : 'Processed files folder'} · <Status>{db.last_check ? 'Checked' : 'Not checked'}</Status></p></div><button className="button primary" onClick={onUpdate}>Update</button></div><div className="tabs" role="tablist">{(['overview', 'content', 'history', 'settings', 'maintenance'] as DatabaseTab[]).map((item) => <button key={item} role="tab" aria-selected={tab === item} className={tab === item ? 'tab active' : 'tab'} onClick={() => setTab(item)}>{item[0].toUpperCase() + item.slice(1)}</button>)}</div>{tab === 'overview' && <div className="detail-grid"><div><span className="field-label">Target</span><code>{db.target.path}</code></div><div><span className="field-label">Embedding profile</span><strong>{String(db.downstream_identity?.profile ?? db.target.representation_profile ?? 'Unknown')}</strong></div><div><span className="field-label">Database identity</span><code>{String(db.downstream_identity?.database_id ?? db.id)}</code></div><div><span className="field-label">Settings revision</span><strong>{db.settings_revision}</strong></div></div>}{tab === 'content' && <ContentPanel api={api} db={db} onSaved={onSaved} />}{tab === 'history' && <HistoryPanel api={api} db={db} />}{tab === 'settings' && <DatabaseSettingsPanel api={api} db={db} onSaved={onSaved} />}{tab === 'maintenance' && <MaintenancePanel api={api} db={db} onUpdate={onUpdate} onApplied={onApplied} setError={setError} />}</section>
}

function MaintenancePanel({ api, db, onUpdate, onApplied, setError }: { api: AppClient; db: DatabaseRecord; onUpdate: () => void; onApplied: () => Promise<void>; setError: (value: string | null) => void }) {
  const [preview, setPreview] = useState<Preview | null>(null)
  const [busy, setBusy] = useState(false)
  const rebuild = async () => {
    setBusy(true)
    try {
      const job = await api.createPreview({ database_id: db.id, operation: 'rebuild' })
      const complete = await waitForJob(api, job.id)
      if (!complete.result?.preview_id) throw new Error(complete.error?.message ?? 'The rebuild review could not be prepared.')
      setPreview(await api.getPreview(String(complete.result.preview_id)))
    } catch (exc) { setError(exc instanceof Error ? exc.message : 'Rebuild review failed.') } finally { setBusy(false) }
  }
  const apply = async () => {
    if (!preview) return
    setBusy(true)
    try { await api.applyPreview(preview.preview_id, preview.required_acknowledgments); await onApplied() }
    catch (exc) { setError(exc instanceof Error ? exc.message : 'Rebuild failed.') }
    finally { setBusy(false) }
  }
  const prospectiveProfile = String(preview?.representation?.profile ?? preview?.representation?.representation_id ?? db.downstream_identity?.profile ?? 'Unknown')
  const exactIds = preview?.effects.delete_ids.items ?? []
  const reasons = preview?.effects.reasons ?? {}
  const episodeChanges = preview?.effects.episode_changes ?? []
  const deleteEpisodes = preview?.effects.delete_episodes ?? []
  return <div className="placeholder-panel"><h3>Maintenance</h3><p className="muted">Maintenance operations are separate from ordinary Update. Rebuild creates a fresh staged version and reviews it before activation; removal uses an exact-record approval.</p><div className="button-row"><button className="button secondary" disabled={busy || db.source_kind === 'managed'} onClick={() => void rebuild()}>Review rebuild</button><button className="button secondary" onClick={onUpdate}>Review remove outdated records</button></div>{db.source_kind === 'managed' && <p className="muted">Managed rebuild is available only when the producer can publish a new validated release. Use Source connections to prepare one, then review it through Update.</p>}{preview && <div className="review-box"><Status tone={preview.validation_findings.some((item) => item.severity === 'error') ? 'warning' : 'good'}>{preview.validation_findings.length ? 'Review findings' : preview.operation === 'remove_outdated' ? 'Removal review ready' : 'Rebuild ready'}</Status><div className="detail-grid"><div><span className="field-label">Current target</span><code>{String(preview.target_identity.path ?? db.target.path)}</code></div><div><span className="field-label">Prospective profile</span><strong>{prospectiveProfile}</strong></div><div><span className="field-label">Work scope</span><strong>{preview.effects.episodes_total} episodes · {preview.effects.records_total} records · {preview.effects.writes} writes</strong></div><div><span className="field-label">Recovery behavior</span><strong>Stage first; keep the active copy recoverable until completion.</strong></div></div>{episodeChanges.length > 0 && <details><summary>Episode scope ({episodeChanges.length})</summary><ul className="event-log">{episodeChanges.map((episode, index) => <li key={`${String(episode.episode_id)}-${index}`}><strong>{String(episode.title ?? episode.episode_id ?? 'Episode')}</strong> — {String(episode.records ?? 0)} records; added {String(episode.added ?? 0)}, changed {String(episode.changed ?? 0)}, metadata-only {String(episode.metadata_only ?? 0)}, unchanged {String(episode.unchanged ?? 0)}</li>)}</ul></details>}{preview.operation === 'remove_outdated' && <details open><summary>Exact records and reasons ({exactIds.length})</summary><DeleteEpisodeScope episodes={deleteEpisodes} />{exactIds.length === 0 ? <p className="muted">No records are selected.</p> : <ul className="event-log">{exactIds.map((id) => <li key={id}><code>{id}</code> — {reasons[id] ?? 'Explicitly accepted as outdated during maintenance review.'}</li>)}</ul>}</details>}{preview.validation_findings.map((finding) => <p className="muted" key={finding.code}>{finding.message}</p>)}<button className="button primary" disabled={busy || preview.validation_findings.some((item) => item.severity === 'error')} onClick={() => void apply()}>{busy ? 'Applying…' : preview.operation === 'remove_outdated' ? 'Apply accepted removal' : 'Apply rebuild'}</button></div>}</div>
}

function ContentPanel({ api, db, onSaved }: { api: AppClient; db: DatabaseRecord; onSaved: () => Promise<void> }) {
  const [mode, setMode] = useState(db.selection_policy.speaker_mode)
  const [assetFilter, setAssetFilter] = useState(db.selection_policy.asset_filter)
  const [assetPattern, setAssetPattern] = useState(db.selection_policy.asset_pattern)
  const [speakers, setSpeakers] = useState(db.selection_policy.allowlist_speakers.join(', '))
  const [excluded, setExcluded] = useState(db.selection_policy.excluded_speakers.join(', '))
  const [excludedEpisodes, setExcludedEpisodes] = useState<string[]>(db.selection_policy.excluded_episode_ids)
  const [episodeOverrides, setEpisodeOverrides] = useState<Record<string, string[]>>(db.selection_policy.episode_overrides)
  const [content, setContent] = useState<Record<string, unknown> | null>(null)
  const [search, setSearch] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => { void api.getDatabaseContent(db.id).then(setContent).catch(() => setContent(null)) }, [api, db.id])

  const episodeRows = (Array.isArray(content?.episodes) ? content.episodes : []) as Array<Record<string, unknown>>
  const filteredEpisodes = episodeRows.filter((episode) => `${String(episode.title ?? '')} ${String(episode.episode_id ?? '')}`.toLowerCase().includes(search.toLowerCase()))
  const save = async () => {
    setBusy(true)
    try {
      await api.updateDatabaseSettings(db.id, { selection_policy: {
        ...db.selection_policy, speaker_mode: mode, asset_filter: assetFilter, asset_pattern: assetPattern,
        allowlist_speakers: speakers.split(',').map((item) => item.trim()).filter(Boolean),
        excluded_speakers: excluded.split(',').map((item) => item.trim()).filter(Boolean),
        excluded_episode_ids: excludedEpisodes, episode_overrides: episodeOverrides,
      } })
      await onSaved()
    } finally { setBusy(false) }
  }
  const toggleExcluded = (episodeId: string) => setExcludedEpisodes((current) => current.includes(episodeId) ? current.filter((item) => item !== episodeId) : [...current, episodeId].sort())
  const setOverride = (episodeId: string, value: string) => setEpisodeOverrides((current) => ({ ...current, [episodeId]: value.split(',').map((item) => item.trim()).filter(Boolean) }))

  return <div className="placeholder-panel">
    <h3>Content selection</h3>
    <p className="muted">Global rules apply to new episodes. Episode overrides use stable episode IDs, and shared context remains available when at least one selected speaker is included.</p>
    <label className="field"><span>Search episodes</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Title or episode ID" /></label>
    <label className="field"><span>Speaker policy</span><select value={mode} onChange={(event) => setMode(event.target.value as 'all' | 'allowlist')}><option value="all">All speakers except exclusions</option><option value="allowlist">Only selected speakers</option></select></label>
    <label className="field"><span>Allowlist speakers</span><input value={speakers} onChange={(event) => setSpeakers(event.target.value)} placeholder="Host, Guest" /></label>
    <label className="field"><span>Excluded speakers</span><input value={excluded} onChange={(event) => setExcluded(event.target.value)} placeholder="Ads, Sponsor" /></label>
    <label className="field"><span>Asset filter</span><select value={assetFilter} onChange={(event) => setAssetFilter(event.target.value)}><option value="reviewed_speaker_transcript">Reviewed speaker transcripts</option><option value="cleaned_speaker_transcript">Cleaned speaker transcripts</option><option value="speaker_transcript">Raw speaker transcripts</option><option value="all">All processed assets</option><option value="custom">Custom pattern</option></select></label>
    {assetFilter === 'custom' && <label className="field"><span>Custom filename pattern</span><input value={assetPattern} onChange={(event) => setAssetPattern(event.target.value)} placeholder="*reviewed*.json" /></label>}
    {content ? <div className="episode-table"><div className="field-label">Episodes</div>{filteredEpisodes.length === 0 ? <p className="muted">No episode metadata is available for this database.</p> : filteredEpisodes.map((episode) => { const episodeId = String(episode.episode_id); const episodeSpeakers = Array.isArray(episode.speakers) ? episode.speakers.map(String).join(', ') : ''; return <div className="episode-row" key={episodeId}><label><input type="checkbox" checked={!excludedEpisodes.includes(episodeId)} onChange={() => toggleExcluded(episodeId)} /><span><strong>{String(episode.title ?? episodeId)}</strong><small>{episodeId} · {String(episode.document_count ?? 0)} records · {episodeSpeakers || 'shared context'}</small></span></label><input aria-label={`Speaker override for ${episodeId}`} value={(episodeOverrides[episodeId] ?? []).join(', ')} onChange={(event) => setOverride(episodeId, event.target.value)} placeholder="Episode speaker override" /></div> })}</div> : <div className="loading-panel">Loading episode inventory…</div>}
    {content && <p className="muted">{String(content.shared_context_note ?? '')}</p>}
    <button className="button primary" disabled={busy} onClick={() => void save()}>{busy ? 'Saving…' : 'Save content policy'}</button>
  </div>
}

function HistoryPanel({ api, db }: { api: AppClient; db: DatabaseRecord }) {
  const [history, setHistory] = useState<Record<string, unknown> | null>(null)
  useEffect(() => { void api.getDatabaseHistory(db.id).then(setHistory).catch(() => setHistory(null)) }, [api, db.id])
  if (!history) return <div className="loading-panel">Loading version history…</div>
  const jobs = Array.isArray(history.jobs) ? history.jobs as Array<Record<string, unknown>> : []
  const summary = history.metadata_summary as Record<string, unknown> | undefined
  return <div className="placeholder-panel"><h3>Version history</h3><div className="detail-grid"><div><span className="field-label">Active state</span><Status tone={history.active_state === 'active' ? 'good' : 'danger'}>{String(history.active_state ?? 'unknown')}</Status></div><div><span className="field-label">Active database release</span><code>{String(history.active_release_id ?? 'Not managed')}</code></div><div><span className="field-label">Source release</span><code>{String(history.source_release_id ?? 'Not declared')}</code></div><div><span className="field-label">Stored episodes</span><strong>{String(summary?.episodes ?? 'Unknown')}</strong></div></div>{jobs.length === 0 ? <p className="muted">No cataloged operations have been recorded yet.</p> : <ul className="event-log">{jobs.map((job) => <li key={String(job.id)}><span>{String(job.kind)}</span>{String(job.state)} · {String(job.completed_at ?? job.created_at)}</li>)}</ul>}<div className="info-box"><strong>Release administration — CLI-only</strong><p className="muted">Promotion, rollback, and pruning require an exact validated plan and are not exposed as unsupported GUI actions.</p><code>conda run -n chroma-db-import python -m chroma_db_import.release_cli status --store &lt;release-store&gt;</code></div></div>
}

function DatabaseSettingsPanel({ api, db, onSaved }: { api: AppClient; db: DatabaseRecord; onSaved: () => Promise<void> }) {
  const [name, setName] = useState(db.display_name)
  const [sourcePath, setSourcePath] = useState(String(db.source_ref.path ?? ''))
  const [sourceRoot, setSourceRoot] = useState(String(db.source_ref.source_root ?? ''))
  const [partition, setPartition] = useState(String(db.source_ref.partition_id ?? ''))
  const [assetFilter, setAssetFilter] = useState(db.selection_policy.asset_filter)
  const [assetPattern, setAssetPattern] = useState(db.selection_policy.asset_pattern)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const save = async () => {
    if (!name.trim() || (assetFilter === 'custom' && !assetPattern.trim())) return
    setBusy(true)
    setSaved(false)
    try {
      if (name.trim() !== db.display_name) await api.renameDatabase(db.id, name.trim())
      const source_ref = db.source_kind === 'managed'
        ? { ...db.source_ref, source_root: sourceRoot, partition_id: partition }
        : { ...db.source_ref, path: sourcePath }
      const selection_policy = { ...db.selection_policy, asset_filter: assetFilter, asset_pattern: assetPattern }
      if (JSON.stringify(source_ref) !== JSON.stringify(db.source_ref) || JSON.stringify(selection_policy) !== JSON.stringify(db.selection_policy)) {
        await api.updateDatabaseSettings(db.id, { source_ref, selection_policy })
      }
      await onSaved()
      setSaved(true)
    } finally { setBusy(false) }
  }
  const sourceValue = db.source_kind === 'managed' ? sourceRoot : sourcePath
  return <div className="placeholder-panel"><h3>Database settings</h3><p className="muted">These values are scoped to this database. Changing the display name never changes its target path or vector identity. Source and selection changes increment the database revision and require a fresh review.</p><label className="field"><span>Display name</span><input value={name} onChange={(event) => setName(event.target.value)} /></label>{db.source_kind === 'managed' ? <><label className="field"><span>Managed source root</span><input value={sourceRoot} onChange={(event) => setSourceRoot(event.target.value)} /></label><label className="field"><span>Partition ID</span><input value={partition} onChange={(event) => setPartition(event.target.value)} /></label></> : <label className="field"><span>Processed source folder</span><input value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} /></label>}<label className="field"><span>Asset filter</span><select value={assetFilter} onChange={(event) => setAssetFilter(event.target.value)}><option value="reviewed_speaker_transcript">Reviewed speaker transcripts</option><option value="cleaned_speaker_transcript">Cleaned speaker transcripts</option><option value="speaker_transcript">Raw speaker transcripts</option><option value="all">All processed assets</option><option value="custom">Custom pattern</option></select></label>{assetFilter === 'custom' && <label className="field"><span>Custom filename pattern</span><input value={assetPattern} onChange={(event) => setAssetPattern(event.target.value)} placeholder="*reviewed*.json" /></label>}<div className="detail-grid"><div><span className="field-label">Source</span><code>{sourceValue || 'Unknown'}</code></div><div><span className="field-label">Fixed target</span><code>{db.target.path}</code></div><div><span className="field-label">Representation</span><code>{String(db.downstream_identity?.representation_id ?? 'Unknown')}</code></div><div><span className="field-label">Scope</span><strong>This database</strong></div></div><button className="button primary" disabled={busy || !name.trim() || (assetFilter === 'custom' && !assetPattern.trim())} onClick={() => void save()}>{busy ? 'Saving…' : 'Save database settings'}</button>{saved && <p className="info-box" role="status">Saved. Review the database again before applying an import-affecting change.</p>}</div>
}

function Create({ api, onCancel, onUseExisting, onDone, setError }: { api: AppClient; onCancel: () => void; onUseExisting: (targetPath: string, sourcePath?: string) => void; onDone: () => Promise<void>; setError: (v: string | null) => void }) {
  const [step, setStep] = useState(1)
  const [sourceKind, setSourceKind] = useState<'folder' | 'managed'>('folder')
  const [source, setSource] = useState('')
  const [partition, setPartition] = useState('')
  const [name, setName] = useState('')
  const [target, setTarget] = useState('')
  const [assetFilter, setAssetFilter] = useState('reviewed_speaker_transcript')
  const [assetPattern, setAssetPattern] = useState('')
  const [speakerMode, setSpeakerMode] = useState<SelectionPolicy['speaker_mode']>('all')
  const [allowlistSpeakers, setAllowlistSpeakers] = useState('')
  const [excludedSpeakers, setExcludedSpeakers] = useState('')
  const [excludedEpisodeIds, setExcludedEpisodeIds] = useState<string[]>([])
  const [episodeOverrides, setEpisodeOverrides] = useState<Record<string, string[]>>({})
  const [episodeSearch, setEpisodeSearch] = useState('')
  const [draftId, setDraftId] = useState<string>(() => window.localStorage.getItem('chroma-gui-create-draft') || '')
  const [scan, setScan] = useState<Record<string, unknown> | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [environment, setEnvironment] = useState<Record<string, unknown> | null>(null)
  const [busy, setBusy] = useState(false)
  const scanRevision = useRef(0)
  const draftIdRef = useRef(draftId)
  const draftSaveQueue = useRef<Promise<void>>(Promise.resolve())
  const restoreDraft = useRef(Boolean(draftId))
  const [targetExists, setTargetExists] = useState(false)

  useEffect(() => { draftIdRef.current = draftId }, [draftId])

  const draftPayload = () => ({
    source_kind: sourceKind,
    source_ref: sourceKind === 'managed' ? { source_root: source, partition_id: partition } : { path: source },
    display_name: name || 'New database',
    target: { path: target },
    selection_policy: {
      speaker_mode: speakerMode,
      excluded_speakers: excludedSpeakers.split(',').map((item) => item.trim()).filter(Boolean),
      allowlist_speakers: allowlistSpeakers.split(',').map((item) => item.trim()).filter(Boolean),
      episode_overrides: episodeOverrides,
      excluded_episode_ids: excludedEpisodeIds,
      asset_filter: assetFilter,
      asset_pattern: assetPattern,
    },
  })

  const persistDraft = () => {
    const payload = draftPayload()
    const queued = draftSaveQueue.current.then(async () => {
      const saved = await api.saveDraft(payload, draftIdRef.current || undefined)
      const nextDraftId = String(saved.id || '')
      if (!nextDraftId) throw new Error('The creation draft could not be saved.')
      draftIdRef.current = nextDraftId
      setDraftId(nextDraftId)
      window.localStorage.setItem('chroma-gui-create-draft', nextDraftId)
    })
    draftSaveQueue.current = queued.catch(() => undefined)
    return queued
  }

  useEffect(() => { if (!draftId || !restoreDraft.current) return; restoreDraft.current = false; void api.getDraft(draftId).then((draft) => { const payload = draft.payload as Record<string, unknown>; const reference = payload.source_ref as Record<string, unknown> | undefined; const policy = payload.selection_policy as Partial<SelectionPolicy> | undefined; setSourceKind(payload.source_kind === 'managed' ? 'managed' : 'folder'); setSource(String(reference?.path ?? reference?.source_root ?? '')); setPartition(String(reference?.partition_id ?? '')); setName(String(payload.display_name ?? '')); setTarget(String((payload.target as Record<string, unknown> | undefined)?.path ?? '')); setSpeakerMode(policy?.speaker_mode === 'allowlist' ? 'allowlist' : 'all'); setAllowlistSpeakers(Array.isArray(policy?.allowlist_speakers) ? policy.allowlist_speakers.join(', ') : ''); setExcludedSpeakers(Array.isArray(policy?.excluded_speakers) ? policy.excluded_speakers.join(', ') : ''); setExcludedEpisodeIds(Array.isArray(policy?.excluded_episode_ids) ? policy.excluded_episode_ids.map(String) : []); setEpisodeOverrides(policy?.episode_overrides && typeof policy.episode_overrides === 'object' ? policy.episode_overrides : {}); setAssetFilter(String(policy?.asset_filter ?? 'reviewed_speaker_transcript')); setAssetPattern(String(policy?.asset_pattern ?? '')) }).catch(() => window.localStorage.removeItem('chroma-gui-create-draft')) }, [api, draftId])
  useEffect(() => { if (step !== 2 || environment !== null) return; void api.environmentReport().then(setEnvironment).catch(() => setEnvironment({})) }, [api, step, environment])

  const scanSource = async (kind: 'folder' | 'managed', value: string, selectedPartition = partition) => {
    const revision = ++scanRevision.current
    setScan(null)
    try {
      const payload = kind === 'managed' ? { source_kind: kind, source_root: value, partition_id: selectedPartition } : { source_kind: kind, path: value, selection_policy: draftPayload().selection_policy }
      const job = await api.scanSource(payload)
      const complete = await waitForJob(api, job.id)
      if (complete.error) throw new Error(complete.error.message)
      if (revision === scanRevision.current && complete.result) { setScan(complete.result); setScanNeedsRefresh(false) }
    } catch (exc) { if (revision === scanRevision.current) setError(exc instanceof Error ? exc.message : 'Source scan failed.') }
  }
  const chooseFolder = async () => { try { const picked = await api.pickFolder(); if (picked) { setSource(picked); await scanSource('folder', picked) } } catch (exc) { setError(exc instanceof Error ? exc.message : 'Folder selection failed.') } }
  const chooseTarget = async () => { try { const picked = await api.pickFolder(); if (picked) { setTarget(picked); setTargetExists(false) } } catch (exc) { setError(exc instanceof Error ? exc.message : 'Destination selection failed.') } }
  const makePreview = async () => {
    setBusy(true)
    try {
      await persistDraft()
      const nextDraftId = draftIdRef.current
      if (!nextDraftId) throw new Error('The creation draft could not be saved.')
      const job = await api.createPreview({ draft_id: nextDraftId, operation: 'create' })
      const complete = await waitForJob(api, job.id)
      if (!complete.result?.preview_id) {
        if (complete.error?.code === 'TARGET_EXISTS') setTargetExists(true)
        throw new Error(complete.error?.message ?? 'The review job did not return a preview.')
      }
      setPreview(await api.getPreview(String(complete.result.preview_id)))
    } catch (exc) {
      if (exc instanceof ClientError && exc.detail.code === 'TARGET_EXISTS') setTargetExists(true)
      setError(exc instanceof Error ? exc.message : 'Could not prepare review.')
    } finally { setBusy(false) }
  }
  const apply = async () => { if (!preview) return; setBusy(true); try { await api.applyPreview(preview.preview_id, preview.required_acknowledgments); window.localStorage.removeItem('chroma-gui-create-draft'); await onDone() } catch (exc) { setError(exc instanceof Error ? exc.message : 'Could not create database.') } finally { setBusy(false) } }
  const goBack = () => { void persistDraft(); setStep((current) => current - 1) }
  const ready = sourceKind === 'folder' ? Boolean(source) : Boolean(source && partition)
  const invalidateScan = () => { setPreview(null); setScanNeedsRefresh(true) }
  const toggleExcludedEpisode = (episodeId: string) => { setExcludedEpisodeIds((current) => current.includes(episodeId) ? current.filter((item) => item !== episodeId) : [...current, episodeId].sort()); invalidateScan() }
  const setEpisodeOverride = (episodeId: string, value: string) => { setEpisodeOverrides((current) => ({ ...current, [episodeId]: value.split(',').map((item) => item.trim()).filter(Boolean) })); invalidateScan() }
  const episodeInventory = Array.isArray(scan?.episode_inventory) ? scan.episode_inventory as Array<Record<string, unknown>> : []
  const filteredInventory = episodeInventory.filter((episode) => `${String(episode.title ?? '')} ${String(episode.episode_id ?? '')}`.toLowerCase().includes(episodeSearch.toLowerCase()))
  const selectedEpisodeCount = episodeInventory.filter((episode) => !excludedEpisodeIds.includes(String(episode.episode_id))).length
  const dateRange = scan?.date_range as { start?: unknown; end?: unknown } | undefined
  const excludedFileCount = Array.isArray(scan?.excluded_files) ? scan.excluded_files.length : 0
  const [scanNeedsRefresh, setScanNeedsRefresh] = useState(false)
  const scanReady = !scanNeedsRefresh && (sourceKind === 'managed' ? scan?.ready_to_publish === true : Boolean(scan))
  const cuda = environment?.cuda && typeof environment.cuda === 'object' ? environment.cuda as { available?: unknown; device_count?: unknown } : null
  const readinessTone = environment === null ? 'neutral' : cuda?.available === true ? 'good' : 'warning'
  const readinessText = environment === null ? 'Checking device readiness…' : cuda?.available === true ? `${String(cuda.device_count ?? 1)} CUDA device(s) available` : 'CUDA readiness needs review; open Environment for supported options.'
  if (targetExists) return <section className="flow">
    <header className="page-header"><div><p className="eyebrow">CREATE DATABASE</p><h1>Destination already exists</h1><p className="muted">Creation never overwrites an existing database. Choose another folder or register the existing database.</p></div></header>
    <div className="card flow-card"><div className="info-box"><strong>{target}</strong><small>The existing database has not been changed.</small></div><div className="button-row"><button className="button secondary" onClick={() => { setTargetExists(false); setStep(2) }}>Choose another location</button><button className="button primary" onClick={() => onUseExisting(target, sourceKind === 'folder' ? source : undefined)}>Use existing database</button></div></div>
  </section>
  return <section className="flow">
    <header className="page-header"><div><p className="eyebrow">CREATE DATABASE</p><h1>{step === 1 ? 'Choose content' : step === 2 ? 'Name and location' : 'Review and create'}</h1><p className="muted">A short three-step setup. Your choices are saved as a draft when you go back.</p></div></header>
    <div className="stepper"><span className={step >= 1 ? 'step current' : 'step'}>1 <b>Content</b></span><span className={step >= 2 ? 'step current' : 'step'}>2 <b>Location</b></span><span className={step >= 3 ? 'step current' : 'step'}>3 <b>Review</b></span></div>
    <div className="card flow-card">
      {step === 1 && <>
        <label className="field"><span>Source type</span><select value={sourceKind} onChange={(event) => { const value = event.target.value as 'folder' | 'managed'; setSourceKind(value); setSource(''); setPartition(''); setScan(null); setPreview(null); setScanNeedsRefresh(false); void persistDraft() }}><option value="folder">Processed files folder</option><option value="managed">Podcast-RAG managed source</option></select></label>
        {sourceKind === 'folder' ? <label className="field"><span>Processed folder</span><div className="input-row"><input value={source} onBlur={() => void persistDraft()} onChange={(event) => { setSource(event.target.value); setScan(null); setScanNeedsRefresh(true) }} placeholder="Choose a folder containing processed files" /><button className="button secondary" onClick={() => void chooseFolder()}>Choose folder</button></div></label> : <>
          <label className="field"><span>Podcast-RAG source root</span><input value={source} onBlur={() => void persistDraft()} onChange={(event) => { setSource(event.target.value); setScan(null); setScanNeedsRefresh(true) }} placeholder="C:/…/Podcast RAG" /></label>
          <label className="field"><span>Verified partition ID</span><input value={partition} onBlur={() => void persistDraft()} onChange={(event) => { setPartition(event.target.value); setScan(null); setScanNeedsRefresh(true) }} placeholder="partition-id" /></label>
          <button className="button secondary" disabled={!ready} onClick={() => void scanSource('managed', source, partition)}>Inspect managed source</button>
        </>}
        {sourceKind === 'folder' && <button className="button secondary" disabled={!ready} onClick={() => void scanSource('folder', source)}>Scan source</button>}
        {scan && <div className="scan-result"><Status tone={scanReady ? 'good' : 'warning'}>{scanReady ? 'Source ready' : 'Source needs attention'}</Status>{sourceKind === 'managed' ? <><strong>{String(scan.completed ?? 0)} completed</strong><span>{String(scan.pending ?? 0)} pending</span><span>{String(scan.failed ?? 0)} failed</span><span>{String(scan.quarantined ?? 0)} quarantined</span></> : <><strong>{String(scan.episodes ?? 0)} episodes</strong><span>{String(scan.eligible_records ?? 0)} eligible records</span><span>{Array.isArray(scan.speakers) ? scan.speakers.length : 0} speakers</span><span>{excludedFileCount} excluded files</span>{(dateRange?.start || dateRange?.end) && <span>{String(dateRange?.start || '—')} → {String(dateRange?.end || '—')}</span>}</>}</div>}
        {sourceKind === 'folder' && scan && <details className="content-picker" open><summary>Choose content <span className="muted">{selectedEpisodeCount} of {episodeInventory.length || String(scan.episodes ?? 0)} episodes included</span></summary><label className="field"><span>Search episodes</span><input value={episodeSearch} onChange={(event) => setEpisodeSearch(event.target.value)} placeholder="Title or episode ID" /></label><label className="field"><span>Speaker policy</span><select value={speakerMode} onChange={(event) => { setSpeakerMode(event.target.value as SelectionPolicy['speaker_mode']); invalidateScan() }}><option value="all">All speakers except exclusions</option><option value="allowlist">Only selected speakers</option></select></label><label className="field"><span>Allowlist speakers</span><input value={allowlistSpeakers} onBlur={() => void persistDraft()} onChange={(event) => { setAllowlistSpeakers(event.target.value); invalidateScan() }} placeholder="Host, Guest" /></label><label className="field"><span>Excluded speakers</span><input value={excludedSpeakers} onBlur={() => void persistDraft()} onChange={(event) => { setExcludedSpeakers(event.target.value); invalidateScan() }} placeholder="Ads, Sponsor" /></label>{filteredInventory.length === 0 ? <p className="muted">No episode inventory matches this search.</p> : <div className="episode-table">{filteredInventory.map((episode) => { const episodeId = String(episode.episode_id); const speakers = Array.isArray(episode.speakers) ? episode.speakers.map(String).join(', ') : ''; return <div className="episode-row" key={episodeId}><label><input type="checkbox" checked={!excludedEpisodeIds.includes(episodeId)} onChange={() => toggleExcludedEpisode(episodeId)} /><span><strong>{String(episode.title ?? episodeId)}</strong><small>{episodeId} · {String(episode.document_count ?? 0)} records · {speakers || 'shared context'}</small></span></label><input aria-label={`Speaker override for ${episodeId}`} value={(episodeOverrides[episodeId] ?? []).join(', ')} onBlur={() => void persistDraft()} onChange={(event) => setEpisodeOverride(episodeId, event.target.value)} placeholder="Episode speaker override" /></div> })}</div>}<p className="muted">Shared episode context remains available when at least one selected speaker is included.</p></details>}
        {sourceKind === 'folder' && <label className="field"><span>Asset filter</span><select value={assetFilter} onChange={(event) => { setAssetFilter(event.target.value); invalidateScan() }} onBlur={() => void persistDraft()}><option value="reviewed_speaker_transcript">Reviewed speaker transcripts</option><option value="cleaned_speaker_transcript">Cleaned speaker transcripts</option><option value="speaker_transcript">Raw speaker transcripts</option><option value="all">All processed assets</option></select></label>}
      </>}
      {step === 2 && <>
        <label className="field"><span>Database name</span><input autoFocus value={name} onBlur={() => void persistDraft()} onChange={(event) => setName(event.target.value)} placeholder="e.g. Weekly Podcast" /></label>
        <label className="field"><span>Storage location</span><div className="input-row"><input value={target} onBlur={() => void persistDraft()} onChange={(event) => setTarget(event.target.value)} placeholder="Choose a final database destination" /><button className="button secondary" onClick={() => void chooseTarget()}>Choose folder</button></div></label>
        <div className="info-box"><span>Embedding profile</span><strong>Qwen3 Embedding 4B</strong><small><Status tone={readinessTone}>{readinessText}</Status> Device changes affect speed; a representation change requires a separate database.</small></div>
        <details><summary>Technical details</summary><p className="muted">IDs and collection names are generated by the Python service and are not derived from the display name. Existing destinations are never overwritten; use Add existing to register one.</p></details>
      </>}
      {step === 3 && <>{preview ? <><div className="review-heading"><div><Status tone={preview.validation_findings.length ? 'warning' : 'good'}>{preview.validation_findings.length ? 'Review warnings' : 'Ready to create'}</Status><h2>{name || 'New database'}</h2><p className="muted">{source} → {preview.target_identity.path}</p></div></div><div className="metric-grid"><Metric label="Episodes" value={preview.effects.episodes_total} /><Metric label="Records" value={preview.effects.records_total} /><Metric label="Writes" value={preview.effects.writes} /></div>{preview.validation_findings.map((finding) => <div className="finding" key={finding.code}>{finding.message}</div>)}</> : <div className="loading-panel">{busy ? 'Preparing review…' : 'Review has not been prepared yet.'}</div>}</>}
    </div>
    <footer className="flow-footer"><button className="button secondary" onClick={step === 1 ? onCancel : goBack}>Back</button>{step < 3 ? <button className="button primary" disabled={(step === 1 && (!ready || !scanReady)) || (step === 2 && (!name || !target))} onClick={() => { if (step === 2) void makePreview(); else void persistDraft(); setStep((current) => current + 1) }}>{step === 2 ? 'Review' : 'Continue'}</button> : <button className="button primary" disabled={!preview || busy || Boolean(preview?.validation_findings.some((item) => item.severity === 'error'))} onClick={() => void apply()}>{busy ? 'Creating…' : 'Create database'}</button>}</footer>
  </section>
}

function AddExisting({ api, initialTarget, initialSource, onCancel, onDone, setError }: { api: AppClient; initialTarget: string; initialSource: string; onCancel: () => void; onDone: () => Promise<void>; setError: (v: string | null) => void }) {
  const [target, setTarget] = useState(initialTarget)
  const [sourceKind, setSourceKind] = useState<'folder' | 'managed'>('folder')
  const [source, setSource] = useState(initialSource)
  const [partition, setPartition] = useState('')
  const [proposal, setProposal] = useState<Record<string, unknown> | null>(null)
  const choose = async (setter: (value: string) => void) => { try { const picked = await api.pickFolder(); if (picked) setter(picked) } catch (exc) { setError(exc instanceof Error ? exc.message : 'Folder selection failed.') } }
  const inspect = async () => {
    try {
      setProposal(await api.inspectExisting({ target_path: target, source_kind: sourceKind, source_path: source, source_root: source, partition_id: partition }))
    } catch (exc) { setError(exc instanceof Error ? exc.message : 'Inspection failed.') }
  }
  const register = async () => { try { await api.registerExisting(proposal ?? {}); await onDone() } catch (exc) { setError(exc instanceof Error ? exc.message : 'Registration failed.') } }
  return <section className="flow">
    <header className="page-header"><div><p className="eyebrow">ADD EXISTING</p><h1>Register a database</h1><p className="muted">Inspection is read-only. Registration never moves or replaces files.</p></div></header>
    <div className="card flow-card">
      <label className="field"><span>Source type</span><select value={sourceKind} onChange={(event) => { setSourceKind(event.target.value as 'folder' | 'managed'); setProposal(null) }}><option value="folder">Processed files folder</option><option value="managed">Podcast-RAG managed source</option></select></label>
      <label className="field"><span>{sourceKind === 'managed' ? 'Managed output or partition folder' : 'Existing database folder'}</span><div className="input-row"><input autoFocus value={target} onChange={(e) => { setTarget(e.target.value); setProposal(null) }} placeholder="C:/…/database" /><button className="button secondary" onClick={() => void choose(setTarget)}>Choose folder</button></div></label>
      {sourceKind === 'managed' ? <><label className="field"><span>Podcast-RAG source root</span><div className="input-row"><input value={source} onChange={(e) => { setSource(e.target.value); setProposal(null) }} placeholder="C:/…/Podcast RAG" /><button className="button secondary" onClick={() => void choose(setSource)}>Choose folder</button></div></label><label className="field"><span>Verified partition ID</span><input value={partition} onChange={(e) => { setPartition(e.target.value); setProposal(null) }} placeholder="partition-id" /></label></> : <label className="field"><span>Processed source folder <small>(required for updates)</small></span><div className="input-row"><input value={source} onChange={(e) => { setSource(e.target.value); setProposal(null) }} placeholder="C:/…/processed_data" /><button className="button secondary" onClick={() => void choose(setSource)}>Choose folder</button></div></label>}
      {proposal && <div className="review-box"><Status tone={proposal.identity_status === 'resolved' ? 'good' : 'warning'}>{String(proposal.identity_status ?? 'Review')}</Status><h3>{String(proposal.display_name ?? 'Existing database')}</h3><code>{String((proposal.target as { path?: string })?.path ?? target)}</code>{(proposal.warnings as string[] | undefined)?.map((warning) => <p key={warning} className="muted">{warning}</p>)}</div>}
    </div>
    <footer className="flow-footer"><button className="button secondary" onClick={onCancel}>Cancel</button>{proposal ? <button className="button primary" onClick={() => void register()}>Add to library</button> : <button className="button primary" disabled={!target || (sourceKind === 'managed' ? !source || !partition : !source)} onClick={() => void inspect()}>Inspect database</button>}</footer>
  </section>
}

function Update({ api, db, preview, setPreview, onBack, onSourceConnections, onRepairIdentity, onCreateSeparate, onApplied, setError }: { api: AppClient; db: DatabaseRecord; preview: Preview | null; setPreview: (p: Preview | null) => void; onBack: () => void; onSourceConnections: () => void; onRepairIdentity: () => void; onCreateSeparate: () => void; onApplied: () => Promise<void>; setError: (v: string | null) => void }) {
  const [busy, setBusy] = useState(false)
  const [editSelection, setEditSelection] = useState(false)
  const [reviewIssue, setReviewIssue] = useState<{ code: string; message: string } | null>(null)
  const reportIssue = (exc: unknown, fallback: string) => {
    const detail = exc instanceof ClientError ? exc.detail : null
    const issue = { code: detail?.code ?? 'JOB_FAILED', message: detail?.message ?? (exc instanceof Error ? exc.message : fallback) }
    setReviewIssue(issue)
    setError(null)
  }
  const refresh = async () => {
    setBusy(true)
    try {
      const job = await api.checkDatabase(db.id)
      const done = await waitForJob(api, job.id)
      if (done.error) throw new ClientError(done.error)
      if (!done.result?.preview_id) throw new Error('The update review did not return a preview.')
      setPreview(await api.getPreview(String(done.result.preview_id)))
      setReviewIssue(null)
    } catch (exc) { setPreview(null); reportIssue(exc, 'Update review failed.') }
    finally { setBusy(false) }
  }
  useEffect(() => { if (!preview) void refresh() }, [])
  const apply = async () => {
    if (!preview) return
    const applicable = preview.operation === 'remove_outdated' ? preview.effects.delete_ids.total > 0 : preview.effects.writes > 0
    if (!applicable) return
    setBusy(true)
    try { await api.applyPreview(preview.preview_id, preview.required_acknowledgments); await onApplied() }
    catch (exc) { reportIssue(exc, 'Update failed.') }
    finally { setBusy(false) }
  }
  const prepareRemoval = async () => {
    if (!preview || preview.effects.retained_missing_ids.total === 0) return
    setBusy(true)
    try { setPreview(await api.createMaintenancePreview(db.id, preview.effects.retained_missing_ids.items)); setReviewIssue(null) }
    catch (exc) { reportIssue(exc, 'Maintenance review failed.') }
    finally { setBusy(false) }
  }
  const reviewSelection = async (selectionPolicy: SelectionPolicy) => {
    setBusy(true)
    try {
      const job = await api.createPreview({ database_id: db.id, operation: 'update', selection_policy: selectionPolicy })
      const done = await waitForJob(api, job.id)
      if (done.error) throw new ClientError(done.error)
      if (!done.result?.preview_id) throw new Error('The selected-content review could not be prepared.')
      setPreview(await api.getPreview(String(done.result.preview_id)))
      setEditSelection(false)
      setReviewIssue(null)
    } catch (exc) { reportIssue(exc, 'Selected-content review failed.') }
    finally { setBusy(false) }
  }
  const removal = preview?.operation === 'remove_outdated'
  const applicable = Boolean(preview && (removal ? preview.effects.delete_ids.total : preview.effects.writes))
  const hasBlockingFinding = Boolean(reviewIssue || preview?.validation_findings.some((finding) => finding.severity === 'error'))
  const storedProfile = String(db.downstream_identity?.profile ?? db.target.representation_profile ?? 'Unknown')
  const previewProfile = String(preview?.representation?.profile ?? preview?.representation?.representation_id ?? 'Unknown')
  const sourceDescription = db.source_kind === 'managed'
    ? `Podcast-RAG · ${String(db.source_ref.partition_id ?? db.source_ref.corpus_id ?? 'managed source')}`
    : String(db.source_ref.path ?? 'Processed source folder')
  return <section className="flow">
    <header className="page-header"><div><p className="eyebrow">{removal ? 'MAINTENANCE' : 'UPDATE'}</p><h1>{db.display_name}</h1><p className="muted">{removal ? 'Review the exact records selected for removal before applying maintenance.' : 'The saved source, target, and embedding profile are being checked.'}</p></div></header>
    {editSelection && !removal ? <UpdateSelectionEditor api={api} db={db} busy={busy} onCancel={() => setEditSelection(false)} onReview={reviewSelection} /> : <div className="card flow-card">
      {reviewIssue && <ReviewIssue issue={reviewIssue} onRefresh={() => void refresh()} onSourceConnections={onSourceConnections} onRepairIdentity={onRepairIdentity} onCreateSeparate={onCreateSeparate} onBack={onBack} />}
      {busy && !preview ? <div className="loading-panel"><span className="spinner" /> Checking source…</div> : preview ? <>
        <div className="review-heading"><div><Status tone={removal ? 'danger' : applicable ? 'warning' : 'good'}>{removal ? 'Removal review' : applicable ? 'Changes found' : 'Up to date'}</Status><h2>{removal ? 'Remove outdated records' : applicable ? 'Review detected changes' : 'No changes to apply'}</h2><p className="muted">Target: {preview.target_identity.path}</p></div><div className="button-row"><button className="button secondary" onClick={() => void refresh()}>Refresh review</button>{!removal && db.source_kind === 'folder' && <button className="button secondary" onClick={() => setEditSelection(true)}>Change content for this update</button>}</div></div>
        <div className="detail-grid"><div><span className="field-label">Source</span><code>{sourceDescription}</code></div><div><span className="field-label">Exact target</span><code>{String(preview.target_identity.path ?? db.target.path)}</code></div><div><span className="field-label">Stored profile</span><strong>{storedProfile}</strong></div><div><span className="field-label">Review profile compatibility</span><strong>{storedProfile === previewProfile ? `Compatible · ${previewProfile}` : `Mismatch · ${previewProfile}`}</strong></div></div>
        <div className="metric-grid"><Metric label="Episodes" value={preview.effects.episodes_total} /><Metric label={removal ? 'Records selected for removal' : 'Records to write'} value={removal ? preview.effects.delete_ids.total : preview.effects.writes} /><Metric label="Retained missing" value={preview.effects.retained_missing_ids.total} /></div>
        <EffectSummary effects={preview.effects} />
        {!removal && preview.effects.retained_missing_ids.total > 0 && <div className="info-box">Records absent from the current source are retained by ordinary Update. <button className="button small" disabled={busy} onClick={() => void prepareRemoval()}>Review removal of these records</button></div>}
        {!removal && db.source_kind === 'folder' && <p className="muted">Content changes here apply to this review only. Save a new default under the database’s Content tab if you want it remembered.</p>}
        {removal && <><div className="finding">This explicit maintenance preview is bound to {preview.effects.delete_ids.total} exact record ID(s). Ordinary Update never deletes retained records.</div><ExactRemovalDetails preview={preview} /></>}
        {preview.validation_findings.map((finding) => <PreviewFinding finding={finding} key={finding.code} onSourceConnections={onSourceConnections} onRepairIdentity={onRepairIdentity} onCreateSeparate={onCreateSeparate} onRefresh={() => void refresh()} />)}
      </> : <div className="loading-panel">Preparing review…</div>}
    </div>}
    <footer className="flow-footer"><button className="button secondary" onClick={onBack}>Back</button>{applicable && !hasBlockingFinding && <button className={removal ? 'button danger' : 'button primary'} disabled={busy} onClick={() => void apply()}>{busy ? 'Applying…' : removal ? 'Remove accepted records' : 'Apply update'}</button>}</footer>
  </section>
}

function ReviewIssue({ issue, onRefresh, onSourceConnections, onRepairIdentity, onCreateSeparate, onBack }: { issue: { code: string; message: string }; onRefresh: () => void; onSourceConnections: () => void; onRepairIdentity: () => void; onCreateSeparate: () => void; onBack: () => void }) {
  const action = issue.code === 'PREVIEW_STALE'
    ? <button className="button secondary" onClick={onRefresh}>Prepare a fresh review</button>
    : issue.code === 'SOURCE_UNAVAILABLE' || issue.code === 'SOURCE_INVALID'
      ? <button className="button secondary" onClick={onSourceConnections}>Open Source connections</button>
      : issue.code === 'IDENTITY_UNRESOLVED'
        ? <button className="button secondary" onClick={onRepairIdentity}>Repair through Add existing</button>
        : issue.code === 'PROFILE_MISMATCH'
          ? <button className="button secondary" onClick={onCreateSeparate}>Create separate database</button>
          : <button className="button secondary" onClick={onBack}>Return to database</button>
  return <div className="finding" role="alert"><strong>{reviewIssueTitle(issue.code)}</strong><p>{issue.message}</p><p className="muted">The active database has not been changed. {reviewIssueAction(issue.code)}</p><div className="button-row">{action}</div></div>
}

function PreviewFinding({ finding, onSourceConnections, onRepairIdentity, onCreateSeparate, onRefresh }: { finding: { severity: string; code: string; message: string }; onSourceConnections: () => void; onRepairIdentity: () => void; onCreateSeparate: () => void; onRefresh: () => void }) {
  const action = finding.code === 'SOURCE_UNAVAILABLE' || finding.code === 'SOURCE_INVALID'
    ? <button className="button small" onClick={onSourceConnections}>Open Source connections</button>
    : finding.code === 'IDENTITY_UNRESOLVED'
      ? <button className="button small" onClick={onRepairIdentity}>Repair through Add existing</button>
      : finding.code === 'PROFILE_MISMATCH'
        ? <button className="button small" onClick={onCreateSeparate}>Create separate database</button>
        : finding.code === 'PREVIEW_STALE'
          ? <button className="button small" onClick={onRefresh}>Prepare a fresh review</button>
          : null
  return <div className="finding"><strong>{findingTitle(finding.code, finding.severity)}</strong><p>{finding.message}</p>{action && <div className="button-row">{action}</div>}</div>
}

function reviewIssueTitle(code: string) {
  return ({
    PREVIEW_STALE: 'This review is out of date',
    SOURCE_UNAVAILABLE: 'The source is unavailable',
    SOURCE_INVALID: 'The source needs repair',
    IDENTITY_UNRESOLVED: 'Database identity needs repair',
    PROFILE_MISMATCH: 'The embedding profile is not compatible',
  } as Record<string, string>)[code] ?? 'The update needs attention'
}

function reviewIssueAction(code: string) {
  return ({
    PREVIEW_STALE: 'Prepare a fresh review before applying anything.',
    SOURCE_UNAVAILABLE: 'Reconnect the source or choose its new location.',
    SOURCE_INVALID: 'Inspect the source connection and resolve the reported issue.',
    IDENTITY_UNRESOLVED: 'Re-inspect the database and confirm its recorded identity before updating.',
    PROFILE_MISMATCH: 'Build a separate database with the required profile; this target remains unchanged.',
  } as Record<string, string>)[code] ?? 'Review the details and choose the supported next action.'
}

function findingTitle(code: string, severity: string) {
  if (code === 'MANAGED_RETENTION_UNSUPPORTED') return 'Routine update is blocked'
  if (code === 'NO_ELIGIBLE_CONTENT') return 'No eligible content was found'
  if (code === 'PROFILE_MISMATCH') return 'Create a separate database for this profile'
  return severity === 'error' ? 'Blocking review finding' : 'Review finding'
}

function UpdateSelectionEditor({ api, db, busy, onCancel, onReview }: { api: AppClient; db: DatabaseRecord; busy: boolean; onCancel: () => void; onReview: (policy: SelectionPolicy) => Promise<void> }) {
  const [policy, setPolicy] = useState<SelectionPolicy>(db.selection_policy)
  const [content, setContent] = useState<Record<string, unknown> | null>(null)
  const [search, setSearch] = useState('')
  useEffect(() => { void api.getDatabaseContent(db.id).then(setContent).catch(() => setContent(null)) }, [api, db.id])
  const episodes = (Array.isArray(content?.episodes) ? content.episodes : []) as Array<Record<string, unknown>>
  const filteredEpisodes = episodes.filter((episode) => `${String(episode.title ?? '')} ${String(episode.episode_id ?? '')}`.toLowerCase().includes(search.toLowerCase()))
  const update = (changes: Partial<SelectionPolicy>) => setPolicy((current) => ({ ...current, ...changes }))
  const toggleEpisode = (episodeId: string) => update({ excluded_episode_ids: policy.excluded_episode_ids.includes(episodeId) ? policy.excluded_episode_ids.filter((item) => item !== episodeId) : [...policy.excluded_episode_ids, episodeId].sort() })
  const setOverride = (episodeId: string, value: string) => update({ episode_overrides: { ...policy.episode_overrides, [episodeId]: value.split(',').map((item) => item.trim()).filter(Boolean) } })
  return <section className="card flow-card"><div className="review-heading"><div><p className="eyebrow">ONE-RUN REVIEW</p><h2>Change content for this update</h2><p className="muted">This selection is attached to the new preview only. It will not change the saved database policy.</p></div></div><label className="field"><span>Search episodes</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Title or episode ID" /></label><label className="field"><span>Speaker policy</span><select value={policy.speaker_mode} onChange={(event) => update({ speaker_mode: event.target.value as SelectionPolicy['speaker_mode'] })}><option value="all">All speakers except exclusions</option><option value="allowlist">Only selected speakers</option></select></label><label className="field"><span>Allowlist speakers</span><input value={policy.allowlist_speakers.join(', ')} onChange={(event) => update({ allowlist_speakers: event.target.value.split(',').map((item) => item.trim()).filter(Boolean) })} placeholder="Host, Guest" /></label><label className="field"><span>Excluded speakers</span><input value={policy.excluded_speakers.join(', ')} onChange={(event) => update({ excluded_speakers: event.target.value.split(',').map((item) => item.trim()).filter(Boolean) })} placeholder="Ads, Sponsor" /></label><label className="field"><span>Asset filter</span><select value={policy.asset_filter} onChange={(event) => update({ asset_filter: event.target.value })}><option value="reviewed_speaker_transcript">Reviewed speaker transcripts</option><option value="cleaned_speaker_transcript">Cleaned speaker transcripts</option><option value="speaker_transcript">Raw speaker transcripts</option><option value="all">All processed assets</option><option value="custom">Custom pattern</option></select></label>{policy.asset_filter === 'custom' && <label className="field"><span>Custom filename pattern</span><input value={policy.asset_pattern} onChange={(event) => update({ asset_pattern: event.target.value })} placeholder="*reviewed*.json" /></label>}<div className="field-label">Episodes</div>{content === null ? <div className="loading-panel">Loading episode inventory…</div> : filteredEpisodes.length === 0 ? <p className="muted">No episode metadata is available for this database.</p> : <div className="episode-table">{filteredEpisodes.map((episode) => { const episodeId = String(episode.episode_id); const speakers = Array.isArray(episode.speakers) ? episode.speakers.map(String).join(', ') : ''; return <div className="episode-row" key={episodeId}><label><input type="checkbox" checked={!policy.excluded_episode_ids.includes(episodeId)} onChange={() => toggleEpisode(episodeId)} /><span><strong>{String(episode.title ?? episodeId)}</strong><small>{episodeId} · {String(episode.document_count ?? 0)} records · {speakers || 'shared context'}</small></span></label><input aria-label={`Speaker override for ${episodeId}`} value={(policy.episode_overrides[episodeId] ?? []).join(', ')} onChange={(event) => setOverride(episodeId, event.target.value)} placeholder="Episode speaker override" /></div> })}</div>}<p className="muted">Shared episode context remains available when at least one selected speaker is included.</p><div className="button-row"><button className="button secondary" disabled={busy} onClick={onCancel}>Cancel</button><button className="button primary" disabled={busy || content === null} onClick={() => void onReview(policy)}>{busy ? 'Preparing review…' : 'Review this selection'}</button></div></section>
}

function Metric({ label, value }: { label: string; value: number }) { return <div className="metric"><strong>{value}</strong><span>{label}</span></div> }
function EffectSummary({ effects }: { effects: Preview['effects'] }) {
  const items = [
    ['Insert', effects.insert_ids.total],
    ['Replace', effects.replace_ids.total],
    ['Metadata only', effects.metadata_only_ids.total],
    ['Unchanged', effects.unchanged_ids.total],
    ['Retained', effects.retained_missing_ids.total],
    ['Delete', effects.delete_ids.total],
  ] as const
  return <div className="effect-grid" aria-label="Planned record effects">{items.map(([label, value]) => <div className="effect-item" key={label}><strong>{value}</strong><span>{label}</span></div>)}</div>
}
function ExactRemovalDetails({ preview }: { preview: Preview }) {
  const ids = preview.effects.delete_ids.items
  const reasons = preview.effects.reasons
  return <details className="review-box" open><summary>Exact records and reasons ({ids.length})</summary><DeleteEpisodeScope episodes={preview.effects.delete_episodes ?? []} />{ids.length === 0 ? <p className="muted">No records are selected.</p> : <ul className="event-log">{ids.map((id) => <li key={id}><code>{id}</code> — {reasons[id] ?? 'Explicitly accepted as outdated during maintenance review.'}</li>)}</ul>}</details>
}
function DeleteEpisodeScope({ episodes }: { episodes: Array<Record<string, unknown>> }) {
  if (episodes.length === 0) return null
  return <p className="muted">Affected episodes: {episodes.map((episode) => { const ids = Array.isArray(episode.record_ids) ? episode.record_ids : []; return `${String(episode.title ?? episode.episode_id ?? 'Unknown episode')} (${ids.length || String(episode.records ?? 0)} records)` }).join(', ')}</p>
}
function JobResultSummary({ result }: { result: Record<string, unknown> }) {
  const metrics = [['Writes', 'actual_writes'], ['Imported episodes', 'imported_episodes'], ['Skipped episodes', 'skipped_episodes'], ['Metadata-only records', 'metadata_only_records'], ['Retained records', 'retained_records']].filter(([, key]) => result[key] !== undefined)
  const warnings = Array.isArray(result.warnings) ? result.warnings.map(String) : []
  const activeState = result.active_database_state
  if (metrics.length === 0 && warnings.length === 0 && activeState === undefined) return null
  return <div className="review-box" role="region" aria-label="Operation result summary">{activeState !== undefined && <p><strong>Active database state:</strong> {String(activeState)}</p>}{metrics.length > 0 && <div className="detail-grid">{metrics.map(([label, key]) => <div key={key}><span className="field-label">{label}</span><strong>{String(result[key])}</strong></div>)}</div>}{warnings.length > 0 && <p className="muted">Warnings: {warnings.join(' ')}</p>}</div>
}
function Activity({ jobs, api, onRefresh, onViewDatabase }: { jobs: JobRecord[]; api: AppClient; onRefresh: () => Promise<void>; onViewDatabase: (id: string) => void }) {
  const [reports, setReports] = useState<Record<string, string>>({})
  const cancel = async (id: string) => { try { await api.cancelJob(id); await onRefresh() } catch { /* the job may have started between render and click */ } }
  const retry = async (job: JobRecord) => { try { await api.retryJob(job.id); setReports((current) => ({ ...current, [job.id]: 'A fresh review was queued. Apply it only after checking the new results.' })); await onRefresh() } catch { /* the operation may have changed state between render and click */ } }
  const exportReport = async (job: JobRecord) => { try { const result = await api.exportReport({ job, result: job.result, error: job.error }); setReports((current) => ({ ...current, [job.id]: String(result.path ?? result.report_id ?? 'Report exported') })) } catch { /* report export is best effort and never changes active data */ } }
  const openFolder = async (job: JobRecord) => { if (!job.database_id) return; try { const path = await api.openDatabaseFolder(job.database_id); setReports((current) => ({ ...current, [job.id]: `Opened ${path}` })) } catch { /* the registered target may be unavailable */ } }
  return <><header className="page-header"><div><p className="eyebrow">WORK HISTORY</p><h1>Activity</h1><p className="muted">Imports, source actions, and review jobs remain available after navigation or restart.</p></div></header>{jobs.length === 0 ? <Empty title="No activity yet" body="Your database checks and imports will appear here." /> : <section className="activity-list">{jobs.map((job) => <article className="card activity-item" key={job.id}><div className="activity-title"><Status tone={job.state === 'succeeded' ? 'good' : job.state === 'failed' ? 'danger' : 'warning'}>{job.state.replaceAll('_', ' ')}</Status><strong>{job.kind === 'import' ? 'Database import' : job.kind === 'scan' ? 'Source scan' : job.kind === 'source' ? 'Source action' : 'Database review'}</strong><time>{job.created_at}</time></div><p>{job.error?.message ?? (job.result?.output ? String(job.result.output) : `Stage: ${job.stage}`)}</p>{job.error?.active_database_state && <p className="muted">Active database state: {job.error.active_database_state}</p>}{Boolean(job.result?.output) && <p><code>{String(job.result?.output)}</code></p>}{job.result && <JobResultSummary result={job.result as Record<string, unknown>} />}<div className="button-row">{job.database_id && <button className="button small" onClick={() => onViewDatabase(job.database_id as string)}>View database</button>}{job.database_id && <button className="button small" onClick={() => void openFolder(job)}>Open database folder</button>}{Boolean(job.result) && <button className="button small" onClick={() => void exportReport(job)}>Export report</button>}{['failed', 'interrupted'].includes(job.state) && <button className="button small" onClick={() => void retry(job)}>Retry with fresh review</button>}{job.state === 'queued' && job.can_cancel && <button className="button small" onClick={() => void cancel(job.id)}>Cancel queued operation</button>}</div>{reports[job.id] && <p className="muted">{reports[job.id]}</p>}<details><summary>View activity log</summary><JobEvents api={api} id={job.id} /></details></article>)}</section>}</>
}
function JobEvents({ api, id }: { api: AppClient; id: string }) { const [events, setEvents] = useState<Array<Record<string, unknown>>>([]); useEffect(() => { void api.getJobEvents(id).then(setEvents).catch(() => undefined) }, [id]); return <ul className="event-log">{events.map((event) => <li key={String(event.sequence)}><span>{String(event.stage)}</span>{String(event.message)}</li>)}</ul> }
function Advanced({ api, selected, onPrepared }: { api: AppClient; selected: DatabaseRecord | null; onPrepared: (db: DatabaseRecord) => void }) {
  const [root, setRoot] = useState('')
  const [partition, setPartition] = useState('')
  const [status, setStatus] = useState<string | null>(null)
  const [sourceReport, setSourceReport] = useState<Record<string, unknown> | null>(null)

  useEffect(() => {
    setRoot(String(selected?.source_ref.source_root ?? ''))
    setPartition(String(selected?.source_ref.partition_id ?? ''))
    setSourceReport(null)
    setStatus(null)
  }, [selected])

  const action = async (name: string) => {
    setStatus(null)
    setSourceReport(null)
    try {
      const job = await api.startSourceAction({ source_root: root, partition_id: partition, action: name, database_id: selected?.id })
      const complete = await waitForJob(api, job.id)
      if (complete.error) throw new Error(complete.error.message)
      const result = complete.result
      const report = result && typeof result.status === 'object' && result.status !== null
        ? result.status as Record<string, unknown>
        : result
      setSourceReport(report)
      setStatus(`${name === 'prepare' ? 'Source release prepared' : `Completed ${name}`}: ${complete.id}`)
      if (name === 'prepare' && selected && complete.state === 'succeeded') onPrepared(selected)
    } catch (exc) {
      setStatus(exc instanceof Error ? exc.message : 'Source action failed.')
    }
  }

  const health = selected?.last_check
  const healthStatus = health ? String(health.status ?? 'unknown') : 'not_checked'
  const healthLabel = health ? String(health.label ?? healthStatus.replaceAll('_', ' ')) : 'Not checked'
  const healthTone = healthStatus === 'up_to_date' ? 'good' : 'warning'

  return <>
    <header className="page-header"><div><p className="eyebrow">ADVANCED TOOLS</p><h1>Source connections</h1><p className="muted">Inspect readiness separately from the health of any active database.</p></div></header>
    <section className="card settings-card">
      <label className="field"><span>Podcast-RAG source root</span><input value={root} onChange={(e) => setRoot(e.target.value)} placeholder="C:/…/Podcast RAG" /></label>
      <label className="field"><span>Partition ID</span><input value={partition} onChange={(e) => setPartition(e.target.value)} placeholder="partition-id" /></label>
      <div className="button-row"><button className="button secondary" disabled={!root || !partition} onClick={() => void action('inspect')}>Inspect source</button><button className="button primary" disabled={!root || !partition || !sourceReport} onClick={() => void action('prepare')}>Prepare source release</button></div>
      {root && partition && !sourceReport && <p className="muted">Inspect the source first to review readiness before resuming processing or preparing a release.</p>}
      <div className="info-box"><span>Active database health</span>{selected ? <><strong>{selected.display_name}</strong><small><Status tone={healthTone}>{healthLabel}</Status>{health?.checked_at ? ` · Last checked ${String(health.checked_at)}` : ' · Health has not been checked.'}</small></> : <small>No database is selected. Source readiness below is independent of active database health.</small>}</div>
      {status && <div className="info-box" role="status">{status}</div>}
      {sourceReport && <div className="review-box" role="region" aria-label="Source readiness report">
        <div className="activity-title"><Status tone={sourceReport.ready_to_publish === true ? 'good' : 'warning'}>{sourceReport.ready_to_publish === true ? 'Source ready to publish' : 'Source needs attention'}</Status><span>{String(sourceReport.display_name ?? sourceReport.partition_id ?? 'Managed source')}</span></div>
        <div className="detail-grid">
          <div><span className="field-label">Completed</span><strong>{String(sourceReport.completed ?? 0)}</strong></div>
          <div><span className="field-label">Pending</span><strong>{String(sourceReport.pending ?? 0)}</strong></div>
          <div><span className="field-label">Failed</span><strong>{String(sourceReport.failed ?? 0)}</strong></div>
          <div><span className="field-label">Interrupted</span><strong>{String(sourceReport.interrupted ?? 0)}</strong></div>
          <div><span className="field-label">Quarantined</span><strong>{String(sourceReport.quarantined ?? 0)}</strong></div>
          <div><span className="field-label">Active release</span><code>{String(sourceReport.active_release_id ?? 'None')}</code></div>
        </div>
        {Array.isArray(sourceReport.warnings) && sourceReport.warnings.length > 0 && <p className="muted">Warnings: {sourceReport.warnings.map(String).join(' ')}</p>}
        <p className="muted">These counts describe source readiness. They do not replace the active database health shown above.</p>
      </div>}
    </section>
  </>
}

function Redundancy() {
  return <>
    <header className="page-header"><div><p className="eyebrow">ADVANCED TOOLS</p><h1>Redundancy analysis</h1><p className="muted">Semantic redundancy assessment is advisory and never changes the active database.</p></div></header>
    <section className="card placeholder-panel"><Status>CLI-only workflow</Status><h2>Select a version, then assess</h2><p>Use the supported redundancy commands to select a frozen database version, preview coverage, assess candidate channels, optionally run a bounded judge pilot, and review or export results.</p><p className="muted">Run <code>chroma-db-import redundancy</code> from the existing environment. Results remain version-bound, and analysis does not approve deletion or modify active database content.</p></section>
  </>
}
function Settings({ api }: { api: AppClient }) {
  const [report, setReport] = useState<Record<string, unknown> | null>(null)
  const [migration, setMigration] = useState<Record<string, unknown> | null>(null)
  const [busy, setBusy] = useState(false)
  const [repairStatus, setRepairStatus] = useState<string | null>(null)
  const repairCommand = '.\\scripts\\Run-ChromaDbImportUi.ps1 -InstallDependencies -Ui Modern -NoLaunch'
  const diagnose = async () => { setBusy(true); try { setReport(await api.environmentReport()) } finally { setBusy(false) } }
  const inspectMigration = async () => { setBusy(true); try { setMigration(await api.migrationCandidates()) } finally { setBusy(false) } }
  const copyRepairCommand = async () => {
    try {
      await navigator.clipboard.writeText(repairCommand)
      setRepairStatus('Repair command copied. Run it in PowerShell to install or refresh the locked desktop dependencies and assets.')
    } catch {
      setRepairStatus(`Copy unavailable. Run this in PowerShell: ${repairCommand}`)
    }
  }
  const candidates = Array.isArray(migration?.candidates) ? migration.candidates as Array<Record<string, unknown>> : []
  return <>
    <header className="page-header"><div><p className="eyebrow">SETTINGS & HELP</p><h1>Environment</h1><p className="muted">Application defaults are separate from database-scoped settings.</p></div></header>
    <section className="card settings-card">
      <h2>Desktop setup</h2>
      <p>Modern UI assets are compiled locally and loaded by the packaged Python desktop host. The application does not run npm or load remote pages at runtime.</p>
      <div className="button-row"><button className="button secondary" disabled={busy} onClick={() => void diagnose()}>{busy ? 'Checking…' : 'Run environment diagnosis'}</button><button className="button secondary" onClick={() => void copyRepairCommand()}>Copy repair command</button></div>
      <p className="muted">Repair is explicit and uses the supported launcher; it never runs automatically when this page opens.</p>
      {repairStatus && <p className="info-box" role="status">{repairStatus}</p>}
      {report && <div className="review-box"><Status tone={report.assets_ready === true && Boolean(report.pywebview) ? 'good' : 'warning'}>{report.assets_ready === true && Boolean(report.pywebview) ? 'Desktop ready' : 'Review setup'}</Status><pre>{JSON.stringify(report, null, 2)}</pre></div>}
    </section>
    <section className="card settings-card"><h2>Legacy-state migration</h2><p className="muted">Inspect a field-level proposal before accepting migration. Original state and managed catalog files remain unchanged.</p><button className="button secondary" disabled={busy} onClick={() => void inspectMigration()}>{busy ? 'Inspecting…' : 'Review migration candidates'}</button>{migration && (candidates.length === 0 ? <p className="muted">No migration candidates were found.</p> : <div className="event-log">{candidates.map((candidate, index) => { const changes = candidate.changes && typeof candidate.changes === 'object' ? candidate.changes : null; const message = typeof candidate.message === 'string' ? candidate.message : ''; return <div key={`${String(candidate.path)}-${index}`}><strong>{String(candidate.kind)}</strong> · {String(candidate.status)} · {String(candidate.path)}{message && <p className="muted">{message}</p>}{changes && <details><summary>View proposed field changes</summary><pre>{JSON.stringify(changes, null, 2)}</pre></details>}</div> })}</div>)}</section>
    <section className="card placeholder-panel"><h2>Legacy interface</h2><p>The existing Qt interface remains available through the launcher while the modern UI is being verified.</p><p className="muted">For accepted migration, run <code>scripts\\Migrate-LegacyChromaDbImportState.ps1</code> from the repository.</p></section>
    <section className="card placeholder-panel"><h2>Settings import/export — Legacy UI</h2><p className="muted">Modern settings import/export is intentionally not a blind apply. Use the existing Legacy interface when you need to save or load a complete settings JSON; review the proposed fields first if the file comes from an older state.</p><code>scripts\\Run-ChromaDbImportUi.ps1 -Ui Legacy</code></section>
  </>
}

function BridgeShellCheck({ api }: { api: AppClient }) {
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const run = async () => {
    setBusy(true)
    try {
      const echo = await api.echo({ source: 'desktop-shell', timestamp: new Date().toISOString() })
      const job = await api.syntheticProgress()
      const complete = await waitForJob(api, job.id)
      setStatus(`${String(echo.source)} round-trip succeeded; synthetic progress ended ${complete.state}.`)
    } catch (exc) { setStatus(exc instanceof Error ? exc.message : 'Desktop shell check failed.') }
    finally { setBusy(false) }
  }
  return <section className="card settings-card"><h2>Desktop shell check</h2><p className="muted">Runs an explicit JSON round-trip and a bounded ten-second progress job. It does not read or change any database.</p><button className="button secondary" disabled={busy} onClick={() => void run()}>{busy ? 'Running shell check…' : 'Run shell check'}</button>{status && <p className="info-box">{status}</p>}</section>
}

async function waitForJob(api: AppClient, id: string): Promise<JobRecord> { for (let attempt = 0; attempt < 120; attempt += 1) { const job = await api.getJob(id); if (!['queued', 'running'].includes(job.state)) return job; await new Promise((resolve) => window.setTimeout(resolve, 250)) } throw new Error('The operation is taking longer than expected. Open Activity to continue watching it.') }

export default App
