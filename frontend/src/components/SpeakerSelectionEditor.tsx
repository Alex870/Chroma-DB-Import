import { useEffect, useMemo, useRef, useState } from 'react'
import type { EpisodeInspection, SelectionPolicy } from '../api/types'

type EpisodeDraft = Pick<EpisodeInspection, 'episode_id' | 'title' | 'speakers' | 'override_mode' | 'override_speakers'> & { excluded?: boolean }
type Props = { policy: SelectionPolicy; episodes: EpisodeDraft[]; onChange: (policy: SelectionPolicy) => void; onSave?: () => void; onReview?: () => void; busy?: boolean }

function parse(value: string): string[] { return [...new Set(value.split(',').map((item) => item.trim()).filter(Boolean))].sort() }

function MixedCheckbox({ checked, mixed, label, onChange }: { checked: boolean; mixed?: boolean; label: string; onChange: () => void }) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { if (ref.current) ref.current.indeterminate = Boolean(mixed) }, [mixed])
  return <label className="speaker-option"><input ref={ref} type="checkbox" aria-label={label} aria-checked={mixed ? 'mixed' : checked} checked={checked} onChange={onChange} />{label}{mixed && <span className="muted"> (some)</span>}</label>
}

export function SpeakerSelectionEditor({ policy, episodes, onChange, onSave, onReview, busy = false }: Props) {
  const [search, setSearch] = useState('')
  const [speakerSearch, setSpeakerSearch] = useState('')
  const visible = useMemo(() => episodes.filter((episode) => `${episode.title} ${episode.episode_id}`.toLowerCase().includes(search.toLowerCase())), [episodes, search])
  const allSpeakers = useMemo(() => [...new Set(episodes.flatMap((episode) => episode.speakers))].sort(), [episodes])
  const shownSpeakers = allSpeakers.filter((speaker) => speaker.toLowerCase().includes(speakerSearch.toLowerCase()))
  const applicable = (speaker: string) => episodes.filter((episode) => !episode.excluded && episode.speakers.includes(speaker))
  const includedFor = (episode: EpisodeDraft, speaker: string) => {
    if (episode.override_mode === 'custom') return episode.override_speakers.includes(speaker)
    return policy.speaker_mode === 'all' ? !policy.excluded_speakers.includes(speaker) : policy.allowlist_speakers.includes(speaker)
  }
  const update = (changes: Partial<SelectionPolicy>) => onChange({ ...policy, ...changes })
  const toggleGlobal = (speaker: string) => {
    const rows = applicable(speaker); const included = rows.filter((episode) => includedFor(episode, speaker)).length
    const next = included < rows.length
    const overrides = { ...policy.episode_overrides }
    for (const episode of rows) {
      const current = episode.override_mode === 'custom' ? new Set(overrides[episode.episode_id] ?? []) : new Set(episode.speakers.filter((item) => includedFor(episode, item)))
      if (next) current.add(speaker); else current.delete(speaker)
      overrides[episode.episode_id] = [...current].sort()
    }
    update({ speaker_mode: 'allowlist', allowlist_speakers: allSpeakers.filter((item) => item === speaker || policy.allowlist_speakers.includes(item)), excluded_speakers: [], episode_overrides: overrides })
  }
  const setGlobal = (mode: 'all' | 'none') => update(mode === 'all' ? { speaker_mode: 'all', excluded_speakers: [], allowlist_speakers: [], episode_overrides: {} } : { speaker_mode: 'allowlist', allowlist_speakers: [], excluded_speakers: [], episode_overrides: {} })
  const setEpisode = (episode: EpisodeDraft, mode: 'default' | 'custom', speakers: string[] = []) => {
    const overrides = { ...policy.episode_overrides }
    if (mode === 'default') delete overrides[episode.episode_id]; else overrides[episode.episode_id] = speakers
    update({ episode_overrides: overrides })
  }
  const setEpisodeSpeakers = (episode: EpisodeDraft, speaker: string) => {
    const current = new Set(policy.episode_overrides[episode.episode_id] ?? [])
    if (current.has(speaker)) current.delete(speaker); else current.add(speaker)
    setEpisode(episode, 'custom', [...current].sort())
  }
  const bulk = (exclude: boolean) => update({ excluded_episode_ids: exclude ? [...new Set([...policy.excluded_episode_ids, ...visible.map((item) => item.episode_id)])].sort() : policy.excluded_episode_ids.filter((id) => !visible.some((item) => item.episode_id === id)) })
  return <section className="selection-editor" aria-label="Content selection">
    <div className="selection-heading"><div><h3>Content selection</h3><p className="muted">Draft changes remain local until you save defaults or review this selection.</p></div><span className="status">{policy.speaker_mode === 'all' ? 'All speakers with exclusions' : 'Selected speakers only'}</span></div>
    <div className="speaker-toolbar"><label className="field"><span>Speaker search</span><input value={speakerSearch} onChange={(event) => setSpeakerSearch(event.target.value)} placeholder="Filter visible speakers" /></label><div className="button-row"><button className="button small" onClick={() => setGlobal('all')}>All speakers</button><button className="button small" onClick={() => setGlobal('none')}>No speakers</button></div></div>
    <div className="speaker-list">{shownSpeakers.map((speaker) => { const rows = applicable(speaker); const count = rows.filter((episode) => includedFor(episode, speaker)).length; return <MixedCheckbox key={speaker} label={speaker} checked={rows.length > 0 && count === rows.length} mixed={count > 0 && count < rows.length} onChange={() => toggleGlobal(speaker)} /> })}</div>
    <label className="field"><span>Episode search</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Title or episode ID" /></label>
    <div className="button-row"><button className="button small" onClick={() => bulk(false)} disabled={visible.length === 0}>Select all shown ({visible.length})</button><button className="button small" onClick={() => bulk(true)} disabled={visible.length === 0}>Clear shown ({visible.length})</button></div>
    <div className="episode-table">{visible.map((episode) => { const excluded = policy.excluded_episode_ids.includes(episode.episode_id); const custom = episode.episode_id in policy.episode_overrides; const chosen = policy.episode_overrides[episode.episode_id] ?? []; return <div className="episode-row" key={episode.episode_id}><label><input type="checkbox" aria-label={`Include ${episode.title}`} checked={!excluded} onChange={() => update({ excluded_episode_ids: excluded ? policy.excluded_episode_ids.filter((id) => id !== episode.episode_id) : [...policy.excluded_episode_ids, episode.episode_id].sort() })} /><span><strong>{episode.title}</strong><small>{episode.episode_id} · {episode.speakers.join(', ') || 'shared context'}</small></span></label><div className="episode-controls"><select aria-label={`Speaker mode for ${episode.episode_id}`} value={custom ? 'custom' : 'default'} onChange={(event) => setEpisode(episode, event.target.value as 'default' | 'custom', event.target.value === 'custom' ? [] : [])}><option value="default">Use database default</option><option value="custom">Custom speakers</option></select>{custom && <div className="speaker-list compact">{episode.speakers.map((speaker) => <label key={speaker}><input type="checkbox" checked={chosen.includes(speaker)} onChange={() => setEpisodeSpeakers(episode, speaker)} />{speaker}</label>)}</div>}</div></div> })}</div>
    {visible.length === 0 && <p className="muted">No episodes match this search.</p>}
    <p className="muted">Unattributed and shared-context records are evaluated by the Python selection resolver; speaker names are not used as a document-count shortcut.</p>
    <div className="button-row">{onSave && <button className="button secondary" disabled={busy} onClick={onSave}>Save content defaults</button>}{onReview && <button className="button primary" disabled={busy} onClick={onReview}>Review this selection</button>}</div>
  </section>
}

export default SpeakerSelectionEditor
