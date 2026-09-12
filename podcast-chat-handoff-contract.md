# PodCast Chat Database Handoff Contract

**Status:** normative v1 contract
**Contract ID:** `podcast-chat-database-handoff-v1`
**Producer:** Chroma DB Import managed-context exporter
**Consumer:** PodCast Chat
**Upstream authority:** [`C:/temp/codex/Podcast-RAG-pipeline/transcription-handoff-contract.md`](C:/temp/codex/Podcast-RAG-pipeline/transcription-handoff-contract.md)

This document defines the stable handoff from a managed Chroma export to PodCast
Chat. It is intentionally separate from the upstream transcription handoff and
release contracts. The upstream producer owns transcript truth and upstream
release identity. Chroma DB Import owns the derived vector export. PodCast Chat
reads the validated export and uses it for retrieval and provenance; it does not
reconstruct identity from folder names or raw cache files.

The contract is filesystem-based in v1. An API, object-store, or packaged
transport may be added later without changing the JSON meanings or the
identity rules.

## 1. Non-negotiable rules

1. A Chat session is bound to exactly one `(partition_id, corpus_id)` pair.
2. `partition_id` and `corpus_id` come from the validated producer handoff and
   release. They are not inferred from a podcast name, folder, filename, or
   local alias.
3. `episode_uid` is globally scoped and must be
   `partition_id + ":" + episode_id`.
4. `database_id` is the downstream database identity and must equal
   `corpus_id`.
5. Normal v1 retrieval is single-partition. A search across two partitions is
   not a normal Chat operation and must use a future, explicit aggregation
   contract.
6. PodCast Chat must use the active-release pointer, or an explicitly selected
   historical release for an administrative/reproducibility workflow. It must
   not silently choose an arbitrary directory.
7. A release is usable only when all required metadata and identity checks pass.
   On a mismatch, Chat must disable retrieval for that export and show the
   validation problem.
8. A changed embedding or representation configuration is a different vector
   space. Chat must not query it with a different representation.
9. Portable artifacts contain no secrets or machine-specific absolute paths.
   The importer-local catalog may contain local paths, but Chat must not depend
   on that catalog.
10. Producer files and upstream release files are read-only to PodCast Chat.
11. The sole supported vector representation is Qwen3-Embedding-4B. This is
    an identity migration within the existing v1 handoff contract; it does not
    require a contract-version bump. Older BGE releases must fail closed.

## 2. Terminology

| Term | Meaning |
|---|---|
| `partition_id` | Immutable producer-defined processing-space identity. It separates podcasts, meeting streams, confidentiality boundaries, or custom contexts. |
| `corpus_id` | Immutable downstream corpus/database identity. It normally equals `partition_id`; a different value is valid only when the producer registry explicitly maps it. |
| `episode_id` | Stable episode or meeting-recording identifier within a partition. |
| `episode_uid` | Globally safe episode identity: `partition_id:episode_id`. |
| Upstream release | A Podcast-RAG release with contract `podcast-rag-corpus-release-v1`. |
| Downstream release | A Chroma export with contract `chroma-export-release-v1`. It represents one upstream release imported with one effective importer profile. |
| Import profile | Importer choices such as embedding model, model revision, normalization, distance metric, speaker selection, and contextualization. |
| Representation | The complete vector/index space, including model, revision, dimension, pooling, contextualization, metric, and implementation version. |
| Active release | The complete downstream export named by `active-release.json`. |
| Historical release | A complete immutable export retained for reproducibility or rollback but not used by normal Chat. |
| Local catalog | Private importer SQLite state, normally `state/context_catalog.sqlite3`. It is not a PodCast Chat input. |

`partition_config_fingerprint` identifies producer/partition configuration.
`import_profile_fingerprint` identifies importer choices. Neither may be
confused with `representation_id`, which identifies the complete vector space.

## 3. Input boundary and package layout

PodCast Chat receives the managed output root selected by the user or by a
deployment configuration. It does not need the Podcast-RAG source root,
processed-cache path, importer state directory, or importer catalog.

The normative managed layout is:

```text
<managed-output-root>/
  partitions/
    <partition_id>/
      active-release.json
      releases/
        <downstream-release-id>/
          export/
            chroma.sqlite3
            podcast.json
            import_manifest.json
            release.json
      staging/
        <operation-id>/                 # incomplete work; never a Chat target
```

The release directory is keyed by the downstream `release.json.release_id`.
The upstream release identity is retained inside `release.json` and must never
be inferred from the directory name.

`staging/` is not a valid Chat input. Chat must ignore it and any temporary
directory whose name ends in `.tmp`.

### 3.1 Explicit export-root input

For a portable copy or a deployment that receives one database at a time, Chat
may be given the path to an individual `export/` directory. It must still
validate `release.json`, `podcast.json`, `import_manifest.json`, and the Chroma
collection before enabling retrieval.

### 3.2 Active-release pointer

`active-release.json` is a small atomic pointer. Its minimum schema is:

```json
{
  "partition_id": "partition_podcast",
  "corpus_id": "partition_podcast",
  "release_id": "chroma_release_01_baseline",
  "updated_at": "2026-09-05T18:30:00+00:00"
}
```

Required fields:

| Field | Type | Rule |
|---|---|---|
| `partition_id` | string | Safe identifier; must equal the partition directory and every downstream artifact. |
| `corpus_id` | string | Safe identifier; must equal every downstream artifact and `podcast.json.database_id`. |
| `release_id` | string | Safe downstream release identifier; must resolve below this partition’s `releases/` directory. |
| `updated_at` | UTC timestamp | Informational pointer update time. |

If the pointer is missing, malformed, outside the managed root, or refers to an
incomplete export, Chat must report “no valid active database” rather than
selecting the newest directory.

## 4. Identity contract

Every managed export must carry the following identity fields in
`release.json`, `podcast.json`, `import_manifest.json`, and document metadata
where the field applies:

```json
{
  "partition_id": "partition_podcast",
  "corpus_id": "partition_podcast",
  "partition_display_name": "Podcast",
  "context_type": "podcast",
  "workflow_profile": "podcast",
  "partition_config_fingerprint": "sha256:...",
  "upstream_release_id": "release_partition_podcast_20260905_01",
  "handoff_ids": ["handoff_20260905T180000Z_01"],
  "episode_uid": "partition_podcast:podcast-2026-01-03"
}
```

`episode_uid` is an episode/document field and is not required at the
collection level. `upstream_release_id` and `handoff_ids` are release and
provenance fields; they must be retained in artifacts even when a particular
document has no direct handoff-level value.

### 4.1 Identity invariants

PodCast Chat must reject the export if any of the following is true:

- the active pointer and export disagree on `partition_id` or `corpus_id`;
- `podcast.json.database_id` is not exactly `corpus_id`;
- `release.json.release_id` is not the pointer’s `release_id`;
- a downstream release has a different `upstream_release_id` in different
  artifacts;
- `partition_id != corpus_id` without producer registry evidence in the
  upstream source package;
- a release contains duplicate `episode_uids`;
- an `episode_uid` does not start with `partition_id + ":"`;
- document metadata contains conflicting partition/corpus identities;
- the Chroma collection identity conflicts with the JSON artifacts;
- the export is a legacy flat-folder import without an explicit adoption and
  managed-export operation.

Display names, descriptions, tags, and local aliases are not identity. A
display-name change must not change the database location, `database_id`, or
any immutable ID.

## 5. Downstream release manifest

`release.json` is the authoritative release-level handoff for PodCast Chat.
Its v1 contract is:

```json
{
  "release_contract_version": "chroma-export-release-v1",
  "release_id": "chroma_release_01_baseline",
  "upstream_release_contract_version": "podcast-rag-corpus-release-v1",
  "upstream_release_id": "release_partition_podcast_20260905_01",
  "partition": {
    "partition_id": "partition_podcast",
    "corpus_id": "partition_podcast",
    "partition_display_name": "Podcast",
    "context_type": "podcast",
    "workflow_profile": "podcast",
    "partition_config_fingerprint": "sha256:..."
  },
  "partition_id": "partition_podcast",
  "corpus_id": "partition_podcast",
  "partition_display_name": "Podcast",
  "context_type": "podcast",
  "workflow_profile": "podcast",
  "partition_config_fingerprint": "sha256:...",
  "handoff_ids": ["handoff_20260905T180000Z_01"],
  "episode_uids": ["partition_podcast:podcast-2026-01-03"],
  "processed_cache_fingerprints": ["sha256:..."],
  "representation_id": "<stable-representation-fingerprint>",
  "embedding_model": "Qwen/Qwen3-Embedding-4B",
  "embedding_model_revision": "5cf2132abc99cad020ac570b19d031efec650f2b",
  "embedding_dimension": 2560,
  "representation_profile": "qwen3-embedding-4b-shadow",
  "provider": "sentence_transformers",
  "normalize_embeddings": true,
  "distance_metric": "cosine",
  "query_instruction_profile": "podcast-retrieval-v1",
  "query_document_mode": "separate-query-instruction",
  "import_profile_fingerprint": "sha256:...",
  "created_at": "2026-09-05T18:30:00+00:00"
}
```

### 5.1 Required release fields

| Field | Type | Consumer behavior |
|---|---|---|
| `release_contract_version` | string | Must equal `chroma-export-release-v1`. |
| `release_id` | string | Immutable downstream export identity. |
| `upstream_release_contract_version` | string | Must equal `podcast-rag-corpus-release-v1`. |
| `upstream_release_id` | string | Stable source release identity. |
| `partition` | object | Complete partition identity. |
| `partition_id` | string | Must agree with `partition.partition_id`. |
| `corpus_id` | string | Must agree with `partition.corpus_id`. |
| `handoff_ids` | array of strings | Source handoff lineage. |
| `episode_uids` | array of unique strings | Complete episode coverage for this release. |
| `processed_cache_fingerprints` | array of strings | Source cache lineage. |
| `representation_id` | string | Complete vector-space identity. |
| `embedding_model` | string | Model used to create document vectors. |
| `embedding_dimension` | positive integer | Must agree with stored vectors and import metadata. |
| `import_profile_fingerprint` | string | Importer profile identity. |
| `created_at` | UTC timestamp | Release creation time. |

Unknown additive fields may be preserved and ignored by a v1 consumer. Missing
required fields, wrong contract versions, duplicate IDs, or conflicting values
are hard validation failures.

## 6. Podcast metadata handoff

`podcast.json` is the presentation and catalog metadata surface. It is not a
replacement for `release.json`, but it supplies the display information Chat
needs without scanning Chroma.

Minimum schema:

```json
{
  "podcast_name": "Podcast",
  "database_id": "partition_podcast",
  "collection_name": "rag_documents__qwen3-embedding-4b-shadow",
  "embedding_model": "Qwen/Qwen3-Embedding-4B",
  "embedding_model_revision": "5cf2132abc99cad020ac570b19d031efec650f2b",
  "embedding_dimension": 2560,
  "representation_id": "<stable-representation-fingerprint>",
  "description": "Managed podcast context: Podcast",
  "episode_count": 1,
  "document_count": 248,
  "speakers": [
    {"id": "host", "name": "Host"}
  ],
  "episodes": [
    {
      "source_file": "episode-01.processed_documents.json",
      "source_fingerprint": "sha256:...",
      "episode_uid": "partition_podcast:podcast-2026-01-03",
      "episode_id": "podcast-2026-01-03",
      "episode_title": "Podcast 20260103",
      "episode_date": "2026-01-03",
      "document_count": 248,
      "speakers": [
        {"id": "host", "name": "Host"}
      ],
      "partition_id": "partition_podcast",
      "corpus_id": "partition_podcast",
      "partition_display_name": "Podcast",
      "context_type": "podcast",
      "workflow_profile": "podcast",
      "partition_config_fingerprint": "sha256:...",
      "upstream_release_id": "release_partition_podcast_20260905_01",
      "imported_at": "2026-09-05T18:30:00+00:00"
    }
  ],
  "partition_id": "partition_podcast",
  "corpus_id": "partition_podcast",
  "partition_display_name": "Podcast",
  "context_type": "podcast",
  "workflow_profile": "podcast",
  "partition_config_fingerprint": "sha256:...",
  "upstream_release_id": "release_partition_podcast_20260905_01",
  "import_profile_fingerprint": "sha256:...",
  "generated_at": "2026-09-05T18:30:00+00:00"
}
```

Required top-level fields are `database_id`, `collection_name`,
`embedding_model`, `embedding_dimension`, `representation_id`, `episodes`, and
`speakers`. A managed export must also have
the complete partition identity and `upstream_release_id`. `database_id` must
equal `corpus_id`; the Qwen3 collection name is derived as
`<base>__qwen3-embedding-4b-shadow` and must be used consistently.

`source_file` is a portable basename or logical source name. It is not a local
filesystem path and must not be used to locate the source transcript.

## 7. Import manifest and representation handoff

`import_manifest.json` records how the Chroma export was produced. PodCast Chat
must use it to decide how to embed a query and whether it can safely query the
database.

The importer emits `manifest_version: "2.0"` with this required minimum:

```json
{
  "manifest_version": "2.0",
  "importer_version": "0.3.0",
  "imported_at": "2026-09-05T18:30:00+00:00",
  "config": {
    "embedding_model": "Qwen/Qwen3-Embedding-4B",
    "embedding_model_revision": "5cf2132abc99cad020ac570b19d031efec650f2b",
    "embedding_provider": "sentence_transformers",
    "normalize_embeddings": true,
    "distance_metric": "cosine",
    "contextualization": "minimal",
    "collection_name": "rag_documents__qwen3-embedding-4b-shadow",
    "portable_artifacts": true
  },
  "collection_name": "rag_documents__qwen3-embedding-4b-shadow",
  "embedding_model": "Qwen/Qwen3-Embedding-4B",
  "embedding_model_revision": "5cf2132abc99cad020ac570b19d031efec650f2b",
  "embedding_dimension": 2560,
  "selected_speakers": [],
  "representation": {
    "provider": "sentence_transformers",
    "model_id": "Qwen/Qwen3-Embedding-4B",
    "model_revision": "5cf2132abc99cad020ac570b19d031efec650f2b",
    "dimension": 2560,
    "normalize_embeddings": true,
    "distance_metric": "cosine",
    "contextualization": "minimal",
    "context_header_version": "1.0",
    "pooling": "mean",
    "query_instruction_profile": "podcast-retrieval-v1",
    "query_document_mode": "separate-query-instruction",
    "index_schema_version": "1.0",
    "implementation_version": "stage-04-v2",
    "representation_id": "<stable-representation-fingerprint>"
  },
  "representation_id": "<stable-representation-fingerprint>",
  "partition": {
    "partition_id": "partition_podcast",
    "corpus_id": "partition_podcast",
    "partition_display_name": "Podcast",
    "context_type": "podcast",
    "workflow_profile": "podcast",
    "partition_config_fingerprint": "sha256:..."
  },
    "partition_id": "partition_podcast",
    "corpus_id": "partition_podcast",
    "upstream_release_id": "release_partition_podcast_20260905_01",
  "handoff_ids": ["handoff_20260905T180000Z_01"],
  "portable": true,
  "validation": {"valid": true},
  "source_files": [],
  "document_counts": {"document_count": 248}
}
```

The `representation` object is authoritative when present. The top-level
`representation_id`, `embedding_model`, `embedding_dimension`,
`collection_name`, `partition_id`, and `corpus_id` must agree with it and with
`release.json`/`podcast.json`.

### 7.1 Query embedding compatibility

PodCast Chat must embed user queries with the same effective representation:

- provider `sentence_transformers` and model ID `Qwen/Qwen3-Embedding-4B`;
- model revision `5cf2132abc99cad020ac570b19d031efec650f2b`;
- same pooling and query/document mode;
- same normalization setting;
- same distance metric;
- same contextualization profile and context-header version;
- same output dimension;
- same representation implementation/index schema version.

For Qwen3, Chat must format the query exactly as:

```text
Instruct: Retrieve podcast passages that best answer the user’s question, preserving speaker, episode, and viewpoint relevance.
Query: <user question>
```

This instruction is used for queries only. Documents retain the importer’s
contextualized document text and never receive the query instruction.

If the exact model revision is unavailable, Chat must report the database as
temporarily unavailable or use a precomputed compatible query-embedding
service. It must not silently substitute another model or representation.

## 8. Chroma database and collection contract

Each managed partition/release has a physically separate Chroma persistence
directory. The default collection is:

```text
rag_documents__qwen3-embedding-4b-shadow
```

The `collection_name` in `podcast.json`, `import_manifest.json`, and the
configuration used to open Chroma must agree. Chat must not connect to a
collection in another partition’s directory merely because the collection name
matches.

### 8.1 Document record shape

Each Chroma record has:

- Chroma ID: `stable_document_id` when present, otherwise `node_id`;
- `document`: the searchable text (`page_content` in the importer source);
- metadata containing the record and provenance fields below.

Minimum metadata for a managed record:

```json
{
  "node_id": "episode-01-leaf-0001",
  "node_type": "leaf_chunk",
  "source": "episode.json",
  "source_type": "json_transcript",
  "episode_id": "podcast-2026-01-03",
  "episode_uid": "partition_podcast:podcast-2026-01-03",
  "episode_title": "Podcast 20260103",
  "speaker_scope": "single",
  "speaker": "Host",
  "partition_id": "partition_podcast",
  "corpus_id": "partition_podcast",
  "partition_display_name": "Podcast",
  "context_type": "podcast",
  "workflow_profile": "podcast",
  "partition_config_fingerprint": "sha256:...",
  "upstream_release_id": "release_partition_podcast_20260905_01",
  "handoff_ids": "[\"handoff_20260905T180000Z_01\"]",
  "representation_id": "<stable-representation-fingerprint>",
  "embedding_fingerprint": "<document-embedding-fingerprint>",
  "metadata_fingerprint": "<metadata-fingerprint>",
  "content_hash": "<embedding-content-hash>",
  "import_source_cache": "episode-01.processed_documents.json",
  "source_content_fingerprint": "<sha256-or-content-fingerprint>",
  "source_identity": "<episode-source-identity>",
  "schema_version": "2.1"
}
```

Metadata values may be strings, numbers, or booleans. Complex values such as
`handoff_ids` and `speakers` may be JSON-encoded strings; Chat must parse them
defensively. Chat must preserve the original values when displaying
provenance, but must not treat a display label as an identity key.

Useful optional metadata includes `source_span_id`, `source_span_ids`,
`start`, `end`, `speaker`, `speakers`, `topic`, `hierarchy_path`, `child_ids`,
`parent_id`, `episode_date`, and producer review/correction fields. These are
used for citations only when present.

### 8.2 Collection-level metadata

When available, collection metadata should expose the same scope and release
identity:

```json
{
  "partition_id": "partition_podcast",
  "corpus_id": "partition_podcast",
  "partition_display_name": "Podcast",
  "context_type": "podcast",
  "workflow_profile": "podcast",
  "partition_config_fingerprint": "sha256:...",
  "upstream_release_id": "release_partition_podcast_20260905_01",
  "chroma_release_id": "chroma_release_01_baseline",
  "import_profile_fingerprint": "sha256:..."
}
```

JSON release artifacts remain authoritative if a Chroma client cannot expose
collection metadata.

## 9. Chat startup and release validation

Before showing a database as available, PodCast Chat should perform these
checks in order:

1. Resolve the configured managed output root or explicit export root.
2. Read and parse `active-release.json` when using managed-root mode.
3. Validate safe IDs and ensure every resolved path remains under the configured
   root. Reject traversal, absolute release IDs, and symlink escapes.
4. Read `release.json`, `podcast.json`, and `import_manifest.json`.
5. Validate the downstream and upstream contract versions.
6. Compare the active pointer, release, podcast metadata, and import manifest
   for exact partition/corpus/release identity agreement.
7. Validate `episode_uids` are unique and correctly scoped.
8. Validate the representation and embedding dimension agreement.
9. Open `chroma.sqlite3` and get the declared collection.
10. Check that the collection exists and is non-empty when the metadata says the
    release contains documents. An empty valid corpus may be displayed but
    should return “no indexed evidence.”
11. Run a small read-only collection smoke check, such as fetching one known ID
    or inspecting count/metadata. Do not write during startup validation.
12. Bind the Chat session to the validated identity tuple:
    `(partition_id, corpus_id, release_id, representation_id)`.

If any check fails, the UI should display a quarantine/unavailable state with a
human-readable reason and retain the previous active session if one exists.

## 10. Query contract

### 10.1 Query request

The internal Chat retrieval request should carry the bound identity explicitly:

```json
{
  "query": "What did the guest say about the proposed launch date?",
  "partition_id": "partition_podcast",
  "corpus_id": "partition_podcast",
  "release_id": "chroma_release_01_baseline",
  "representation_id": "<stable-representation-fingerprint>",
  "top_k": 8,
  "filters": {
    "episode_uids": [],
    "speakers": [],
    "episode_ids": [],
    "node_types": []
  }
}
```

The user-visible query may omit these fields, but the Chat runtime must add
them from the active validated session. A request for a different partition or
corpus must cause a context switch, not a mixed query.

### 10.2 Chroma query behavior

For normal v1 retrieval:

- open only the active release’s `chroma.sqlite3`;
- use the declared collection name;
- embed the query with the declared representation;
- request `top_k` results, bounded by a product-level maximum;
- apply filters only to metadata fields that are present and validated;
- always retain returned IDs, distances, documents, and metadata for provenance;
- optionally add a redundant `where` filter for `partition_id` and `corpus_id`
  even though the database is physically isolated;
- never search sibling partition directories in the same request.

Recommended redundant filter:

```json
{
  "$and": [
    {"partition_id": "partition_podcast"},
    {"corpus_id": "partition_podcast"}
  ]
}
```

If the collection contains records with conflicting scope metadata, Chat must
fail closed rather than filtering them out silently.

### 10.3 Speaker and episode filters

`selected_speakers` in `import_manifest.json` describes the importer profile;
it is not necessarily the user’s current query filter. Query filters should be
applied using record metadata:

- `speaker` for a single-speaker record;
- `speakers` for a multi-speaker record, parsing a JSON string when necessary;
- `speaker_scope` to distinguish `single`, `multi`, `mixed`, or other producer
  values;
- `episode_uid` for globally safe episode filtering;
- `episode_id` only when the request is already bound to one partition.

Summary nodes such as `episode_thesis` or `cluster_summary` may intentionally
have no single speaker. Do not discard them solely because a speaker filter is
active unless the product explicitly requests speaker-only evidence.

## 11. Retrieval result and provenance contract

The retrieval layer should normalize each hit to this internal shape:

```json
{
  "rank": 1,
  "distance": 0.183,
  "chroma_id": "episode-01-leaf-0001",
  "text": "The proposed launch date is ...",
  "partition_id": "partition_podcast",
  "corpus_id": "partition_podcast",
  "release_id": "chroma_release_01_baseline",
  "upstream_release_id": "release_partition_podcast_20260905_01",
  "handoff_ids": ["handoff_20260905T180000Z_01"],
  "episode_uid": "partition_podcast:podcast-2026-01-03",
  "episode_id": "podcast-2026-01-03",
  "episode_title": "Podcast 20260103",
  "episode_date": "2026-01-03",
  "speaker": "Host",
  "node_type": "leaf_chunk",
  "source": "episode.json",
  "source_span_ids": ["123"],
  "start": 12.34,
  "end": 18.91
}
```

Fields unavailable in Chroma metadata must be `null` or omitted; Chat must not
invent timestamps, speakers, source spans, or episode titles. The answer layer
must keep the release and partition identity attached to every evidence item so
that citations cannot cross contexts.

Minimum answer provenance should include:

- the display name and `partition_id`/`corpus_id` used;
- downstream `release_id` and `upstream_release_id`;
- the retrieved Chroma record IDs;
- episode title/ID/UID;
- speaker when present;
- source span/timestamp when present;
- the representation ID used for retrieval.

If no valid evidence is retrieved, Chat should say that the selected context
does not contain indexed evidence for the question. It must not ask the user to
provide the transcript when the database is valid but has no matching result,
and it must not fill the gap with evidence from another partition.

## 12. Release lifecycle and atomicity

The importer validates source files, metadata, embeddings, evidence, and a
retrieval smoke check in staging. It promotes a release only after those checks
pass. PodCast Chat must rely on that behavior and treat a release as immutable
after promotion.

Expected reader behavior:

- read the active pointer once at session start;
- hold the resolved release identity for the session;
- on refresh, reread the pointer and compare the identity tuple;
- if the pointer changes, finish the current request against the old complete
  export or start a new session against the new complete export;
- never hold a writable handle to the source or release metadata;
- never modify `active-release.json` as part of a query.

An interrupted importer operation must leave either the previous valid active
release or a complete newly promoted release. A leftover staging directory is
not evidence that a release is usable.

## 13. Historical releases and rollback

Historical releases may be opened for an explicit “view release” or
reproducibility action. Such a view must show:

- display name and context type;
- `partition_id` and `corpus_id`;
- downstream and upstream release IDs;
- representation/model identity;
- release creation time;
- whether it is active, historical, quarantined, or unavailable.

Chat must not silently switch the active pointer. Rollback is an importer or
operator operation that atomically changes the pointer after validating the
target release. Chat should observe the new pointer on its next refresh.

## 14. Legacy and invalid input behavior

PodCast Chat must not directly consume:

- arbitrary recursive folders of `*.processed_documents.json` files;
- a flat Chroma directory with no managed release metadata;
- a legacy folder whose identity is only a podcast name or filename;
- a release marked `quarantined` or `failed`;
- `staging/` output;
- the importer’s private `state/context_catalog.sqlite3`.

Legacy content becomes Chat-compatible only after an explicit adoption/migration
operation creates a managed export with a synthetic legacy scope and complete
downstream metadata. Even then, the legacy scope remains isolated from
producer-managed partitions.

## 15. Privacy and security

PodCast Chat should treat `context_type`, workflow profile, and producer privacy
metadata as access-control inputs when present. At minimum:

- do not expose an archived or hidden local context in normal selection;
- do not allow a user to query a context they are not authorized to access;
- do not display or log local source roots, absolute paths, model cache paths,
  secrets, or private importer state;
- escape source text and metadata when rendered as Markdown/HTML;
- consider transcript text and metadata untrusted input;
- preserve confidentiality boundaries even when two contexts have the same
  display name or similarly named episodes.

An application-level permission system may sit above this contract. The
contract’s identity fields are necessary for authorization decisions but are not
themselves proof that a user is authorized.

## 16. Versioning and compatibility

This v1 contract uses exact contract IDs:

- upstream handoff: `podcast-rag-transcription-handoff-v1`;
- upstream release: `podcast-rag-corpus-release-v1`;
- downstream Chroma export: `chroma-export-release-v1`;
- PodCast Chat consumer handoff: `podcast-chat-database-handoff-v1`.

Patch-level additive fields are backward-compatible when required fields retain
their meaning. A new major contract version must be explicitly supported by
Chat before use. Chat must report unsupported versions clearly and must not
guess a schema based on filenames.

The following identity changes are never in-place edits:

- `partition_id`;
- `corpus_id`;
- `episode_uid`;
- `upstream_release_id`;
- downstream `release_id`;
- `representation_id`.

A display name or local alias may change without moving the database. A changed
import profile or representation creates a separate downstream release/vector
space; it must not mutate an existing release that Chat may still be using.

## 17. Reference consumer algorithm

The following pseudocode is normative in behavior, though not in programming
language:

```text
load managed_output_root
read partitions/<partition_id>/active-release.json
validate pointer IDs and release_id path

read release.json
read podcast.json
read import_manifest.json
validate contract versions
validate pointer == release == podcast == import_manifest identity
validate database_id == corpus_id
validate unique episode_uids and partition prefixes
validate representation_id/model/revision/dimension/metric agreement

open export/chroma.sqlite3
get collection_name from validated metadata
validate collection and optional redundant partition/corpus metadata
bind session to (partition_id, corpus_id, release_id, representation_id)

for each user query:
    embed with the validated representation
    query only this collection
    apply optional episode/speaker/node filters
    return hits with identity and provenance
    generate an answer only from those hits
```

## 18. Acceptance tests for PodCast Chat

The consumer implementation should include tests proving that:

1. Two contexts with identical filenames remain separate databases and produce
   different `episode_uid` values.
2. A valid active pointer opens exactly the pointed-to release.
3. A missing or malformed pointer never falls back to a random release.
4. Pointer/release/podcast/import-manifest partition or corpus mismatches disable
   retrieval before a query.
5. `database_id` is always the validated `corpus_id`.
6. A release with duplicate or foreign `episode_uids` is rejected.
7. A `partition_id != corpus_id` mapping is accepted only with producer registry
   evidence.
8. A model, revision, dimension, normalization, metric, or representation
   mismatch disables retrieval rather than silently re-embedding with another
   configuration.
9. A query cannot retrieve records from another partition, even when the record
   has the same episode filename or display name.
10. Speaker and episode filters preserve partition/corpus identity.
11. Returned citations preserve release, handoff, episode, speaker, and source
    span provenance when present.
12. Missing source spans or timestamps are displayed as unavailable, not
    fabricated.
13. A release with no matching evidence produces a context-local no-evidence
    response.
14. An interrupted promotion leaves the prior active release usable.
15. A new active pointer is picked up only after refresh/restart and never
    partially.
16. Archived, hidden, quarantined, staging, and legacy-only inputs are not
    selectable as normal active databases.
17. Portable JSON contains no absolute machine paths or secrets.
18. Historical release inspection does not change the active pointer.

## 19. Producer/importer responsibilities

Chroma DB Import is responsible for:

- validating upstream handoff and release contracts before embedding;
- preserving partition, corpus, episode, handoff, cache, and representation
  lineage;
- creating one physically isolated Chroma export per managed partition/release;
- writing portable `podcast.json`, `import_manifest.json`, and `release.json`;
- atomically promoting only complete validated exports;
- preserving immutable historical exports and the active-release pointer;
- keeping local source paths and UI preferences in the private catalog rather
  than portable artifacts.

PodCast Chat is responsible for:

- validating the managed export before use;
- binding every session and query to one validated context and representation;
- using the declared embedding configuration for query vectors;
- preserving provenance into the answer and citation layer;
- preventing cross-partition retrieval in v1;
- reporting unavailable, quarantined, incompatible, or empty contexts clearly;
- treating source text and metadata as untrusted content.

This division keeps context creation authoritative upstream, database export
deterministic in Chroma DB Import, and retrieval safe and independently
interrogable in PodCast Chat.

## 20. Deduplication-aware v2 extension

This section is an explicit extension. It does not change the meaning or
acceptance rules of `podcast-chat-database-handoff-v1`. A consumer that only
supports v1 must reject a release whose `release_contract_version` is
`chroma-export-release-v2`, rather than treating it as v1 by filename or by
ignoring unknown fields.

### 20.1 v2 capability declaration

Safe and Audit exports use:

```json
{
  "release_contract_version": "chroma-export-release-v2",
  "required_capabilities": ["dedup-aliases-v1"],
  "dedup": {
    "manifest_path": "dedup_manifest.json",
    "manifest_sha256": "sha256:...",
    "ledger_path": "dedup_occurrences.jsonl",
    "ledger_sha256": "sha256:...",
    "policy_fingerprint": "sha256:...",
    "plan_fingerprint": "sha256:..."
  }
}
```

The v2 release still contains the v1 identity and representation fields, and
still has one physical Chroma database per `(partition_id, corpus_id)` scope.
`dedup_manifest.json` has contract version `chroma-export-dedup-v1`; its
`policy`, `plan_fingerprint`, `coverage`, exact groups, near edges, and ledger
reference are authoritative for repetition control. `dedup_occurrences.jsonl`
is sorted by original `document_id` and contains every eligible occurrence,
including suppressed aliases and the original page content/provenance.

The ledger’s portable row fields are:

```text
document_id, node_id, partition_id, corpus_id, episode_uid,
verified_cache_fingerprint, page_content, producer_metadata,
normalized_text_hash, source_span_hash, duplicate_group_id,
preferred_canonical_id, storage_status, alias_target_id,
decision_method, decision_reason
```

The consumer must verify both artifact hashes and all release/scope/representation
bindings before opening the collection. Safe stores only the canonical ID for
an exact group. Audit stores all members while declaring the preferred
canonical ID. A direct alias target must exist in the same ledger; alias chains,
cycles, missing targets, foreign scopes, and duplicate ledger IDs are invalid.

### 20.2 Query and collapse rules

For a v2 session, Chat binds to one validated partition, corpus, downstream
release, and representation exactly as in v1. It then:

1. validates every returned hit against that identity before applying filters;
2. applies episode, speaker, node, and access filters before representative
   selection;
3. asks the vector store for `min(collection_count, 200, 3 * top_k)` candidates
   when retrieval repetition control is enabled, with `top_k` a positive
   integer no greater than 200;
4. preserves the store’s supplied ranking and breaks equal ranks by document ID;
5. keeps the first eligible hit per validated exact duplicate group, while
   retaining ungrouped hits and cross-episode occurrences independently;
6. attaches only filter-eligible related occurrence provenance from the ledger;
7. reports bounded underfill when fewer than `top_k` representatives remain.

Near edges are report-only in v2. They never cause suppression or retrieval
collapse. Disabling retrieval repetition control changes the candidate request
to `min(collection_count, top_k)` and returns the first eligible hits without
group collapse; artifact, identity, and provenance validation remain required.
No consumer may reinterpret a distance value as cosine similarity or perform
unbounded refills after collapse.

### 20.3 Provenance and no-evidence behavior

The answer layer must preserve the returned occurrence ID even when it resolves
an alias to the canonical stored record. Citations should identify the original
occurrence, canonical stored record, duplicate group when present, episode UID,
speaker/source span when present, and the active release/representation. A
valid v2 context with no matching eligible evidence produces a context-local
no-evidence response; it does not fall back to another partition or request
the source transcript.

### 20.4 v2 validation failures

PodCast Chat must disable the v2 database before querying if any required v2
artifact is missing, outside the export root, hash-mismatched, malformed, or
inconsistent with Chroma IDs/counts. It must report unsupported capabilities,
quarantined releases, archived contexts, incomplete staging, and invalid alias
or group membership clearly. The importer’s private catalog and local source
paths are not v2 inputs.
