# Deduplication and Repetition-Control Design Proposal

**Status:** Revised design for implementation; no implementation performed.
**Assessment basis:** Current working-tree code, including uncommitted managed-context changes.
**Companion:** [Implementation plan](deduplication-implementation-plan.md).

## 1. Assessment and decisions

The original direction is sound: preserve evidence, suppress proven accidental copies, report approximate matches, and control retrieval repetition. It was not safe to implement literally. This revision resolves the following gaps.

| Gap | Decision |
|---|---|
| A matching span alone could suppress changed or conflicting text. | Require matching content, provenance, representation input, and compatible metadata together. |
| Near matches were both report-only and suppressible by default. | No approximate suppression in v1; Strict is deferred. |
| “Same source” could mean an entire episode with legitimately repeated phrases. | Require validated, nonempty source-span identity. |
| Normalization and canonical election were vague. | Specify deterministic rules below. |
| Suppression loses IDs, hierarchy links, and provenance. | Require a portable occurrence ledger and direct aliases. |
| Per-cache processing misses release-wide collisions and makes canonical choice order-dependent. | Plan the complete eligible release before embedding. |
| Embedding reuse and record identity were conflated. | Separate their keys, decisions, and counters. |
| No failure, migration, or consumer compatibility contract. | Rebuild immutable releases and introduce a fail-closed downstream v2 contract. |
| Retrieval appeared to be owned by this repository. | Supply a reference helper here; identify actual Chat integration separately. |

Verified local integration facts:

- `importer.py:import_cache` reconciles one cache at a time. `reconciliation.py:plan_reconciliation` already distinguishes added, changed, metadata-only, unchanged, and removed records. Preserve those distinctions.
- `importer.py:embedding_cache_path` hashes all metadata through `document_content_hash`. Dedup annotations/release stamps would invalidate reusable vectors. Use the existing representation-aware `embedding_fingerprint` instead.
- `managed.py:run_managed_import` saves a profile derived from supplied config instead of applying the saved context profile. Managed imports must honor saved settings.
- `managed.py:_write_managed_metadata` counts raw documents per cache/episode. Use final eligible/stored inventories instead.
- `cli.py:run_import` can stop early and still write a manifest. Managed promotion must verify complete planned coverage and execution status.
- `ManagedCatalog.save_profile` replaces the current profile. Add revision history and downstream-export records for multiple profiles of one upstream release.
- The current [Chat handoff](podcast-chat-handoff-contract.md) accepts downstream v1 and has no alias contract. An optional sidecar alone is insufficient compatibility protection.

These are findings about local code, not claims that deduplication already exists.

## 2. Scope and defaults

V1 covers managed immutable exports only. Legacy flat imports retain current behavior until explicitly adopted into a managed scope. Do not introduce suppression into mutable legacy imports.

| Profile | Exact copies | Approximate matches | Downstream contract |
|---|---|---|---|
| Off | Existing identity reconciliation | Disabled | Existing v1 |
| Safe | Suppress proven aliases | Report only | v2 |
| Audit | Retain all eligible IDs; report would-suppress aliases | Report only | v2 |

Existing contexts migrate to Off; new contexts default to Safe. UI must state that Safe/Audit exports require a dedup-aware Chat consumer. Saving settings marks an import as needed; it does not build or activate a release. Importing with changed effective settings creates a new release. Display-only UI preferences do not affect release identity.

Strict, automatic near suppression, vector similarity, and general semantic/MMR diversification are deferred. V1 near detection is deterministic lexical similarity, not a claim of reliable paraphrase detection. Cross-episode evidence is always retained. Cross-partition/corpus comparison is prohibited in every mode, including candidate indexes and caches.

## 3. Validation and planning order

1. Resolve the complete upstream release; verify cache hashes and partition/corpus mapping with existing managed validation.
2. Validate raw items before the document loader can skip malformed rows. Reject missing effective IDs and repeated IDs across all caches, even if identical or later excluded. Effective ID remains `stable_document_id`, falling back to `node_id`.
3. Verify each record's exact partition/corpus and `episode_uid == partition_id + ':' + episode_id`; episode must belong to the release. Fill missing scope only from validated enclosing producer metadata. Conflicts are errors, never overwritten.
4. Apply existing text/speaker eligibility rules; count excluded inputs and reasons. Never select a filtered-out canonical.
5. Prepare representation inputs with existing `embedding_text` semantics and plan exact groups over the complete eligible snapshot, including changed records. Previously seen IDs do not bypass deduplication.
6. Build the near report, portable occurrence inventory, expected stored IDs, and plan fingerprint. Dry-run stops here without constructing a provider or writing vectors, embedding cache, profiles, release status, or active pointers.
7. Real imports use a fresh staging export and only planned stored IDs. Reuse verified vectors, write all artifacts, validate, publish the complete directory, then atomically activate.

Validation-only stops after scope/input validation. Preview can read historical data and return stdout/UI output; writing a report requires an explicit output request. Catalog setup/migration is a separate initialization action, not a hidden preview mutation.

## 4. Exact algorithm

### 4.1 Normalization

`dedup_key_version = dedup-key-v1`. Apply Unicode NFC, replace each Unicode whitespace run with one ASCII space, and strip outer whitespace. Preserve case, punctuation, diacritics, numbers, negation, speaker labels, and markup. No NFKC, case folding, punctuation stripping, or heuristic formatting removal. Empty normalized content is excluded by existing text eligibility rules.

Text hash: SHA-256 of normalized UTF-8 bytes, with `sha256:` prefix. Structured hash: SHA-256 of UTF-8 JSON with sorted object keys, compact separators, `ensure_ascii=False`, `allow_nan=False`. Define array order explicitly. Never use Python `hash()`. Compare underlying equality fields after a hash match; hashes alone do not authorize suppression.

### 4.2 Provenance

V1 uses the verified cache content fingerprint plus episode UID as its source revision. This conservative choice prevents suppression across distinct cache revisions. Do not reinterpret existing representation-dependent `source_identity()` as span identity.

Accept a nonempty string `source_span_id`, or a nonempty list of nonempty strings `source_span_ids`; defensively parse a JSON-encoded list when present. Use a sorted unique set of IDs. If both fields exist their sets must agree. Missing, empty, malformed, or conflicting spans mean retain, with a reason. Numeric span IDs need a future explicit adapter; do not coerce them. Do not infer spans from timestamps, filenames, text hashes, parent IDs, or overlap.

`source_span_hash` hashes `[partition_id, corpus_id, episode_uid, verified_cache_fingerprint, sorted_span_ids]`.

### 4.3 Suppression predicate

Different effective IDs share an exact group only when ALL these values agree:

- partition, corpus, episode UID, verified cache fingerprint;
- nonempty node type and complete valid span set;
- normalized content AND exact `embedding_text` string;
- original producer metadata after removing ONLY `stable_document_id` and `node_id`.

Compare producer metadata before importer enrichment/sanitization. Speaker, timestamps, hierarchy references, review flags, permissions, and every other metadata difference prevent suppression. This deliberately avoids an invented metadata precedence rule. Reserve `dedup_` and all new duplicate/hash output field names below; reject producer collisions in dedup-enabled mode.

Retain equal-text candidates that fail the predicate with reasons such as `missing_span`, `different_source_revision`, `different_embedding_input`, or `metadata_conflict`. Retain same-span/different-text records and report a span conflict. A changed stable ID follows reconciliation; competing different IDs are not silently resolved as corrections.

Exact embedding input equality makes physical suppression stricter than normalized equality. The normalized hash still supports reporting. Widening suppression requires a new key/policy version.

### 4.4 Canonical choice

Choose the smallest effective ID using Python string ordering within each exact group of size >=2. Define `duplicate_group_id = 'dupgrp_' + full_sha256(structured_exact_key)`, where the key includes the version and every equality field above, but no input order, canonical ID, profile, or timestamp. Members are sorted by ID.

Safe stores the canonical and makes every other member a direct alias to it. Audit stores all members with the same preferred canonical but no suppression alias. Reject chains, cycles, foreign/missing targets, and inconsistent membership. Recompute every snapshot: deletion of a canonical promotes the smallest surviving eligible member. A singleton has no duplicate group.

Do not rewrite upstream IDs/hierarchy links. Consumers resolve links through release-local aliases while retaining original occurrence IDs for provenance. Restrictive metadata equality keeps aliases compatible with filters.

## 5. Near detection: bounded report only

`near_algorithm_version = lexical-near-v1`. Compare exact representatives (one per exact group, including Audit) plus unique records. Block by `(partition_id, corpus_id, node_type)`; never compare summary/source node types. Cross-episode pairs within a block are allowed and always retained.

Freeze `lexical_index.tokenize` behavior for this algorithm version. Form sets of consecutive three-token tuples. Skip records with fewer than 20 tokens, reporting counts. For blocks of at most 2,000 eligible representatives, enumerate unique pairs in sorted ID order. Emit a direct edge when both token length ratio `min/max >= 0.90` and shingle Jaccard `intersection/union >= 0.90`. Thresholds are inclusive, finite numbers in [0,1], excluding booleans. Configurable positive integer block limit cannot exceed 2,000 in v1.

Skip an oversized block entirely and label detection incomplete; never silently sample or run an unbounded all-corpus comparison. No vector/model calls. Edges contain scores and same/cross-episode classification. Do not form transitive groups: A≈B and B≈C does not imply A≈C. Near edges never change storage or default retrieval collapse. Thresholds are starting engineering defaults, not measured accuracy claims.

## 6. Artifacts and compatibility

Safe/Audit use `release_contract_version: chroma-export-release-v2` and `required_capabilities: [dedup-aliases-v1]`. Older consumers accepting only v1 reject v2. Off remains v1. Keep upstream versions and import-manifest version 2.0 unchanged; extend the latter additively. During implementation add an explicit v2 extension to the Chat handoff; do not silently redefine v1 compatibility.

Required files for v2:

| File | Contents |
|---|---|
| `dedup_manifest.json` | `contract_version: chroma-export-dedup-v1`; release/upstream/partition/corpus/representation IDs; full effective policy/fingerprint; plan fingerprint; counts; exact groups; near edges; coverage; ledger SHA-256 and row count. |
| `dedup_occurrences.jsonl` | One portable UTF-8 JSON object per eligible original ID, sorted by ID, retained and suppressed. |
| `release.json` | Existing fields plus required capabilities and `dedup` reference: manifest relative path, file SHA-256, policy and plan fingerprints. |

`import_manifest.json` repeats the dedup reference/counts. Hash final ledger bytes first, then manifest bytes, then write references; avoid cyclic hashes. Artifact paths must resolve inside the export, rejecting traversal/symlink escapes. Validate versions, scope, finite scores, hashes, unique IDs, alias/group integrity, and Chroma inventory before use/reuse.

Each ledger row contains:

```text
document_id, node_id, partition_id, corpus_id, episode_uid,
verified_cache_fingerprint, page_content, producer_metadata,
normalized_text_hash, source_span_hash (null if unavailable),
duplicate_group_id (null if none), preferred_canonical_id,
storage_status (retained | suppressed_exact), alias_target_id (null unless suppressed),
decision_method, decision_reason
```

Preserve original text and portable provenance for every occurrence. Use existing portability rules to omit/redact machine-local paths and secrets, retaining logical names/hashes. Comparisons use original in-memory metadata; exported decisions use the portable projection. The ledger enables review/reconstruction without private SQLite; do not invent omitted fields or call a reconstruction a producer-signed release. It preserves evidence but does not promise proportional disk savings because original text is retained outside the vector index.

Exact groups contain ID, canonical ID, sorted member IDs, and `method: exact_provenance_v1`. Near edges contain left/right IDs, `method: lexical_shingle_jaccard_v1`, similarity, length ratio, and scope. Do not reuse exact group IDs for near edges.

Chroma scalar output fields: `dedup_key_version`, `normalized_text_hash`, optional `source_span_hash`, optional `duplicate_group_id`, `duplicate_status` (unique/canonical/retained_exact), `duplicate_method` (none/exact_provenance_v1), `duplicate_of` (empty except Audit noncanonical members), and `dedup_policy_version`. Omit unavailable values rather than writing null/nested Chroma metadata. Multiple near scores live in the report.

Plan fingerprint hashes sorted portable occurrence decisions, groups, edges, coverage, and effective policy. Exclude timestamps, runtime embedding counters, local paths, and downstream release ID. Dry-run/import with the same inputs/settings must agree. Release identity uses upstream identity and full effective import profile including dedup settings/versions. Verify full fingerprints on reuse; truncated-name collision is an error, never overwrite. Dedup-only changes do not change representation ID.

## 7. Embeddings and reconciliation

Cache key is existing `embedding_fingerprint(page_content, producer_metadata, resolved_spec)` under the partition's private cache, not normalized text or all metadata. Add a cache format version; validate fingerprint, representation, dimension, finite numeric values, and provider vector count. Old/invalid entries are misses; leave them intact. Coalesce identical pending keys within a batch even with disk cache disabled, then fan vectors out. Different headers/representations must miss.

Separate producer metadata fingerprints from importer-generated annotations. Do not hash output fingerprints into themselves. Metadata-only/dedup annotation changes require no embedding; actual embedding input changes require a matching cache entry or computation.

Managed exports are fresh complete snapshots, not incremental mutations of the active export. Suppressed IDs are not source removals and must not trigger legacy deletion confirmation. Preserve existing legacy reconcile/allow-delete-missing behavior. Never carry stale aliases or near edges forward.

If a managed export enables lexical/advanced sidecars, build searchable rows from the same stored-ID inventory. Every searchable ID must exist in Chroma; hierarchy targets must resolve via stored IDs/aliases or be explicitly unavailable source references. Do not add an unrelated sidecar pipeline where none is currently enabled.

## 8. Configuration and lifecycle

Effective nested dedup policy keys:

```text
profile: off | safe | audit
policy_version: dedup-v1
key_version: dedup-key-v1
near_algorithm_version: lexical-near-v1
near_enabled: true
near_jaccard_threshold: 0.90
near_length_ratio: 0.90
near_min_tokens: 20
near_max_block_records: 2000
retrieval: {enabled: true, oversample_factor: 3, candidate_cap: 200}
```

Safe/Audit defaults are above. Off disables near/retrieval. Version/min-token fields are implementation controlled; reject unknown/Strict values. Retrieval factor/cap are fixed v1 constants, enablement is user editable. All policy fields participate in the fingerprint.

Add idempotent transactional catalog migration with append-only `import_profile_revisions(partition_id, profile_fingerprint, profile_json, created_at)` keyed by partition/fingerprint, and `downstream_exports(partition_id, downstream_release_id, upstream_release_id, profile_fingerprint, status, detail_json, created_at)` keyed by partition/downstream ID. Preserve discovery rows/history. Pointer remains reader authority and can repair catalog status after a crash.

Load saved effective context profile before planning; base config only fills missing legacy fields. Explicit UI/CLI edits save revisions. Starting import must not overwrite saved settings with global defaults. Include all behavior-changing representation fields in import fingerprints, including output dimension and Matryoshka compatibility omitted by the current managed payload.

UI exposes Off/Safe/Audit, report toggle/thresholds, retrieval toggle, Preview, and read-only Review groups with reasons/provenance/counts/coverage. No Strict, canonical editing, or manual group assignments. Use workers for expensive operations and thread-local SQLite connections.

Freeze run config and serialize imports/promotion per partition using an OS-backed process-lifetime file lock (on Windows use a real file lock, not a stale sentinel). Other partitions may proceed. Competing same-partition import reports busy. Recheck source hashes before importing each cache and before promotion; changed inputs abort. A mid-run profile edit affects the next run, not the current snapshot.

Before promotion validate full planned coverage, actual Chroma IDs/counts/metadata, every ledger/group/hash, enabled sidecars, and read-only retrieval smoke (skip query only for valid empty collections). Cancellation, early stop, or incomplete coverage leaves the previous pointer unchanged. Publish complete directory, then replace pointer atomically. Never overwrite history. A crash after publication before activation can reuse the validated export; catalog failure after pointer success is repaired from the pointer.

## 9. Retrieval and repository boundary

Implement a provider-independent reference helper here. PodCast Chat integration is a separate deliverable; local helper tests do not mean the live app was changed.

1. Validate v2 artifacts and bind a single partition/corpus/release/representation. Reject any foreign returned hit before filtering/collapse.
2. Apply episode/speaker/node/access filters before representative selection. Resolve hierarchy aliases while retaining original IDs for citations.
3. Accept positive integer `top_k <= 200`; request `min(collection_count, 200, 3*top_k)` candidates by default. Preserve established store ranking, not an assumed cosine conversion.
4. In rank order keep first hit per validated exact group; ungrouped hits remain independent. Break equal supplied ranks by ID. Never collapse on a bare normalized hash, a missing group value, or a near edge. Audit may select a relevant eligible noncanonical member.
5. Return up to top_k; bounded underfill is allowed/reported. No unlimited refills or relaxed filters.
6. Attach only filter-eligible related provenance from the ledger. Separate passage, occurrence, and distinct episode counts. Repeated cross-episode text remains independently attributable and is not collapsed by default.

When retrieval collapse is disabled, return the first eligible top_k hits without grouping and request only `min(collection_count, top_k)` candidates. Artifact/scope validation and alias resolution remain mandatory for v2; disabling repetition control does not disable provenance validation.

General topic/episode diversification is future work. Exported retrieval settings are instructions for compatible consumers, not a change to another application's behavior.

## 10. Metrics and migration

Snapshot counts: input, excluded, eligible, stored, suppressed_exact, would_suppress_exact, exact_group_count. Near counts: candidate pairs, edges, unique flagged records, skipped-short records, skipped-block records, status (disabled/complete/incomplete). Reconciliation new/changed/metadata-only/unchanged/removed is a separate operation classification, not a dedup total.

Actual embedding counters: provider document inputs, cache-hit records, shared-vector records; compatibility probes separately. Dry-run marks embedding work not executed rather than claiming measured savings. Reused releases retain original snapshot totals but the reuse operation reports zero embedding work.

Required equations: `input = excluded + eligible`; `eligible = stored + suppressed_exact`; Audit suppression is zero; `would_suppress_exact = sum(group_size-1)`; episode stored counts sum to actual collection count. Near counts overlap and cannot be added to suppression totals.

Migration is rebuild-only from verified upstream inputs. Never backfill historical Chroma metadata. If upstream inputs are missing, retain the old export and report faithful rebuild unavailable. Do not invent provenance. Rollback validates and selects an existing complete export without changing it.

Acceptance covers deterministic planning, exact/provenance conflicts, canonical removal/change, global identity rejection, cross-episode retention, bounded nontransitive near reports, correct embedding reuse, portable provenance/aliases, filtered retrieval, counts, profile persistence/migration, corruption/cancellation/concurrency recovery, and byte-for-byte preservation of producer files/history. See the implementation plan for concrete fixtures and checkpoints.
