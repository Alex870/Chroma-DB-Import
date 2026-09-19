import type { EpisodeInspection } from '../api/types'

function value(item: number | null | undefined): string { return item === null || item === undefined ? 'Not available' : String(item) }

export function EpisodeDetails({ episode, onClose }: { episode: EpisodeInspection; onClose: () => void }) {
  return <section className="episode-details" aria-label={`Details for ${episode.title}`}>
    <div className="detail-heading"><div><h3>{episode.title}</h3><p className="muted">{episode.date ?? 'Date not available'} · {episode.comparison.replaceAll('_', ' ')}</p></div><button className="button small" onClick={onClose}>Close</button></div>
    <div className="detail-grid"><div><span className="field-label">Source file</span><code>{episode.source_file ?? 'Not available'}</code></div><div><span className="field-label">Source documents</span><strong>{value(episode.source_document_count)}</strong></div><div><span className="field-label">Included by draft</span><strong>{value(episode.included_document_count)}</strong></div><div><span className="field-label">Stored documents</span><strong>{value(episode.stored_document_count)}</strong></div><div><span className="field-label">Leaf chunks</span><strong>{value(episode.node_counts.leaf_chunk)}</strong></div><div><span className="field-label">Position cards</span><strong>{value(episode.node_counts.position_card)}</strong></div><div><span className="field-label">Cluster summaries</span><strong>{value(episode.node_counts.cluster_summary)}</strong></div><div><span className="field-label">Episode theses</span><strong>{value(episode.node_counts.episode_thesis)}</strong></div></div>
    </section>
}

export default EpisodeDetails
