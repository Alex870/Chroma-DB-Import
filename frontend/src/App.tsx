import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { client, ClientError, type AppClient } from './api/client'
import { mockClient } from './api/mockClient'
import type { ContentInventory, ContextRef, ContextSummary, DatabaseRecord, ExecutionOptions, JobRecord, ManagedCreationDefaults, Preview, SelectionPolicy } from './api/types'
import { ReportView } from './components/ReportView'
import { OperationProgress, OperationProvider } from './components/OperationProgress'
import { SpeakerSelectionEditor } from './components/SpeakerSelectionEditor'
import { EpisodeDetails } from './components/EpisodeDetails'
import { SourceConnections } from './pages/SourceConnections'
import { RedundancyAnalysis } from './pages/RedundancyAnalysis'
import { SettingsHelp } from './pages/SettingsHelp'

type Area = 'databases' | 'activity' | 'advanced' | 'settings'
type DatabaseTab = 'overview' | 'content' | 'history' | 'settings' | 'maintenance'

const useMock = import.meta.env.VITE_USE_MOCK_BRIDGE === 'true'

function Status({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'good' | 'warning' | 'danger' }) {
  return <span className={`status status-${tone}`}><span aria-hidden="true" />{children}</span>
}

function Empty({ title, body, action }: { title: string; body: string; action?: React.ReactNode }) {
  return <section className="empty"><div className="empty-icon" aria-hidden="true">◫</div><h2>{title}</h2><p>{body}</p>{action}</section>
}

function managedTargetPath(outputRoot: string, partitionId: string) {
  const root = outputRoot.replace(/[\\/]+$/, '')
  return root && partitionId ? `${root}/partitions/${partitionId}` : ''
}

function fallbackManagedCreationDefaults(context: ContextSummary): ManagedCreationDefaults {
  const partitionId = context.ref.partition_id || 'new-partition'
  const outputRoot = String(context.tracking?.suggested_target || '').trim() || `${context.source_root.replace(/[\\/]+$/, '')}/exports`
  const displayName = context.display_name?.trim() || partitionId
  return {
    source_kind: 'managed',
    source_ref: {
      connection_id: context.ref.connection_id,
      source_root: context.source_root,
      partition_id: partitionId,
      corpus_id: context.corpus_id || partitionId,
      catalog_path: context.catalog_path,
      upstream_release_id: String(context.tracking?.latest_release?.upstream_release_id ?? context.source_status.active_release_id ?? ''),
    },
    display_name: displayName,
    target: { path: managedTargetPath(outputRoot, partitionId), managed_output_root: outputRoot },
    selection_policy: { speaker_mode: 'all', excluded_speakers: [], allowlist_speakers: [], episode_overrides: {}, excluded_episode_ids: [], asset_filter: 'reviewed_speaker_transcript', asset_pattern: '' },
    execution_options: { embedding_device: 'auto' },
    representation: { profile: 'qwen3-embedding-4b-shadow', embedding_model: 'Qwen/Qwen3-Embedding-4B' },
    provenance: { output: 'managed_fallback', display_name: 'partition', selection_policy: 'builtin', execution_options: 'builtin', representation: 'builtin', release: 'latest_valid_release' },
  }
}

function managedTargetError(target: string, partitionId: string) {
  const normalized = target.trim().replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
  const suffix = `/partitions/${partitionId.trim().toLowerCase()}`
  if (!normalized) return 'An output partition is required.'
  if (!partitionId.trim() || !normalized.endsWith(suffix)) return `The output partition must end in partitions/${partitionId || '<partition id>'}.`
  return null
}

function managedOutputRootFromTarget(target: string, partitionId: string) {
  const normalized = target.trim().replace(/\\/g, '/').replace(/\/+$/, '')
  const marker = `/partitions/${partitionId.trim()}`
  const index = normalized.toLowerCase().lastIndexOf(marker.toLowerCase())
  return index > 0 ? normalized.slice(0, index) : ''
}

function AppShell() {
  const api: AppClient = useMock ? mockClient : client
  const initialArea: Area = new URLSearchParams(window.location.search).get('workspace') === 'contexts' ? 'advanced' : 'databases'
  const [area, setArea] = useState<Area>(initialArea)
  const [databases, setDatabases] = useState<DatabaseRecord[]>([])
  const [contexts, setContexts] = useState<ContextSummary[]>([])
  const [connectionStates, setConnectionStates] = useState<Array<Record<string, unknown>>>([])
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
  const [existingSourceKind, setExistingSourceKind] = useState<'folder' | 'managed'>('folder')
  const [existingPartition, setExistingPartition] = useState('')
  const [existingConnectionId, setExistingConnectionId] = useState('')
  const [archiveView, setArchiveView] = useState<'active' | 'archived'>('active')
  const [createContext, setCreateContext] = useState<ContextSummary | null>(null)
  const [updateReleaseId, setUpdateReleaseId] = useState<string | null>(null)
  const [sourceContextRef, setSourceContextRef] = useState<ContextRef | null>(null)
  const [existingContextRef, setExistingContextRef] = useState<ContextRef | null>(null)
  const runningRef = useRef(false)
  const reconciliationRef = useRef(false)

  const reconcilePipeline = async () => {
    if (reconciliationRef.current) return
    reconciliationRef.current = true
    try {
      const reconciliation = await api.reconcileSources()
      setContexts(reconciliation.contexts)
      setConnectionStates(reconciliation.connections)
    } catch (exc) {
      setError(exc instanceof ClientError ? exc.message : 'The source status could not be refreshed.')
    } finally {
      reconciliationRef.current = false
    }
  }

  const refresh = async (reconcile = false) => {
    try {
      const version = await api.handshake()
      if (version.api_version !== 'gui-api-v1') throw new ClientError({ code: 'BRIDGE_VERSION_MISMATCH', message: 'The desktop bridge is older than this frontend. Rebuild the packaged UI before continuing.', field: null, details_id: null })
      const [nextDatabases, nextJobs] = await Promise.all([api.listDatabases(true), api.listJobs()])
      setDatabases(nextDatabases)
      setJobs(nextJobs)
      setError(null)
      // The library is usable as soon as its local catalog is loaded. Source
      // reconciliation can inspect a large producer tree, so let it run
      // independently of the initial database-list render.
      if (reconcile) void reconcilePipeline()
    }
    catch (exc) { setError(exc instanceof ClientError ? exc.message : 'The desktop connection is unavailable.') }
  }
  useEffect(() => { void refresh(true) }, [])
  useEffect(() => {
    const active = jobs.some((job) => job.state === 'queued' || job.state === 'running')
    if (!active) {
      if (runningRef.current) {
        runningRef.current = false
        void refresh()
      }
      return
    }
    runningRef.current = true
    const id = window.setInterval(() => void refresh(false), 1000)
    return () => window.clearInterval(id)
  }, [jobs])

  const filtered = useMemo(() => databases.filter((db) => db.display_name.toLowerCase().includes(query.toLowerCase())), [databases, query])
  const running = jobs.filter((job) => job.state === 'queued' || job.state === 'running')

  const choose = (db: DatabaseRecord) => { setSelected(db); setView('library'); setArea('databases'); setTab('overview') }
  const startUpdate = (db: DatabaseRecord, releaseId: string | null = null) => { setSelected(db); setView('update'); setPreview(null); setUpdateReleaseId(releaseId); setError(null) }
  const archive = async (db: DatabaseRecord) => { if (!window.confirm('Hide this library entry? Database files remain.')) return; await api.archiveDatabase(db.id); setMessage('Hidden from this library; database files remain.'); await refresh() }
  const replaceContext = (updated: ContextSummary) => {
    setContexts((current) => current.map((item) => item.ref.connection_id === updated.ref.connection_id && item.ref.partition_id === updated.ref.partition_id ? updated : item))
  }
  const loadCachedContext = async (ref: ContextRef) => {
    try { replaceContext(await api.getContext(ref)) } catch { /* The existing screen remains usable until the next explicit pipeline refresh. */ }
  }
  const archiveContext = async (context: ContextSummary) => {
    if (!window.confirm(`Remove ${context.display_name || context.ref.partition_id} from available partitions? This only hides the partition in Chroma DB Import; source files and databases remain unchanged.`)) return
    try {
      await api.setContextArchived(context.ref, true)
      setContexts((current) => current.map((item) => item.ref.connection_id === context.ref.connection_id && item.ref.partition_id === context.ref.partition_id ? { ...item, local_status: 'archived' } : item))
      setMessage('Partition removed from available partitions; source files and databases were not changed.')
    } catch (exc) { setError(exc instanceof ClientError ? exc.message : exc instanceof Error ? exc.message : 'The partition could not be removed.') }
  }
  const openExistingTarget = (targetPath: string, sourcePath = '', partitionId = '', connectionId = '', contextRef: ContextRef | null = null) => {
    setExistingTarget(targetPath)
    setExistingSource(sourcePath)
    setExistingSourceKind('managed')
    setExistingPartition(partitionId)
    setExistingConnectionId(connectionId)
    setExistingContextRef(contextRef)
    setArea('databases')
    setView('add')
  }
  const adoptContext = async (context: ContextSummary, candidate: Record<string, unknown>) => {
    const databaseId = String(candidate.database_id ?? '')
    const target = candidate.target && typeof candidate.target === 'object' ? candidate.target as Record<string, unknown> : {}
    if (!databaseId) {
      openExistingTarget(String(target.path ?? ''), context.source_root, context.ref.partition_id, context.ref.connection_id, context.ref)
      return
    }
    const database = databases.find((item) => item.id === databaseId)
    if (database?.archived) { setError('Archived databases are not eligible existing targets. Restore it before linking.'); return }
    if (!window.confirm('Use this existing database for the selected source partition?')) return
    try {
      await api.adoptDatabaseLink({ database_id: databaseId, connection_id: context.ref.connection_id, partition_id: context.ref.partition_id })
      await loadCachedContext(context.ref)
      setMessage('Existing database linked; future source releases will be tracked automatically.')
    } catch (exc) { setError(exc instanceof ClientError ? exc.message : exc instanceof Error ? exc.message : 'The existing database could not be linked.') }
  }
  const restoreDatabase = async (db: DatabaseRecord, contextRef?: ContextRef) => {
    try {
      const restored = await api.restoreDatabase(db.id)
      setDatabases((current) => current.map((item) => item.id === restored.id ? restored : item))
      setMessage('Restored to the active library; database files were not changed.')
      if (contextRef) await loadCachedContext(contextRef)
    } catch (exc) { setError(exc instanceof ClientError ? exc.message : exc instanceof Error ? exc.message : 'The database could not be restored.') }
  }
  const openSourceContext = (context: ContextSummary) => { setSourceContextRef(context.ref); setArea('advanced'); setAdvancedView('source') }

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark">C</div><div><strong>Chroma DB Import</strong><small>Database manager</small></div></div>
      <nav aria-label="Primary navigation">
        <button className={area === 'databases' ? 'nav-item active' : 'nav-item'} onClick={() => setArea('databases')}>▦ <span>Databases</span></button>
        <button className={area === 'activity' ? 'nav-item active' : 'nav-item'} onClick={() => setArea('activity')}>◷ <span>Activity</span>{running.length > 0 && <b>{running.length}</b>}</button>
        <div className="nav-section">ADVANCED TOOLS</div>
        <button className={area === 'advanced' && advancedView === 'source' ? 'nav-item active' : 'nav-item'} onClick={() => { setArea('advanced'); setAdvancedView('source') }}>⌁ <span>Source connections</span></button>
        <button className={area === 'advanced' && advancedView === 'redundancy' ? 'nav-item active' : 'nav-item'} onClick={() => { setArea('advanced'); setAdvancedView('redundancy') }}>◌ <span>Redundancy analysis · semantic judge</span></button>
        <div className="nav-spacer" />
        <button className={area === 'settings' ? 'nav-item active' : 'nav-item'} onClick={() => setArea('settings')}>⚙ <span>Settings & help</span></button>
      </nav>
    </aside>
    <main className="main-content">
      {error && <div className="alert alert-danger" role="alert"><strong>Connection or workflow issue.</strong> {error}<button onClick={() => setError(null)} aria-label="Dismiss">×</button></div>}
      {message && <div className="alert alert-good" role="status">{message}<button onClick={() => setMessage(null)} aria-label="Dismiss">×</button></div>}
      <OperationProgress onViewActivity={() => setArea('activity')} />
      {running.length > 0 && <button className="job-strip" onClick={() => setArea('activity')}><span className="spinner" />{running.length} operation{running.length > 1 ? 's' : ''} in progress <span>View activity →</span></button>}
      {area === 'databases' && view === 'library' && <Library databases={filtered.filter((db) => archiveView === 'archived' ? db.archived : !db.archived)} allDatabases={databases} contexts={contexts} query={query} setQuery={setQuery} archiveView={archiveView} setArchiveView={setArchiveView} onCreate={() => { setCreateContext(null); setView('create'); setSelected(null) }} onCreateFromContext={(context) => { setCreateContext(context); setView('create'); setSelected(null) }} onAdd={() => setView('add')} onSelect={choose} onUpdate={startUpdate} onSupplement={(db, releaseId) => startUpdate(db, releaseId)} onArchive={archive} onRestore={restoreDatabase} onRemoveContext={archiveContext} onAdoptContext={adoptContext} onOpenSourceContext={openSourceContext} onRefresh={() => refresh(true)} />}
      {area === 'databases' && view === 'create' && <Create api={api} initialContext={createContext} onCancel={() => { setCreateContext(null); setView('library') }} onUseExisting={(targetPath, sourcePath, sourceKind = 'folder', partitionId = '', connectionId = '') => { setExistingTarget(targetPath); setExistingSource(sourcePath ?? ''); setExistingSourceKind(sourceKind); setExistingPartition(partitionId); setExistingConnectionId(connectionId); setView('add') }} onDone={async () => { setCreateContext(null); await refresh(); setView('library'); setArea('activity') }} setError={setError} />}
      {area === 'databases' && view === 'add' && <AddExisting api={api} initialTarget={existingTarget} initialSource={existingSource} initialSourceKind={existingSourceKind} initialPartition={existingPartition} initialConnectionId={existingConnectionId} onCancel={() => setView('library')} onDone={async () => { const contextRef = existingContextRef; setExistingTarget(''); setExistingSource(''); setExistingPartition(''); setExistingConnectionId(''); setExistingContextRef(null); await refresh(); if (contextRef) await loadCachedContext(contextRef); setView('library') }} setError={setError} />}
      {area === 'databases' && view === 'update' && selected && <Update api={api} db={selected} sourceReleaseId={updateReleaseId} preview={preview} setPreview={setPreview} onBack={() => setView('library')} onSourceConnections={() => { setArea('advanced'); setAdvancedView('source') }} onRepairIdentity={() => { setExistingTarget(selected.target.path); setExistingSource(String(selected.source_ref.path ?? selected.source_ref.source_root ?? '')); setView('add') }} onCreateSeparate={() => { setCreateContext(null); setView('create'); setSelected(null) }} onApplied={async () => { setUpdateReleaseId(null); await refresh(); setArea('activity') }} setError={setError} />}
      {area === 'databases' && view === 'library' && selected && <DatabaseDetail api={api} db={selected} tab={tab} setTab={setTab} onUpdate={() => void startUpdate(selected)} onApplied={async () => { await refresh(); setArea('activity') }} onSaved={async () => { await refresh(); const latest = await api.getDatabase(selected.id); setSelected(latest) }} setError={setError} />}
      {area === 'activity' && <Activity jobs={jobs} api={api} onRefresh={refresh} onViewDatabase={(id) => { const db = databases.find((item) => item.id === id); if (db) choose(db) }} />}
      {area === 'advanced' && advancedView === 'source' && <SourceConnections api={api} selectedDatabase={selected} databases={databases} initialContexts={contexts} initialConnectionStates={connectionStates} initialContextRef={sourceContextRef} onPipelineRefreshed={(reconciliation) => { setContexts(reconciliation.contexts); setConnectionStates(reconciliation.connections) }} onContextsChanged={(nextContexts) => setContexts(nextContexts)} onConnectionStatesChanged={(nextStates) => setConnectionStates(nextStates)} onCreateContext={(context) => { setCreateContext(context); setArea('databases'); setView('create') }} onRestoreDatabase={restoreDatabase} onSupplement={(db, releaseId) => { setArea('databases'); startUpdate(db, releaseId) }} onUseExisting={(targetPath, sourcePath, partitionId, connectionId, contextRef) => openExistingTarget(targetPath, sourcePath ?? '', partitionId ?? '', connectionId ?? '', contextRef ?? null)} onPrepared={(db) => { setArea('databases'); startUpdate(db) }} />}
      {area === 'advanced' && advancedView === 'redundancy' && <RedundancyAnalysis api={api} />}
      {area === 'settings' && <><SettingsHelp api={api} /><Settings api={api} /><BridgeShellCheck api={api} /></>}
    </main>
  </div>
}

function App() {
  return <OperationProvider><AppShell /></OperationProvider>
}

function Library({ databases, allDatabases, contexts, query, setQuery, archiveView, setArchiveView, onCreate, onCreateFromContext, onAdd, onSelect, onUpdate, onSupplement, onArchive, onRestore, onRemoveContext, onAdoptContext, onOpenSourceContext, onRefresh }: { databases: DatabaseRecord[]; allDatabases: DatabaseRecord[]; contexts: ContextSummary[]; query: string; setQuery: (v: string) => void; archiveView: 'active' | 'archived'; setArchiveView: (value: 'active' | 'archived') => void; onCreate: () => void; onCreateFromContext: (context: ContextSummary) => void; onAdd: () => void; onSelect: (db: DatabaseRecord) => void; onUpdate: (db: DatabaseRecord) => void; onSupplement: (db: DatabaseRecord, releaseId: string) => void; onArchive: (db: DatabaseRecord) => void; onRestore: (db: DatabaseRecord, contextRef?: ContextRef) => void; onRemoveContext: (context: ContextSummary) => void; onAdoptContext: (context: ContextSummary, candidate: Record<string, unknown>) => void; onOpenSourceContext: (context: ContextSummary) => void; onRefresh: () => Promise<void> }) {
  const [menuId, setMenuId] = useState<string | null>(null)
  const trackedContexts = contexts.filter((context) => context.tracking && context.local_status !== 'archived')
  const stateLabel = (state: string) => ({ ready_to_create: 'Ready to create', update_available: 'Update available', current: 'Current', source_not_ready: 'Source needs attention', source_unavailable: 'Source unavailable', profile_mismatch: 'Profile mismatch', ambiguous_match: 'Choose a database', legacy_adoption_available: 'Existing database available', target_unavailable: 'Target unavailable' }[state] ?? state.replaceAll('_', ' '))
  const stateTone = (state: string): 'neutral' | 'good' | 'warning' | 'danger' => state === 'current' ? 'good' : ['source_unavailable', 'profile_mismatch', 'target_unavailable'].includes(state) ? 'danger' : state === 'ready_to_create' ? 'good' : 'warning'
  const pipelinePanel = trackedContexts.length > 0 && <section className="card pipeline-card"><div className="pipeline-heading"><div><p className="eyebrow">PROCESSED PIPELINE</p><h2>Available partitions</h2><p className="muted">The source pipeline and this library are checked together. Nothing is changed until you review an action.</p></div><button className="button small" onClick={() => void onRefresh()}>Refresh</button></div><div className="pipeline-list">{trackedContexts.map((context) => {
    const tracking = context.tracking!
    const latest = tracking.latest_release
    const matching = tracking.matching_databases.length === 1 ? tracking.matching_databases[0] : null
    const database = matching ? allDatabases.find((db) => db.id === String(matching.database_id)) : null
    const eligibleLegacy = tracking.legacy_candidates.filter((candidate) => {
      if (candidate.status !== 'adoption_available') return false
      const candidateDatabase = candidate.database_id ? allDatabases.find((db) => db.id === String(candidate.database_id)) : null
      if (candidateDatabase) return !candidateDatabase.archived && Boolean(candidateDatabase.target.path)
      const target = candidate.target && typeof candidate.target === 'object' ? candidate.target as Record<string, unknown> : {}
      return Boolean(String(target.path ?? '').trim())
    })
    const partitionName = context.display_name || context.ref.partition_id
    const actions: ReactNode[] = []
    if (tracking.state === 'ready_to_create') {
      actions.push(<button key="create" aria-label={`Create database for ${partitionName}`} className="button primary small" onClick={() => onCreateFromContext(context)}>Create new database</button>)
    } else if (tracking.state === 'legacy_adoption_available') {
      actions.push(<button key="create" aria-label={`Create database for ${partitionName}`} className="button primary small" onClick={() => onCreateFromContext(context)}>Create new database</button>)
      if (eligibleLegacy.length === 1) actions.push(<button key="use" aria-label={`Use existing database for ${partitionName}`} className="button secondary small" onClick={() => onAdoptContext(context, eligibleLegacy[0])}>Use existing database</button>)
      else if (eligibleLegacy.length > 1) actions.push(<button key="choose" className="button secondary small" onClick={() => onOpenSourceContext(context)}>Choose existing database</button>)
      else actions.push(<button key="review" className="button secondary small" onClick={() => onOpenSourceContext(context)}>Review source connection</button>)
    } else if (tracking.state === 'update_available' && database && !database.archived && matching?.compatible !== false) {
      actions.push(<button key="update" className="button primary small" onClick={() => onSupplement(database, String(latest?.upstream_release_id ?? ''))}>Review supplement</button>)
    } else if (tracking.state === 'current' && database) {
      actions.push(<button key="review" className="button secondary small" onClick={() => onSelect(database)}>Review database</button>)
    } else if (tracking.state === 'ambiguous_match' && database?.archived) {
      actions.push(<button key="restore" className="button secondary small" onClick={() => onRestore(database, context.ref)}>Restore database</button>)
      actions.push(<button key="separate" aria-label={`Create separate database for ${partitionName}`} className="button secondary small" onClick={() => onCreateFromContext(context)}>Create separate database</button>)
    } else if (tracking.state === 'ambiguous_match') {
      actions.push(<button key="choose" className="button secondary small" onClick={() => onOpenSourceContext(context)}>{tracking.matching_databases.length > 1 ? 'Choose update database' : 'Choose existing database'}</button>)
    } else if (tracking.state === 'profile_mismatch' || tracking.state === 'target_unavailable') {
      actions.push(<button key="separate" aria-label={`Create separate database for ${partitionName}`} className="button secondary small" onClick={() => onCreateFromContext(context)}>Create separate database</button>)
    }
    actions.push(<button key="remove" aria-label={`Remove partition ${partitionName}`} className="button small" onClick={() => onRemoveContext(context)}>Remove partition</button>)
    return <article className="pipeline-item" key={`${context.ref.connection_id}:${context.ref.partition_id}`}><div><div className="activity-title"><Status tone={stateTone(tracking.state)}>{stateLabel(tracking.state)}</Status><strong>{partitionName}</strong></div><p className="muted">{context.context_type} · {context.ref.partition_id} · {String(latest?.upstream_release_id ?? 'No published release')}</p><small>{String(context.source_status.completed ?? 0)} completed · {String(context.source_status.pending ?? 0)} pending · {String(context.source_status.failed ?? 0)} failed · {String(context.source_status.quarantined ?? 0)} quarantined</small>{tracking.change_counts && <small>{tracking.change_counts.episodes_total} episodes in latest release · record changes are calculated in review</small>}{tracking.last_applied_release_id && <small>Last applied: {tracking.last_applied_release_id}</small>}<p className="muted">{tracking.reason}</p></div><div className="button-row">{actions}</div></article>
  })}</div></section>
  return <>{pipelinePanel}<header className="page-header"><div><p className="eyebrow">LIBRARY</p><h1>Databases</h1><p className="muted">Choose a database to review content, update it, or inspect its technical details.</p></div><div className="header-actions"><button className="button secondary" onClick={onAdd}>Add existing</button><button className="button primary" onClick={onCreate}>＋ Create database</button></div></header>
    <div className="toolbar"><label className="search">⌕<input aria-label="Search databases" placeholder="Search databases…" value={query} onChange={(e) => setQuery(e.target.value)} /></label><div className="button-row" role="group" aria-label="Database visibility"><button className={`button small ${archiveView === 'active' ? 'primary' : ''}`} onClick={() => setArchiveView('active')}>Active</button><button className={`button small ${archiveView === 'archived' ? 'primary' : ''}`} onClick={() => setArchiveView('archived')}>Archived</button><span className="muted">{databases.length} {databases.length === 1 ? 'database' : 'databases'}</span></div></div>
    {databases.length === 0 ? <Empty title={query ? `No ${archiveView} databases match` : archiveView === 'archived' ? 'No archived databases' : trackedContexts.length > 0 ? 'No registered databases yet' : 'No databases yet'} body={query ? 'Try a different search or switch the Active / Archived view.' : trackedContexts.length > 0 ? 'Your processed pipeline is ready to review above. Choose a partition to create or link its database.' : 'Create a database from processed podcast or meeting content, or add an existing export.'} action={!query && archiveView === 'active' ? <><button className="button primary" onClick={onCreate}>Create database</button><button className="button secondary" onClick={onAdd}>Add existing database</button></> : undefined} /> : <section className="card table-card"><table><thead><tr><th>Database</th><th>Source</th><th>Status</th><th>Last checked</th><th aria-label="Actions" /></tr></thead><tbody>{databases.map((db) => <tr key={db.id} onClick={() => onSelect(db)}><td><button className="link-button" onClick={() => onSelect(db)}>{db.display_name}</button><small>{db.target.path}</small></td><td>{db.source_kind === 'managed' ? 'Podcast-RAG source' : 'Processed files folder'}</td><td>{db.last_check ? <Status tone={db.last_check.status === 'up_to_date' ? 'good' : 'warning'}>{String(db.last_check.label ?? 'Needs review')}</Status> : <Status>Not checked</Status>}</td><td>{db.last_check ? String(db.last_check.checked_at ?? 'Unknown') : '—'}</td><td><div className="row-actions">{!db.archived && <button className="button small" onClick={(e) => { e.stopPropagation(); void onUpdate(db) }}>Update</button>}<button className="button small" aria-expanded={menuId === db.id} onClick={(e) => { e.stopPropagation(); setMenuId(menuId === db.id ? null : db.id) }}>More</button>{menuId === db.id && <div className="more-menu" role="menu">{db.archived ? <button role="menuitem" onClick={() => { setMenuId(null); void onRestore(db) }}>Restore</button> : <button role="menuitem" onClick={() => { setMenuId(null); void onArchive(db) }}>Archive database</button>}</div>}</div></td></tr>)}</tbody></table></section>}
  </>
}

function DatabaseDetail({ api, db, tab, setTab, onUpdate, onApplied, onSaved, setError }: { api: AppClient; db: DatabaseRecord; tab: DatabaseTab; setTab: (tab: DatabaseTab) => void; onUpdate: () => void; onApplied: () => Promise<void>; onSaved: () => Promise<void>; setError: (value: string | null) => void }) {
  const [report, setReport] = useState<import('./api/types').Report | null>(null)
  const [busy, setBusy] = useState(false)
  const openFolder = async () => { setBusy(true); try { const path = await api.openDatabaseFolder(db.id); setError(`Opened ${path}`) } catch (exc) { setError(exc instanceof Error ? exc.message : 'The active database folder could not be opened.') } finally { setBusy(false) } }
  const loadDetails = async () => { setBusy(true); try { setReport(await api.getDatabaseDetails(db.id)) } catch (exc) { setError(exc instanceof Error ? exc.message : 'Database details could not be read.') } finally { setBusy(false) } }
  if (report) return <ReportView report={report} originLabel="Database details" onClose={() => setReport(null)} onSave={async (value) => { const saved = await api.exportReport(value); setError(`Report saved to ${String(saved.path ?? saved.report_id)}`) }} />
  return <section className="detail card"><div className="detail-heading"><div><p className="eyebrow">DATABASE</p><h2>{db.display_name}</h2><p className="muted">{db.source_kind === 'managed' ? 'Podcast-RAG source' : 'Processed files folder'} · <Status>{db.last_check ? 'Checked' : 'Not checked'}</Status></p></div><div className="button-row"><button className="button secondary" disabled={busy} onClick={() => void openFolder()}>Open folder</button><button className="button primary" onClick={onUpdate}>Update</button></div></div><div className="tabs" role="tablist">{(['overview', 'content', 'history', 'settings', 'maintenance'] as DatabaseTab[]).map((item) => <button key={item} role="tab" aria-selected={tab === item} className={tab === item ? 'tab active' : 'tab'} onClick={() => setTab(item)}>{item[0].toUpperCase() + item.slice(1)}</button>)}</div>{tab === 'overview' && <><div className="detail-grid"><div><span className="field-label">Target</span><code>{db.target.path}</code></div><div><span className="field-label">Embedding profile</span><strong>{String(db.downstream_identity?.profile ?? db.target.representation_profile ?? 'Unknown')}</strong></div><div><span className="field-label">Database identity</span><code>{String(db.downstream_identity?.database_id ?? db.id)}</code></div><div><span className="field-label">Settings revision</span><strong>{db.settings_revision}</strong></div></div><button className="button secondary" disabled={busy} onClick={() => void loadDetails()}>Database details</button></>}{tab === 'content' && <ContentPanel api={api} db={db} onSaved={onSaved} />}{tab === 'history' && <HistoryPanel api={api} db={db} />}{tab === 'settings' && <DatabaseSettingsPanel api={api} db={db} onSaved={onSaved} />}{tab === 'maintenance' && <MaintenancePanel api={api} db={db} onUpdate={onUpdate} onApplied={onApplied} setError={setError} />}</section>
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
  const [policy, setPolicy] = useState<SelectionPolicy>(db.selection_policy)
  const [content, setContent] = useState<ContentInventory | null>(null)
  const [details, setDetails] = useState<import('./api/types').EpisodeInspection | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { let alive = true; void api.getDatabaseContent(db.id).then((value) => { if (alive) setContent(value) }).catch(() => { if (alive) setContent(null) }); return () => { alive = false } }, [api, db.id])
  const save = async () => { setBusy(true); try { await api.updateDatabaseSettings(db.id, { selection_policy: policy }); await onSaved() } finally { setBusy(false) } }
  if (!content) return <div className="loading-panel">Loading episode inventory…</div>
  return <div className="placeholder-panel"><SpeakerSelectionEditor policy={policy} episodes={content.episodes} onChange={setPolicy} onSave={() => void save()} busy={busy} />{details && <EpisodeDetails episode={details} onClose={() => setDetails(null)} />}<p className="muted">{content.source_available ? 'Source and stored inventory are available.' : 'Source inventory is unavailable; stored episodes remain available with unavailable source metrics.'}</p><div className="episode-detail-links">{content.episodes.slice(0, 100).map((episode) => <button className="link-button" key={episode.episode_id} onClick={() => setDetails(episode)}>{episode.title} details</button>)}</div></div>
}

function HistoryPanel({ api, db }: { api: AppClient; db: DatabaseRecord }) {
  const [history, setHistory] = useState<Record<string, unknown> | null>(null)
  useEffect(() => { void api.getDatabaseHistory(db.id).then(setHistory).catch(() => setHistory(null)) }, [api, db.id])
  if (!history) return <div className="loading-panel">Loading version history…</div>
  const jobs = Array.isArray(history.jobs) ? history.jobs as Array<Record<string, unknown>> : []
  const summary = history.metadata_summary as Record<string, unknown> | undefined
  const tracking = history.tracking as Record<string, unknown> | undefined
  const releaseHistory = Array.isArray(tracking?.release_history) ? tracking.release_history as Array<Record<string, unknown>> : []
  const link = tracking?.link as Record<string, unknown> | null | undefined
  return <div className="placeholder-panel"><h3>Version history</h3><div className="detail-grid"><div><span className="field-label">Active state</span><Status tone={history.active_state === 'active' ? 'good' : 'danger'}>{String(history.active_state ?? 'unknown')}</Status></div><div><span className="field-label">Active database release</span><code>{String(history.active_release_id ?? 'Not managed')}</code></div><div><span className="field-label">Source release</span><code>{String(history.source_release_id ?? 'Not declared')}</code></div><div><span className="field-label">Stored episodes</span><strong>{String(summary?.episodes ?? 'Unknown')}</strong></div></div>{link && <div className="info-box"><strong>Automatic source tracking</strong><p className="muted">{String(link.partition_id ?? 'Partition')} · last applied {String(link.last_source_release_id ?? 'Not imported')}</p></div>}{releaseHistory.length > 0 && <><h4>Source and database releases</h4><ul className="event-log">{releaseHistory.map((event) => <li key={String(event.id)}><span>{String(event.operation)}</span>{String(event.status)} · source {String(event.upstream_release_id ?? 'unknown')} · database {String(event.downstream_release_id ?? 'unknown')} · {String(event.created_at)}</li>)}</ul></>}{jobs.length === 0 ? <p className="muted">No cataloged operations have been recorded yet.</p> : <ul className="event-log">{jobs.map((job) => <li key={String(job.id)}><span>{String(job.kind)}</span>{String(job.state)} · {String(job.completed_at ?? job.created_at)}</li>)}</ul>}<div className="info-box"><strong>Release administration — CLI-only</strong><p className="muted">Promotion, rollback, and pruning require an exact validated plan and are not exposed as unsupported GUI actions.</p><code>conda run -n chroma-db-import python -m chroma_db_import.release_cli status --store &lt;release-store&gt;</code></div></div>
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

function Create({ api, initialContext, onCancel, onUseExisting, onDone, setError }: { api: AppClient; initialContext: ContextSummary | null; onCancel: () => void; onUseExisting: (targetPath: string, sourcePath?: string, sourceKind?: 'folder' | 'managed', partitionId?: string, connectionId?: string) => void; onDone: () => Promise<void>; setError: (v: string | null) => void }) {
  const contextualDefaults = initialContext ? (initialContext.creation_defaults ?? fallbackManagedCreationDefaults(initialContext)) : null
  const [step, setStep] = useState(1)
  const [sourceKind, setSourceKind] = useState<'folder' | 'managed'>(() => initialContext ? 'managed' : 'folder')
  const [source, setSource] = useState(() => contextualDefaults?.source_ref.source_root ?? '')
  const [partition, setPartition] = useState(() => contextualDefaults?.source_ref.partition_id ?? '')
  const [connectionId, setConnectionId] = useState(() => contextualDefaults?.source_ref.connection_id ?? '')
  const [catalogPath, setCatalogPath] = useState(() => contextualDefaults?.source_ref.catalog_path ?? '')
  const [corpusId, setCorpusId] = useState(() => contextualDefaults?.source_ref.corpus_id ?? '')
  const [upstreamReleaseId, setUpstreamReleaseId] = useState(() => contextualDefaults?.source_ref.upstream_release_id ?? '')
  const [name, setName] = useState(() => contextualDefaults?.display_name ?? '')
  const [target, setTarget] = useState(() => contextualDefaults?.target.path ?? '')
  const [managedOutputRoot, setManagedOutputRoot] = useState(() => contextualDefaults?.target.managed_output_root ?? '')
  const [assetFilter, setAssetFilter] = useState(() => contextualDefaults?.selection_policy.asset_filter ?? 'reviewed_speaker_transcript')
  const [assetPattern, setAssetPattern] = useState(() => contextualDefaults?.selection_policy.asset_pattern ?? '')
  const [speakerMode, setSpeakerMode] = useState<SelectionPolicy['speaker_mode']>(() => contextualDefaults?.selection_policy.speaker_mode ?? 'all')
  const [allowlistSpeakers, setAllowlistSpeakers] = useState(() => contextualDefaults?.selection_policy.allowlist_speakers.join(', ') ?? '')
  const [excludedSpeakers, setExcludedSpeakers] = useState(() => contextualDefaults?.selection_policy.excluded_speakers.join(', ') ?? '')
  const [excludedEpisodeIds, setExcludedEpisodeIds] = useState<string[]>(() => contextualDefaults?.selection_policy.excluded_episode_ids ?? [])
  const [episodeOverrides, setEpisodeOverrides] = useState<Record<string, string[]>>(() => contextualDefaults?.selection_policy.episode_overrides ?? {})
  const [executionOptions, setExecutionOptions] = useState<ExecutionOptions>(() => contextualDefaults?.execution_options ?? { embedding_device: 'auto' })
  const [episodeSearch, setEpisodeSearch] = useState('')
  const [draftId, setDraftId] = useState<string>(() => initialContext ? '' : window.localStorage.getItem('chroma-gui-create-draft') || '')
  const [scan, setScan] = useState<Record<string, unknown> | null>(() => initialContext?.source_status ?? null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [environment, setEnvironment] = useState<Record<string, unknown> | null>(null)
  const [busy, setBusy] = useState(false)
  const scanRevision = useRef(0)
  const draftIdRef = useRef(draftId)
  const draftSaveQueue = useRef<Promise<void>>(Promise.resolve())
  const restoreDraft = useRef(Boolean(draftId) && !initialContext)
  const [targetExists, setTargetExists] = useState(false)
  const [contextPinned, setContextPinned] = useState(Boolean(initialContext))
  const [targetManuallyEdited, setTargetManuallyEdited] = useState(false)

  useEffect(() => { draftIdRef.current = draftId }, [draftId])

  const draftPayload = () => ({
    source_kind: sourceKind,
    source_ref: sourceKind === 'managed' ? { source_root: source, partition_id: partition, ...(connectionId ? { connection_id: connectionId } : {}), ...(catalogPath ? { catalog_path: catalogPath } : {}), ...(corpusId ? { corpus_id: corpusId } : {}), ...(upstreamReleaseId ? { upstream_release_id: upstreamReleaseId } : {}) } : { path: source },
    display_name: name || 'New database',
    target: { path: target, ...(sourceKind === 'managed' && managedOutputRoot ? { managed_output_root: managedOutputRoot } : {}) },
    execution_options: executionOptions,
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

  useEffect(() => { if (!draftId || !restoreDraft.current) return; restoreDraft.current = false; void api.getDraft(draftId).then((draft) => { const payload = draft.payload as Record<string, unknown>; const reference = payload.source_ref as Record<string, unknown> | undefined; const policy = payload.selection_policy as Partial<SelectionPolicy> | undefined; const targetValue = payload.target as Record<string, unknown> | undefined; const options = payload.execution_options as Partial<ExecutionOptions> | undefined; setSourceKind(payload.source_kind === 'managed' ? 'managed' : 'folder'); setSource(String(reference?.path ?? reference?.source_root ?? '')); setPartition(String(reference?.partition_id ?? '')); setConnectionId(String(reference?.connection_id ?? '')); setCatalogPath(String(reference?.catalog_path ?? '')); setCorpusId(String(reference?.corpus_id ?? '')); setUpstreamReleaseId(String(reference?.upstream_release_id ?? '')); setName(String(payload.display_name ?? '')); setTarget(String(targetValue?.path ?? '')); setManagedOutputRoot(String(targetValue?.managed_output_root ?? '')); setSpeakerMode(policy?.speaker_mode === 'allowlist' ? 'allowlist' : 'all'); setAllowlistSpeakers(Array.isArray(policy?.allowlist_speakers) ? policy.allowlist_speakers.join(', ') : ''); setExcludedSpeakers(Array.isArray(policy?.excluded_speakers) ? policy.excluded_speakers.join(', ') : ''); setExcludedEpisodeIds(Array.isArray(policy?.excluded_episode_ids) ? policy.excluded_episode_ids.map(String) : []); setEpisodeOverrides(policy?.episode_overrides && typeof policy.episode_overrides === 'object' ? policy.episode_overrides : {}); setAssetFilter(String(policy?.asset_filter ?? 'reviewed_speaker_transcript')); setAssetPattern(String(policy?.asset_pattern ?? '')); setExecutionOptions({ embedding_device: String(options?.embedding_device ?? 'auto') }); setContextPinned(false) }).catch(() => window.localStorage.removeItem('chroma-gui-create-draft')) }, [api, draftId])
  useEffect(() => { if (step !== 2 || environment !== null) return; void api.environmentReport().then(setEnvironment).catch(() => setEnvironment({})) }, [api, step, environment])

  const invalidateManagedIdentity = (preserveCatalog = false) => {
    setContextPinned(false)
    setUpstreamReleaseId('')
    setCorpusId('')
    if (!preserveCatalog) {
      setConnectionId('')
      setCatalogPath('')
    }
    setScan(null)
    setScanNeedsRefresh(true)
    setPreview(null)
  }
  const editManagedSource = (value: string) => {
    setSource(value)
    invalidateManagedIdentity()
    if (!targetManuallyEdited) {
      const nextRoot = value.trim() ? `${value.trim().replace(/[\\/]+$/, '')}/exports` : ''
      setManagedOutputRoot(nextRoot)
      setTarget(managedTargetPath(nextRoot, partition))
    }
  }
  const editManagedPartition = (value: string) => {
    setPartition(value)
    invalidateManagedIdentity(true)
    if (!targetManuallyEdited) setTarget(managedTargetPath(managedOutputRoot || `${source.replace(/[\\/]+$/, '')}/exports`, value))
  }
  const editTarget = (value: string) => {
    setTarget(value)
    setTargetManuallyEdited(true)
    setPreview(null)
    const derivedRoot = sourceKind === 'managed' ? managedOutputRootFromTarget(value, partition) : ''
    if (derivedRoot) setManagedOutputRoot(derivedRoot)
  }

  const scanSource = async (kind: 'folder' | 'managed', value: string, selectedPartition = partition) => {
    const revision = ++scanRevision.current
    setScan(null)
    try {
      const payload = kind === 'managed'
        ? { source_kind: kind, source_root: value, partition_id: selectedPartition, ...(catalogPath ? { catalog_path: catalogPath } : {}) }
        : { source_kind: kind, path: value, selection_policy: draftPayload().selection_policy }
      const job = await api.scanSource(payload)
      const complete = await waitForJob(api, job.id)
      if (complete.error) throw new Error(complete.error.message)
      if (revision === scanRevision.current && complete.result) {
        setScan(complete.result)
        setScanNeedsRefresh(false)
        if (kind === 'managed') {
          const result = complete.result
          setUpstreamReleaseId(String(result.latest_release_id ?? result.active_release_id ?? ''))
          setCatalogPath(String(result.catalog_path ?? catalogPath))
          setCorpusId(String(result.corpus_id ?? corpusId))
          if (!contextPinned && source === initialContext?.source_root && selectedPartition === initialContext?.ref.partition_id) {
            setContextPinned(true)
            setConnectionId(String(contextualDefaults?.source_ref.connection_id ?? initialContext?.ref.connection_id ?? ''))
          }
        }
      }
    } catch (exc) { if (revision === scanRevision.current) setError(exc instanceof Error ? exc.message : 'Source scan failed.') }
  }
  const chooseFolder = async () => { try { const picked = await api.pickFolder(); if (picked) { setSource(picked); await scanSource('folder', picked) } } catch (exc) { setError(exc instanceof Error ? exc.message : 'Folder selection failed.') } }
  const chooseTarget = async () => { try { const picked = await api.pickFolder(); if (picked) { setTarget(picked); setTargetManuallyEdited(true); setTargetExists(false); setPreview(null); if (sourceKind === 'managed') setManagedOutputRoot(managedOutputRootFromTarget(picked, partition)) } } catch (exc) { setError(exc instanceof Error ? exc.message : 'Destination selection failed.') } }
  const targetError = sourceKind === 'managed' ? managedTargetError(target, partition) : !target.trim() ? 'A database destination is required.' : null
  const makePreview = async () => {
    if (targetError) {
      setError(targetError)
      return
    }
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
  const apply = async () => {
    if (!preview) return
    setBusy(true)
    try {
      await api.applyPreview(preview.preview_id, preview.required_acknowledgments)
      window.localStorage.removeItem('chroma-gui-create-draft')
      await onDone()
    } catch (exc) {
      if (exc instanceof ClientError && exc.detail.code === 'TARGET_EXISTS') {
        setTargetExists(true)
        setError(null)
      } else {
        setError(exc instanceof Error ? exc.message : 'Could not create database.')
      }
    } finally { setBusy(false) }
  }
  const goBack = () => { void persistDraft(); setStep((current) => current - 1) }
  const ready = sourceKind === 'folder' ? Boolean(source) : Boolean(source && partition)
  const invalidateScan = () => { setPreview(null); setScanNeedsRefresh(true) }
  const invalidateSelection = () => { setPreview(null); if (sourceKind === 'folder') setScanNeedsRefresh(true) }
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
  const deviceChoices = ['auto', 'cpu', ...Array.from({ length: Math.max(0, Number(cuda?.device_count ?? 0)) }, (_, index) => `cuda:${index}`)]
  if (/^cuda:\d+$/.test(executionOptions.embedding_device) && !deviceChoices.includes(executionOptions.embedding_device)) deviceChoices.push(executionOptions.embedding_device)
  const contextualProfile = String(contextualDefaults?.representation.profile ?? 'Qwen3 Embedding 4B')
  const contextualRelease = upstreamReleaseId || String(contextualDefaults?.source_ref.upstream_release_id ?? scan?.latest_release_id ?? scan?.active_release_id ?? 'No validated release selected')
  const contextualSelection = `${speakerMode === 'allowlist' ? 'Allowlisted speakers' : 'All speakers'} · ${assetFilter}`
  if (targetExists) return <section className="flow">
    <header className="page-header"><div><p className="eyebrow">CREATE DATABASE</p><h1>Destination already exists</h1><p className="muted">Creation never overwrites an existing database. Choose another folder or register the existing database.</p></div></header>
    <div className="card flow-card"><div className="info-box"><strong>{target}</strong><small>The existing database has not been changed.</small></div><div className="button-row"><button className="button secondary" onClick={() => { setTargetExists(false); setStep(2) }}>Choose another location</button><button className="button primary" onClick={() => onUseExisting(target, source, sourceKind, sourceKind === 'managed' ? partition : '', sourceKind === 'managed' ? connectionId : '')}>Use existing database</button></div></div>
  </section>
  return <section className="flow">
    <header className="page-header"><div><p className="eyebrow">CREATE DATABASE</p><h1>{step === 1 ? 'Choose content' : step === 2 ? 'Name and location' : 'Review and create'}</h1><p className="muted">A short three-step setup. Your choices are saved as a draft when you go back.</p></div></header>
    <div className="stepper"><span className={step >= 1 ? 'step current' : 'step'}>1 <b>Content</b></span><span className={step >= 2 ? 'step current' : 'step'}>2 <b>Location</b></span><span className={step >= 3 ? 'step current' : 'step'}>3 <b>Review</b></span></div>
    <div className="card flow-card">
      {step === 1 && <>
        <label className="field"><span>Source type</span><select value={sourceKind} onChange={(event) => { const value = event.target.value as 'folder' | 'managed'; setSourceKind(value); setSource(''); setPartition(''); setConnectionId(''); setCatalogPath(''); setCorpusId(''); setUpstreamReleaseId(''); setManagedOutputRoot(''); setTarget(''); setTargetManuallyEdited(false); setScan(null); setPreview(null); setScanNeedsRefresh(false); setContextPinned(false); void persistDraft() }}><option value="folder">Processed files folder</option><option value="managed">Podcast-RAG managed source</option></select></label>
        {initialContext && sourceKind === 'managed' && <div className="context-prefill" role="status"><div><p className="eyebrow">PARTITION-SCOPED CREATION</p><strong>{name || partition}</strong><p className="muted">This wizard is prefilled for the selected managed partition. Known values remain editable; source or partition identity changes require a fresh inspection.</p></div><div className="detail-grid"><div><span className="field-label">Partition</span><code>{partition}</code></div><div><span className="field-label">Latest valid release</span><code>{contextualRelease}</code></div><div><span className="field-label">Profile</span><strong>{contextualProfile}</strong></div><div><span className="field-label">Defaults</span><small>{contextualDefaults?.provenance.output === 'managed_fallback' ? 'Standard managed export path' : 'Resolved from saved settings'}</small></div></div></div>}
        {sourceKind === 'folder' ? <label className="field"><span>Processed folder</span><div className="input-row"><input value={source} onBlur={() => void persistDraft()} onChange={(event) => { setSource(event.target.value); setScan(null); setScanNeedsRefresh(true) }} placeholder="Choose a folder containing processed files" /><button className="button secondary" onClick={() => void chooseFolder()}>Choose folder</button></div></label> : <>
          <label className="field"><span>Podcast-RAG source root</span><input value={source} onBlur={() => void persistDraft()} onChange={(event) => editManagedSource(event.target.value)} placeholder="C:/…/Podcast RAG" /></label>
          <label className="field"><span>Verified partition ID</span><input value={partition} onBlur={() => void persistDraft()} onChange={(event) => editManagedPartition(event.target.value)} placeholder="partition-id" /></label>
          <button className="button secondary" disabled={!ready} onClick={() => void scanSource('managed', source, partition)}>Inspect managed source</button>
        </>}
        {sourceKind === 'folder' && <button className="button secondary" disabled={!ready} onClick={() => void scanSource('folder', source)}>Scan source</button>}
        {scan && <div className="scan-result"><Status tone={scanReady ? 'good' : 'warning'}>{scanReady ? 'Source ready' : 'Source needs attention'}</Status>{sourceKind === 'managed' ? <><strong>{String(scan.completed ?? 0)} completed</strong><span>{String(scan.pending ?? 0)} pending</span><span>{String(scan.failed ?? 0)} failed</span><span>{String(scan.quarantined ?? 0)} quarantined</span></> : <><strong>{String(scan.episodes ?? 0)} episodes</strong><span>{String(scan.eligible_records ?? 0)} eligible records</span><span>{Array.isArray(scan.speakers) ? scan.speakers.length : 0} speakers</span><span>{excludedFileCount} excluded files</span>{(dateRange?.start || dateRange?.end) && <span>{String(dateRange?.start || '—')} → {String(dateRange?.end || '—')}</span>}</>}</div>}
        {sourceKind === 'folder' && scan && <details className="content-picker" open><summary>Choose content <span className="muted">{selectedEpisodeCount} of {episodeInventory.length || String(scan.episodes ?? 0)} episodes included</span></summary><label className="field"><span>Search episodes</span><input value={episodeSearch} onChange={(event) => setEpisodeSearch(event.target.value)} placeholder="Title or episode ID" /></label><label className="field"><span>Speaker policy</span><select value={speakerMode} onChange={(event) => { setSpeakerMode(event.target.value as SelectionPolicy['speaker_mode']); invalidateScan() }}><option value="all">All speakers except exclusions</option><option value="allowlist">Only selected speakers</option></select></label><label className="field"><span>Allowlist speakers</span><input value={allowlistSpeakers} onBlur={() => void persistDraft()} onChange={(event) => { setAllowlistSpeakers(event.target.value); invalidateScan() }} placeholder="Host, Guest" /></label><label className="field"><span>Excluded speakers</span><input value={excludedSpeakers} onBlur={() => void persistDraft()} onChange={(event) => { setExcludedSpeakers(event.target.value); invalidateScan() }} placeholder="Ads, Sponsor" /></label>{filteredInventory.length === 0 ? <p className="muted">No episode inventory matches this search.</p> : <div className="episode-table">{filteredInventory.map((episode) => { const episodeId = String(episode.episode_id); const speakers = Array.isArray(episode.speakers) ? episode.speakers.map(String).join(', ') : ''; return <div className="episode-row" key={episodeId}><label><input type="checkbox" checked={!excludedEpisodeIds.includes(episodeId)} onChange={() => toggleExcludedEpisode(episodeId)} /><span><strong>{String(episode.title ?? episodeId)}</strong><small>{episodeId} · {String(episode.document_count ?? 0)} records · {speakers || 'shared context'}</small></span></label><input aria-label={`Speaker override for ${episodeId}`} value={(episodeOverrides[episodeId] ?? []).join(', ')} onBlur={() => void persistDraft()} onChange={(event) => setEpisodeOverride(episodeId, event.target.value)} placeholder="Episode speaker override" /></div> })}</div>}<p className="muted">Shared episode context remains available when at least one selected speaker is included.</p></details>}
        {sourceKind === 'folder' && <label className="field"><span>Asset filter</span><select value={assetFilter} onChange={(event) => { setAssetFilter(event.target.value); invalidateScan() }} onBlur={() => void persistDraft()}><option value="reviewed_speaker_transcript">Reviewed speaker transcripts</option><option value="cleaned_speaker_transcript">Cleaned speaker transcripts</option><option value="speaker_transcript">Raw speaker transcripts</option><option value="all">All processed assets</option></select></label>}
        {sourceKind === 'managed' && <details className="content-picker" open><summary>Import selection <span className="muted">{contextualSelection}</span></summary><label className="field"><span>Speaker policy</span><select value={speakerMode} onChange={(event) => { setSpeakerMode(event.target.value as SelectionPolicy['speaker_mode']); invalidateSelection() }}><option value="all">All speakers except exclusions</option><option value="allowlist">Only selected speakers</option></select></label><label className="field"><span>Allowlist speakers</span><input value={allowlistSpeakers} onBlur={() => void persistDraft()} onChange={(event) => { setAllowlistSpeakers(event.target.value); invalidateSelection() }} placeholder="Host, Guest" /></label><label className="field"><span>Excluded speakers</span><input value={excludedSpeakers} onBlur={() => void persistDraft()} onChange={(event) => { setExcludedSpeakers(event.target.value); invalidateSelection() }} placeholder="Ads, Sponsor" /></label><label className="field"><span>Asset filter</span><select value={assetFilter} onChange={(event) => { setAssetFilter(event.target.value); invalidateSelection() }} onBlur={() => void persistDraft()}><option value="reviewed_speaker_transcript">Reviewed speaker transcripts</option><option value="cleaned_speaker_transcript">Cleaned speaker transcripts</option><option value="speaker_transcript">Raw speaker transcripts</option><option value="all">All processed assets</option><option value="custom">Custom pattern</option></select></label>{assetFilter === 'custom' && <label className="field"><span>Custom filename pattern</span><input value={assetPattern} onBlur={() => void persistDraft()} onChange={(event) => { setAssetPattern(event.target.value); invalidateSelection() }} placeholder="*reviewed*.json" /></label>}<p className="muted">Selection edits preserve the inspected managed release and invalidate only this creation review.</p></details>}
      </>}
      {step === 2 && <>
        {sourceKind === 'managed' && <div className="context-prefill"><div><p className="eyebrow">MANAGED PARTITION</p><strong>{partition}</strong><p className="muted">The output field is the exact managed partition target. The service will not add another nested <code>partitions/{partition}</code> segment.</p></div><div className="detail-grid"><div><span className="field-label">Source root</span><code>{source || 'Not set'}</code></div><div><span className="field-label">Release</span><code>{contextualRelease}</code></div></div></div>}
        <label className="field"><span>Database name</span><input autoFocus value={name} onBlur={() => void persistDraft()} onChange={(event) => { setName(event.target.value); setPreview(null) }} placeholder="e.g. Weekly Podcast" /></label>
        <label className="field"><span>Storage location</span><div className="input-row"><input aria-label="Storage location" value={target} onBlur={() => void persistDraft()} onChange={(event) => sourceKind === 'managed' ? editTarget(event.target.value) : (() => { setTarget(event.target.value); setPreview(null) })()} placeholder={sourceKind === 'managed' ? '…/exports/partitions/partition-id' : 'Choose a final database destination'} /><button className="button secondary" onClick={() => void chooseTarget()}>Choose folder</button></div>{targetError && <small className="field-error" role="alert">{targetError}</small>}</label>
        {sourceKind === 'managed' && <label className="field"><span>Embedding device</span><select value={executionOptions.embedding_device} onChange={(event) => { setExecutionOptions({ embedding_device: event.target.value }); setPreview(null) }}>{deviceChoices.map((device) => <option value={device} key={device}>{device === 'auto' ? 'Automatic' : device === 'cpu' ? 'CPU' : `CUDA device ${device.slice(5)}`}</option>)}</select></label>}
        <div className="info-box"><span>Embedding profile</span><strong>{sourceKind === 'managed' ? contextualProfile : 'Qwen3 Embedding 4B'}</strong><small><Status tone={readinessTone}>{readinessText}</Status> {sourceKind === 'managed' ? <>Effective profile is read-only. Selection: {contextualSelection}.</> : <>Device changes affect speed; a representation change requires a separate database.</>}</small></div>
        <details><summary>Technical details</summary><p className="muted">IDs and collection names are generated by the Python service and are not derived from the display name. Existing destinations are never overwritten; use Add existing to register one.</p></details>
      </>}
      {step === 3 && <>{preview ? <><div className="review-heading"><div><Status tone={preview.validation_findings.length ? 'warning' : 'good'}>{preview.validation_findings.length ? 'Review warnings' : 'Ready to create'}</Status><h2>{name || 'New database'}</h2><p className="muted">{source} → {preview.target_identity.path}</p></div></div><div className="detail-grid"><div><span className="field-label">Source identity</span><code>{sourceKind === 'managed' ? `${source} · ${partition}` : source}</code></div><div><span className="field-label">Selected release</span><code>{String(preview.source_snapshot.release_id ?? contextualRelease)}</code></div><div><span className="field-label">Effective profile</span><strong>{String(preview.representation.profile ?? contextualProfile)}</strong></div><div><span className="field-label">Device</span><strong>{executionOptions.embedding_device}</strong></div><div><span className="field-label">Selection policy</span><strong>{contextualSelection}</strong></div><div><span className="field-label">Exact target</span><code>{String(preview.target_identity.path ?? target)}</code></div></div><div className="metric-grid"><Metric label="Episodes" value={preview.effects.episodes_total} /><Metric label="Records" value={preview.effects.records_total} /><Metric label="Writes" value={preview.effects.writes} /></div>{preview.validation_findings.map((finding) => <div className="finding" key={finding.code}>{finding.message}</div>)}</> : <div className="loading-panel">{busy ? 'Preparing review…' : 'Review has not been prepared yet.'}</div>}</>}
    </div>
    <footer className="flow-footer"><button className="button secondary" onClick={step === 1 ? onCancel : goBack}>Back</button>{step < 3 ? <button className="button primary" disabled={(step === 1 && (!ready || !scanReady)) || (step === 2 && (!name.trim() || Boolean(targetError)))} onClick={() => { if (step === 2) void makePreview(); else void persistDraft(); setStep((current) => current + 1) }}>{step === 2 ? 'Review' : 'Continue'}</button> : <button className="button primary" disabled={!preview || busy || Boolean(preview?.validation_findings.some((item) => item.severity === 'error'))} onClick={() => void apply()}>{busy ? 'Creating…' : 'Create database'}</button>}</footer>
  </section>
}

function AddExisting({ api, initialTarget, initialSource, initialSourceKind = 'folder', initialPartition = '', initialConnectionId = '', onCancel, onDone, setError }: { api: AppClient; initialTarget: string; initialSource: string; initialSourceKind?: 'folder' | 'managed'; initialPartition?: string; initialConnectionId?: string; onCancel: () => void; onDone: () => Promise<void>; setError: (v: string | null) => void }) {
  const [target, setTarget] = useState(initialTarget)
  const [sourceKind, setSourceKind] = useState<'folder' | 'managed'>(initialSourceKind)
  const [source, setSource] = useState(initialSource)
  const [partition, setPartition] = useState(initialPartition)
  const [proposal, setProposal] = useState<Record<string, unknown> | null>(null)
  const choose = async (setter: (value: string) => void) => { try { const picked = await api.pickFolder(); if (picked) setter(picked) } catch (exc) { setError(exc instanceof Error ? exc.message : 'Folder selection failed.') } }
  const inspect = async () => {
    try {
      const inspected = await api.inspectExisting({ target_path: target, source_kind: sourceKind, source_path: source, source_root: source, partition_id: partition })
      if (sourceKind === 'managed' && initialConnectionId && inspected.source_ref && typeof inspected.source_ref === 'object') {
        inspected.source_ref = { ...(inspected.source_ref as Record<string, unknown>), connection_id: initialConnectionId }
      }
      setProposal(inspected)
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

function Update({ api, db, sourceReleaseId, preview, setPreview, onBack, onSourceConnections, onRepairIdentity, onCreateSeparate, onApplied, setError }: { api: AppClient; db: DatabaseRecord; sourceReleaseId: string | null; preview: Preview | null; setPreview: (p: Preview | null) => void; onBack: () => void; onSourceConnections: () => void; onRepairIdentity: () => void; onCreateSeparate: () => void; onApplied: () => Promise<void>; setError: (v: string | null) => void }) {
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
      const job = sourceReleaseId ? await api.createPreview({ database_id: db.id, operation: 'update', upstream_release_id: sourceReleaseId }) : await api.checkDatabase(db.id)
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
      const job = await api.createPreview({ database_id: db.id, operation: 'update', selection_policy: selectionPolicy, ...(sourceReleaseId ? { upstream_release_id: sourceReleaseId } : {}) })
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
  const [content, setContent] = useState<ContentInventory | null>(null)
  const [search, setSearch] = useState('')
  useEffect(() => { void api.getDatabaseContent(db.id).then(setContent).catch(() => setContent(null)) }, [api, db.id])
  const episodes = (Array.isArray(content?.episodes) ? content.episodes : []) as unknown as Array<Record<string, unknown>>
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
  const [repairReview, setRepairReview] = useState<Record<string, unknown> | null>(null)
  const [settingsProposal, setSettingsProposal] = useState<Record<string, unknown> | null>(null)
  const [settingsPath, setSettingsPath] = useState<string | null>(null)
  const [migration, setMigration] = useState<Record<string, unknown> | null>(null)
  const [busy, setBusy] = useState(false)
  const [repairStatus, setRepairStatus] = useState<string | null>(null)
  const repairCommand = '.\\scripts\\Run-ChromaDbImportUi.ps1 -InstallDependencies -Ui Modern -NoLaunch'
  const diagnose = async () => { setBusy(true); try { setReport(await api.environmentReport()) } finally { setBusy(false) } }
  const reviewRepair = async () => { setBusy(true); setRepairStatus(null); try { setRepairReview(await api.previewEnvironmentRepair()) } catch (exc) { setRepairStatus(exc instanceof Error ? exc.message : 'Repair review could not be prepared.') } finally { setBusy(false) } }
  const applyRepair = async () => { const reviewId = String(repairReview?.review_id || ''); if (!reviewId) return; setBusy(true); setRepairStatus(null); try { const job = await api.applyEnvironmentRepair(reviewId); setRepairStatus(`Repair queued as ${job.id}. Watch Activity for the installer result.`); setRepairReview(null) } catch (exc) { setRepairStatus(exc instanceof Error ? exc.message : 'Repair could not be started.') } finally { setBusy(false) } }
  const inspectMigration = async () => { setBusy(true); try { setMigration(await api.migrationCandidates()) } finally { setBusy(false) } }
  const exportDefaults = async () => { const path = await api.pickSaveFile('settings_export'); if (!path) return; setBusy(true); try { const transfer = await api.exportSettings('app_defaults'); const saved = await api.saveSettingsTransfer(transfer, path); setRepairStatus(`Application defaults exported to ${String(saved.path ?? path)}.`) } catch (exc) { setRepairStatus(exc instanceof Error ? exc.message : 'Settings export failed.') } finally { setBusy(false) } }
  const reviewSettingsImport = async () => { const path = await api.pickFile('settings_import'); if (!path) return; setBusy(true); try { setSettingsPath(path); setSettingsProposal(await api.previewSettingsImport(path, 'app_defaults')) } catch (exc) { setRepairStatus(exc instanceof Error ? exc.message : 'Settings import could not be reviewed.') } finally { setBusy(false) } }
  const applySettingsProposal = async () => { if (!settingsProposal || !settingsPath) return; const fields = Array.isArray(settingsProposal.allowed_field_paths) ? settingsProposal.allowed_field_paths.filter((item): item is string => typeof item === 'string') : []; setBusy(true); try { const result = await api.applySettingsImport(settingsPath, settingsProposal, fields, 'app_defaults'); setRepairStatus(`Applied ${String((result.applied_fields as string[] | undefined)?.length ?? 0)} reviewed application-default fields.`); setSettingsProposal(null); setSettingsPath(null) } catch (exc) { setRepairStatus(exc instanceof Error ? exc.message : 'Settings import could not be applied.') } finally { setBusy(false) } }
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
      <div className="button-row"><button className="button secondary" disabled={busy} onClick={() => void diagnose()}>{busy ? 'Checking…' : 'Run environment diagnosis'}</button><button className="button secondary" disabled={busy} onClick={() => void reviewRepair()}>Review repair</button><button className="button secondary" onClick={() => void copyRepairCommand()}>Copy repair command</button></div>
      <p className="muted">Repair is explicit and uses the supported launcher; it never runs automatically when this page opens.</p>
      {repairStatus && <p className="info-box" role="status">{repairStatus}</p>}
      {repairReview && <div className="review-box"><Status tone="warning">Review required</Status><p>{String(repairReview.impact || 'The current environment will be updated by the supported launcher.')}</p><code>{Array.isArray(repairReview.args) ? repairReview.args.join(' ') : 'Allowlisted launcher arguments'}</code><div className="button-row"><button className="button primary" disabled={busy} onClick={() => void applyRepair()}>Apply repair</button><button className="button secondary" disabled={busy} onClick={() => setRepairReview(null)}>Cancel</button></div></div>}
      {report && <div className="review-box"><Status tone={report.assets_ready === true && Boolean(report.pywebview) ? 'good' : 'warning'}>{report.assets_ready === true && Boolean(report.pywebview) ? 'Desktop ready' : 'Review setup'}</Status><pre>{JSON.stringify(report, null, 2)}</pre></div>}
    </section>
    <section className="card settings-card"><h2>Legacy-state migration</h2><p className="muted">Inspect a field-level proposal before accepting migration. Original state and managed catalog files remain unchanged.</p><button className="button secondary" disabled={busy} onClick={() => void inspectMigration()}>{busy ? 'Inspecting…' : 'Review migration candidates'}</button>{migration && (candidates.length === 0 ? <p className="muted">No migration candidates were found.</p> : <div className="event-log">{candidates.map((candidate, index) => { const changes = candidate.changes && typeof candidate.changes === 'object' ? candidate.changes : null; const message = typeof candidate.message === 'string' ? candidate.message : ''; return <div key={`${String(candidate.path)}-${index}`}><strong>{String(candidate.kind)}</strong> · {String(candidate.status)} · {String(candidate.path)}{message && <p className="muted">{message}</p>}{changes && <details><summary>View proposed field changes</summary><pre>{JSON.stringify(changes, null, 2)}</pre></details>}</div> })}</div>)}</section>
    <section className="card placeholder-panel"><h2>Legacy interface</h2><p>The existing Qt interface remains available through the launcher while the modern UI is being verified.</p><p className="muted">For accepted migration, run <code>scripts\\Migrate-LegacyChromaDbImportState.ps1</code> from the repository.</p></section>
    <section className="card settings-card"><h2>Scoped settings transfer</h2><p className="muted">Transfers contain only application defaults. Every import is reviewed field by field; runtime configuration, secrets, jobs, active pointers, and database content are excluded.</p><div className="button-row"><button className="button secondary" disabled={busy} onClick={() => void exportDefaults()}>Export application defaults</button><button className="button secondary" disabled={busy} onClick={() => void reviewSettingsImport()}>Review settings import</button></div>{settingsProposal && <div className="review-box"><Status tone={settingsProposal.compatibility === 'compatible' ? 'good' : 'danger'}>{String(settingsProposal.compatibility ?? 'Review')}</Status><pre>{JSON.stringify(settingsProposal.changes ?? {}, null, 2)}</pre><div className="button-row"><button className="button primary" disabled={busy || settingsProposal.compatibility !== 'compatible'} onClick={() => void applySettingsProposal()}>Apply selected fields</button><button className="button" disabled={busy} onClick={() => { setSettingsProposal(null); setSettingsPath(null) }}>Cancel</button></div></div>}</section>
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
