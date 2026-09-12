# State-of-the-Art Implementation Plan: Chroma DB Import

Last updated: July 2026

## Objective

Evolve Chroma DB Import from a reliable Qwen3-only single-vector index builder into a versioned, measurable indexing platform that can safely add lexical and other separately approved representations without weakening its local-first workflow, resumability, or downstream compatibility.

The plan front-loads changes that are fully contained in this repository. Cross-repository work begins only after local schemas, adapters, fixtures, and compatibility behavior are stable. Hard research migrations are intentionally last.

## Guiding Constraints

- Preserve the pinned Qwen3 representation as the only supported default.
- Reject incompatible BGE and other legacy exports; rebuild them as fresh Qwen3 exports.
- Treat every representation change as a versioned index migration, not an in-place mutation.
- Preserve stable document IDs and primary-evidence provenance.
- Support CPU operation; GPU acceleration may improve throughput but must not be mandatory.
- Every new capability must be feature-flagged and represented in the export manifest.

## Phase Summary

| Phase | Capability | Effort | Scope | Other repositories touched |
|---|---|---|---|---|
| 1 | Representation and manifest contracts | Easy-medium | Repo-local | None |
| 2 | Content-addressed reconciliation | Easy-medium | Repo-local | None |
| 3 | Deterministic contextual headers | Medium | Repo-local | None |
| 4 | Pinned Qwen3 provider and fresh-export workflow | Medium | Repo-local | None initially |
| 5 | Matryoshka-ready dimension experiments | Medium | Repo-local | None initially |
| 6 | Lexical sidecar index generation | Medium | Repo-local first | `PodCast Chat`, later `RAGScope` |
| 7 | Shared judged-query and run contracts | Medium | Multi-repo | `RAGScope`, `PodCast Chat`; optionally `Podcast-RAG-pipeline` |
| 8 | Dual-index compatibility and A/B release flow | Medium | Multi-repo | `PodCast Chat`, `RAGScope` |
| 9 | Deterministic provenance graph | Medium-hard | Multi-repo | `Podcast-RAG-pipeline`, `PodCast Chat`, `RAGScope` |
| 10 | True late chunking | Hard | Multi-repo | `Podcast-RAG-pipeline`, `PodCast Chat`, `RAGScope` |
| 11 | Multi-vector / late-interaction indexing | Hard | Multi-repo | `PodCast Chat`, `RAGScope` |
| 12 | GraphRAG/HippoRAG and domain adaptation | Hard | Multi-repo | All podcast pipeline repos |

## Phase 1: Representation and Manifest Contracts

Scope: repo-local.

Add a versioned representation model before adding new embedding behavior.

Deliverables:

- Introduce an `index_schema_version` independent from the application version.
- Add manifest fields for representation provider, model revision, dimension, normalization, distance metric, contextualization method, truncation policy, sparse-index identity, and build timestamp.
- Add a typed internal `RepresentationSpec` used by CLI, UI, importer, diagnostics, and manifest writer.
- Define compatibility states: exact match, readable legacy, migration required, and unsupported.
- Extend preflight validation to reject partial or contradictory representation metadata.
- Add fixtures for current legacy manifests and the new schema.

Tests and exit criteria:

- Existing exports still validate as readable legacy exports.
- New exports round-trip through manifest serialization.
- Invalid model/dimension/context combinations fail before embedding begins.
- CLI and UI display the same representation summary.

## Phase 2: Content-Addressed Reconciliation

Scope: repo-local.

Complete the incremental-update model by handling changed and removed documents, not only new or already-seen IDs.

Deliverables:

- Compute a stable content fingerprint from normalized document text plus representation-affecting metadata.
- Compare source inventory, previous manifest, batch state, embedding cache, and Chroma IDs before writes.
- Classify records as unchanged, added, changed, removed, or metadata-only changed.
- Add dry-run reconciliation reports and explicit delete/tombstone handling.
- Make interrupted reconciliation resumable and idempotent.
- Record counts and fingerprints in the final manifest.

Tests and exit criteria:

- Re-running an unchanged import performs no embeddings or writes.
- Changing content updates exactly the affected record.
- Removing a source record removes or tombstones it according to configuration.
- Failed batches can resume without duplicate records.

## Phase 3: Deterministic Contextual Headers

Scope: repo-local.

Add broader context to embeddings without changing displayed document text.

Deliverables:

- Build a deterministic embedding-only header from podcast, episode title/date, speaker, node type, hierarchy level, topic label, and parent label when present.
- Keep `document_text` and `embedding_text` distinct in diagnostics and cache fingerprints.
- Add configuration profiles: `none`, `minimal`, and `full`.
- Include header format/version in representation metadata.
- Add a preview command/UI panel showing the exact text sent to the embedding model.

Tests and exit criteria:

- Headers are stable across repeated builds and operating systems.
- Missing metadata produces a valid minimal header without placeholder noise.
- Original display/citation text remains unchanged.
- Cache keys change when contextualization changes.

## Phase 4: Pinned Qwen3 Provider and Fresh-Export Workflow

Scope: repo-local initially.

Keep model loading and encoding decoupled from Chroma writes while enforcing the single pinned Qwen3 representation and explicit rebuild boundaries.

Deliverables:

- Define an embedding provider interface for model load, encode, normalize, dimension discovery, device selection, and model revision reporting.
- Adapt the Sentence Transformers path to the Qwen3-only provider interface.
- Reject non-Qwen models, revisions, dimensions, caches, collections, and upstream releases before embedding or writing.
- Write Qwen3 exports to representation-scoped directories and collections.
- Add a fresh-export/rebuild workflow from one validated source inventory; never reconcile Qwen3 vectors into an incompatible collection.
- Report import time, throughput, memory, dimension, and disk footprint.

Tests and exit criteria:

- The provider produces normalized 2,560-dimensional Qwen3 vectors with the pinned revision.
- Qwen3 exports cannot overwrite or reconcile incompatible indexes.
- Manifest and collection metadata uniquely identify each representation.
- CPU and CUDA selection fail gracefully with actionable diagnostics.

## Phase 5: Matryoshka-Ready Dimension Experiments

Scope: repo-local initially.

Support experimental dimension truncation only for models whose documentation and metadata explicitly permit it.

Deliverables:

- Add optional `output_dimension` to the representation spec.
- Reject truncation for models not declared Matryoshka-compatible.
- Normalize vectors after truncation when required by the provider.
- Build versioned indexes at selected dimensions and record size/throughput deltas.
- Add a benchmark report template for downstream quality results.

Tests and exit criteria:

- Dimension validation prevents accidental slicing of ordinary embeddings.
- Chroma collection dimension always matches manifest dimension.
- Full and truncated indexes coexist and are independently inspectable.

Decision gate: proceed beyond experimentation only if corpus size or query latency is a demonstrated constraint and RAGScope shows acceptable quality retention.

## Phase 6: Lexical Sidecar Index Generation

Scope: repo-local implementation first; integration is multi-repo.

Produce a portable lexical search artifact while retaining Chroma as the dense index.

Repo-local deliverables:

- Define normalized `search_text` assembled from primary text plus selected metadata fields.
- Build a compact BM25 sidecar keyed by the same stable document IDs used in Chroma.
- Version tokenizer, normalization, stemming, stopword, and field-weight settings.
- Include lexical artifact fingerprints and document counts in the manifest.
- Validate one-to-one ID coverage between dense and lexical indexes.
- Add CLI/UI inspection for exact-term searches and sidecar health.

Cross-repository integration:

- `PodCast Chat`: load the sidecar, issue lexical queries, fuse dense and lexical candidates, and expose channel provenance.
- `RAGScope`: import dense/lexical run traces, compare channels, and score hybrid retrieval.

Tests and exit criteria:

- Lexical artifacts are reproducible from identical inputs.
- Missing or corrupt sidecars degrade to dense-only behavior downstream.
- Dense and lexical candidates resolve to identical document metadata.
- Export size and build time are reported.

## Phase 7: Shared Judged-Query and Run Contracts

Scope: multi-repo.

Touched repositories:

- `RAGScope`: owns benchmark execution, metrics, comparison, and reporting.
- `PodCast Chat`: emits normalized retrieval/answer traces.
- `Chroma DB Import`: emits immutable corpus/index identity used by every run.
- `Podcast-RAG-pipeline` optionally provides source/evidence identity fixtures.

Deliverables:

- Agree on JSON schemas for corpus identity, retrieval candidates, scores, filters, evidence IDs, timing, and judged queries.
- Add shared contract fixtures to each affected repo.
- Export importer build reports in the normalized run format.
- Add contract version negotiation and actionable incompatibility messages.

Tests and exit criteria:

- A fixture produced by this repo loads in RAGScope without manual conversion.
- Run records identify the exact index and representation used.
- Contract tests fail when IDs, dimensions, or schema versions disagree.

## Phase 8: Dual-Index Compatibility and A/B Release Flow

Scope: multi-repo.

Touched repositories: `PodCast Chat`, `RAGScope`.

Deliverables:

- Add export aliases such as `current`, `candidate`, and immutable build IDs without relying on destructive directory replacement.
- Let Podcast Chat select compatible index versions and clearly label experimental indexes.
- Let RAGScope run the same judged queries against baseline and candidate builds.
- Generate a promotion report covering quality, latency, storage, failures, and regressions.
- Require an explicit promotion operation before changing the default alias.

Tests and exit criteria:

- Rollback changes only the alias, not index contents.
- Old Podcast Chat versions receive a clear compatibility failure rather than incorrect vectors.
- Promotion is blocked when required benchmark guardrails fail.

## Phase 9: Deterministic Provenance Graph

Effort: medium-hard. Scope: multi-repo.

Touched repositories:

- `Podcast-RAG-pipeline`: emits canonical parent/child, claim/evidence, topic/document, speaker/episode, and temporal edges.
- `Chroma DB Import`: validates, versions, and packages graph artifacts.
- `PodCast Chat`: performs bounded graph expansion.
- `RAGScope`: visualizes paths and evaluates graph-assisted retrieval.

Deliverables:

- Define stable node and edge schemas with provenance and confidence.
- Package a sidecar graph without replacing Chroma.
- Validate dangling edges, cycles where forbidden, and source evidence resolution.
- Keep LLM-inferred edges separate from deterministic edges.

Exit criteria:

- Every graph result resolves to existing evidence.
- Graph expansion can be disabled with no impact on dense/lexical retrieval.
- Multi-hop benchmark gains justify added latency and storage.

## Phase 10: True Late Chunking

Effort: hard. Scope: multi-repo.

Touched repositories: `Podcast-RAG-pipeline`, `PodCast Chat`, `RAGScope`.

Deliverables:

- Preserve source token/span boundaries from preprocessing through import.
- Implement long-context encoding and post-encoding chunk pooling.
- Handle episodes exceeding the model context with deterministic windows.
- Version pooling, overlap, and boundary rules.
- Rebuild candidate indexes and compare against contextual headers.

Decision gate: adopt only if late chunking materially outperforms deterministic headers on ambiguous-reference queries after accounting for build cost.

## Phase 11: Multi-Vector / Late-Interaction Indexing

Effort: hard. Scope: multi-repo.

Touched repositories: `PodCast Chat`, `RAGScope`.

Deliverables:

- Add a separate late-interaction index provider rather than forcing token vectors into the Chroma contract.
- Package model/index runtime metadata and hardware requirements.
- Implement candidate retrieval or reranking in Podcast Chat.
- Measure quality, storage, CPU/GPU latency, and installer complexity in RAGScope.

Decision gate: promote only if it outperforms dense + BM25 + cross-encoder reranking enough to justify the operational burden.

## Phase 12: GraphRAG/HippoRAG and Domain Adaptation

Effort: hard. Scope: multi-repo.

Touched repositories: `Podcast-RAG-pipeline`, `Chroma DB Import`, `PodCast Chat`, `RAGScope`, and `podcast-host-transcription-pipeline` if transcript/entity normalization must improve.

Deliverables:

- Evaluate LLM-derived entity/community graphs only after deterministic graph baselines.
- Add graph snapshot lineage and incremental rebuild behavior.
- Build a human-reviewed training/evaluation set with hard negatives.
- Fine-tune or distill a reranker before attempting embedding-model adaptation.
- Compare factual, associative, temporal, and global-query performance independently.

Decision gate: no production adoption without measurable gains, reproducible rebuilds, explainable evidence paths, and acceptable local resource use.

## Recommended First Release Boundary

The first implementation release should include Phases 1-4. It will provide versioned contracts, correct incremental reconciliation, contextual embeddings, and safe shadow indexes entirely within this repository. Phase 5 is optional based on resource needs. Phase 6 can then produce the first cross-repo frontier artifact without disrupting the current dense export.
