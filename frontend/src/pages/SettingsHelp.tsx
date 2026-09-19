import { useMemo, useState } from 'react'
import type { AppClient } from '../api/client'
import { GUIDE_ARTICLES } from '../help/guide'

export function SettingsHelp({ api: _api }: { api: AppClient }) {
  const [query, setQuery] = useState('')
  const [article, setArticle] = useState<string | null>(null)
  const visible = useMemo(() => GUIDE_ARTICLES.filter((item) => `${item.title} ${item.summary} ${item.body}`.toLowerCase().includes(query.toLowerCase())), [query])
  const selected = GUIDE_ARTICLES.find((item) => item.id === article)
  return <section className="card settings-card guide-panel"><div className="detail-heading"><div><p className="eyebrow">SETTINGS & HELP</p><h2>Guide</h2><p className="muted">Short, current instructions for the Modern workflow.</p></div></div>{selected ? <><button className="button" onClick={() => setArticle(null)}>Back to Guide</button><h3>{selected.title}</h3><p>{selected.body}</p></> : <><label className="field"><span>Search guide</span><input aria-label="Search guide" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search create, update, recovery…" /></label><div className="guide-list">{visible.map((item) => <button key={item.id} className="guide-item" onClick={() => setArticle(item.id)}><strong>{item.title}</strong><span>{item.summary}</span></button>)}</div></>}</section>
}

export default SettingsHelp
