# Roadmap

Updated: 2026-07-17

`Chroma DB Import` is the transactional corpus-release boundary between processed podcast artifacts and self-contained vector exports. Versioned manifests, hash-driven classification, embedding governance, staging validation, reconciliation, rollback evidence, and downstream contract fixtures are implemented. The next work makes corpus updates routine, observable, and scalable.

## Product Direction

- Treat every promoted export as an immutable, identifiable corpus release.
- Apply the smallest safe delta while preserving rollback and historical evidence.
- Never mix incompatible embedding spaces or representation identities.
- Make every inserted, updated, retained, omitted, and removed vector explainable.
- Keep exports self-contained and readable by Chat and RAGScope without importer internals.

## Current Foundation

- Generate/update/reconcile workflows with episode and speaker selection.
- Source/content identity classification and exact embedding cache keys.
- Complete embedding fingerprints, pinned provider probes, and mismatch refusal.
- Temporary staging, dimension/finite/evidence validation, smoke queries, promotion, and failure reports.
- Default-retain deletion semantics with explicit reconcile mode and reasons.
- Versioned `podcast.json` and import manifest consumed by Chat and RAGScope fixtures.
- Clean-machine dependency pins, diagnostics, and UI progress reporting.

## Value-Ordered Priorities

### 1. Introduce corpus releases and delta ingestion

- Consume Podcast-RAG delta manifests directly instead of rediscovering every change from directory state.
- Assign a corpus release ID spanning source caches, representation, embedding fingerprint, selection, and parent release.
- Preview the exact vector and metadata delta before work begins.
- Promote atomically, retain a bounded rollback generation, and verify Chat/RAGScope readiness after promotion.
- Emit stale-evidence and judgment-impact information for RAGScope.

### 2. Automate export health and recovery

- Add preflight estimates for document count, embedding work, cache reuse, disk space, and expected duration.
- Add resumable background jobs, cancellation checkpoints, and recovery from interrupted staging/promotion.
- Provide backup/restore, retention, and manifest migration commands.
- Produce one actionable health summary covering metadata, hierarchy, duplicates, exclusions, smoke queries, and rollback state.

### 3. Support measured retrieval representations

- Import/select dense representation IDs explicitly and preserve lexical fields for hybrid retrievers.
- Add side-by-side migration plans for new embedding spaces rather than in-place replacement.
- Benchmark batching, embedding cache layout, and collection write sizes on representative corpora.
- Support optional auxiliary indexes only after RAGScope shows a judged retrieval gap.

### 4. Improve selection and reconciliation UX

- Show why an episode/speaker is new, changed, unchanged, newly eligible, stale, or excluded.
- Add diff views for source identity, representation, selection, and expected downstream effects.
- Require confirmation for destructive reconcile operations and export a rollback plan first.
- Make headless plans and UI plans use the same validated core.

### 5. Harden interoperability and packaging

- Run generated-export smoke checks through Chat scanning and RAGScope provenance APIs in the release path.
- Add large-corpus and low-disk target-machine tests.
- Document portable export layout, migration compatibility, and safe deletion boundaries.
- Package only after cache-free Windows and rollback drills pass.

## Sequencing

1. Adopt processed-cache delta manifests and corpus release identity.
2. Add preview, resumability, rollback generations, and post-promotion consumer checks.
3. Establish operational health, backup/restore, and migration workflows.
4. Run scale/performance tuning on representative releases.
5. Add new representation/index support only from measured retrieval needs.
6. Complete destructive-operation UX and target-machine packaging validation.

The ecosystem-level sequence and promotion rules live in `../PODCAST_ECOSYSTEM_ROADMAP.md` when these repositories share a workspace.
## Phases 0–2 implementation status (2026-07-17)

Processed-delta consumption and the approved staged corpus-release lifecycle, retention, promotion, and rollback are implemented. Real release acceptance awaits the approved private evaluation pack.
