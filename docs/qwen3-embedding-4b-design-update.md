# Design Update: Qwen3-Embedding-4B

Status: sole production profile implementation  
Date: 2026-09-07  
Scope: `Chroma DB Import`, with coordinated query-client changes required before promotion

## Decision

Make `Qwen/Qwen3-Embedding-4B` the sole versioned dense representation. Older
embedding artifacts, profiles, and saved settings are rejected and must be
rebuilt as Qwen3 exports.

The Qwen3 profile produces 2,560-dimensional vectors. Collections, cache
identities, manifests, and query providers must use the complete Qwen3
representation identity.

## Evidence and operating envelope

The model was loaded successfully on the local NVIDIA GeForce RTX 5070 Ti using
the project’s CUDA environment and the current `HuggingFaceEmbeddings` path.

Measured results:

| Measurement | Result |
|---|---:|
| Model dtype | `bfloat16` |
| Model-load allocation | ~7.7 GiB |
| Output dimension | 2,560 |
| Peak for representative embedding batch | ~11.6 GiB allocated / ~11.8 GiB reserved |
| Constrained test | Passed with ~11.9 GiB available before load |
| Free after constrained embedding | ~2.4 GiB |

The initial production profile should use CUDA batch size 1–2. Preflight should
measure available VRAM and reject or warn before starting a build when the
configured batch and safety margin cannot fit. CPU remains a fallback, but is not
the target operating mode for this profile.

## Representation identity

Register the following immutable identity:

```json
{
  "profile": "qwen3-embedding-4b-shadow",
  "provider": "sentence_transformers",
  "model_id": "Qwen/Qwen3-Embedding-4B",
  "model_revision": "5cf2132abc99cad020ac570b19d031efec650f2b",
  "dimension": 2560,
  "output_dimension": null,
  "normalize_embeddings": true,
  "distance_metric": "cosine",
  "inference_dtype": "bfloat16",
  "contextualization": "minimal",
  "query_instruction_profile": "podcast-retrieval-v1"
}
```

The revision above is the locally tested Hugging Face snapshot. It must be
validated against the intended upstream revision before release and then pinned
in the provider registry. Model identity, revision, dtype, query instruction,
normalization, contextualization, and dimension must all participate in the
representation fingerprint.

## Query and document encoding

Documents continue to use the existing deterministic embedding text:

```text
<contextual header>

<document text>
```

Queries require a separate Qwen3 instruction. The first profile should use:

```text
Instruct: Retrieve podcast passages that best answer the user’s question, preserving speaker, episode, and viewpoint relevance.
Query: <user question>
```

The query instruction is a compatibility field, not an untracked UI string. It
must be stored in the manifest and used by the downstream query client. Documents
must not receive the query instruction.

## Configuration design

Use one immutable representation profile. The existing profile name is retained
to avoid an unnecessary identity rename, but it is the sole production profile:

```text
qwen3-embedding-4b-shadow
```

The model, provider, revision, dimension, normalization, distance metric, query
instruction profile, and safe batch default are resolved from that profile.
There is no custom model selector and no compatibility alias.

## Repository changes

### Provider and representation

- Add the Qwen3 model revision to `PINNED_MODEL_REVISIONS`.
- Add a named Qwen3 profile constant and profile resolver.
- Extend `RepresentationSpec` with the encoding fields that affect compatibility,
  especially inference dtype and query-instruction profile.
- Add a provider adapter that can encode plain documents and instructed queries
  separately while retaining normalized vectors.
- Keep model loading offline during imports; downloading remains an explicit action.
- Report actual dtype, resolved revision, dimension, device, and representation ID
  in diagnostics and manifests.

### Memory and batching

- Make recommended batch size model-aware rather than device-only.
- Default Qwen3 CUDA imports to batch size 2, with batch size 1 as the safe fallback.
- Add a preflight memory probe using the selected model and a representative input.
- Catch CUDA out-of-memory failures with guidance to lower batch size or select the
  CPU fallback; do not partially promote a failed candidate export.
- Record peak allocation, peak reserved memory, batch size, and device in the build
  report when available.

### Export and release isolation

- Write Qwen3 exports under a distinct immutable profile path, for example
  `exports/qwen3-embedding-4b-shadow/<build-id>`.
- Use a distinct Chroma collection or persist directory; never mix 1,024- and
  2,560-dimensional records.
- Include the profile, model revision, dimension, query instruction profile, and
  resource measurements in the release manifest.
- Reject existing non-Qwen exports; rebuilding is the migration path.
- Ensure embedding-cache keys include the complete Qwen3 representation identity.

### UI and CLI

- Add Qwen3 as an explicit candidate profile labelled with its 2,560-dimensional
  output and high-VRAM requirement.
- Show that the sole profile requires a compatible query client.
- Add CLI/profile support for model download, smoke test, Qwen3 build, and report
  inspection.
- Qwen3 is the only model and active UI/CLI profile. Existing non-Qwen settings
  fail validation and are not offered as choices.

## Cross-repository contract

Before promotion, `PodCast Chat` must consume the candidate manifest and verify an
exact match for:

- model ID and immutable revision;
- representation ID and index schema;
- embedding dimension;
- normalization and distance metric;
- contextualization/header version;
- query-instruction profile and implementation version.

An incompatible client must fail clearly rather than query a Qwen3 index with
vectors from another representation.

## Validation plan

1. Add unit tests for profile resolution, mutual exclusion, pinned revision,
   2,560-dimensional probes, dtype reporting, and query/document prompt separation.
2. Add an integration fixture proving a Qwen3 collection cannot be reconciled with
   an older collection or cache.
3. Run the CUDA smoke test on the target GPU with batch sizes 1 and 2, recording
   peak memory and throughput.
4. Build a fresh Qwen3 export from the validated source inventory. Do not update
   an existing non-Qwen collection in place.
5. Run a judged podcast query set covering exact names, quotations, dates, speaker
   viewpoints, cross-episode synthesis, and unanswerable questions.
6. Compare Recall@k, MRR/nDCG, speaker/episode constraint accuracy, latency, import
   time, peak VRAM, disk size, and failure rate.

## Promotion gates

Promote Qwen3 only when all of the following are true:

- the candidate passes manifest, collection, and query-client compatibility checks;
- judged retrieval quality improves or is demonstrably equal on critical query
  categories;
- the target GPU remains within the approved VRAM budget with the selected batch;
- import and query latency remain acceptable for local use;
- the candidate can be rolled back by changing the active release alias, without
  deleting the existing non-Qwen export.

After those gates pass, the Qwen3 index is the production default. Old exports
remain separate historical artifacts until explicitly cleaned up.

## Explicitly out of scope

- deleting existing non-Qwen collections or exports;
- deleting or rewriting existing non-Qwen collections;
- enabling Qwen3 sparse or multi-vector retrieval;
- introducing Qwen3 reranking;
- truncating the 2,560-dimensional output before a separate quality experiment;
- changing the D: runtime configuration.

## Migration runbook

1. Keep the canonical C: repository as the source of truth and review the
   Qwen3-only diff.
2. Update Podcast-RAG’s producer configuration and release metadata to emit
   Qwen3. The current D: runtime configuration and old partition release must
   be updated by a field-aware merge after explicit approval; do not replace
   those files wholesale.
3. Provision the pinned snapshot
   `Qwen/Qwen3-Embedding-4B@5cf2132abc99cad020ac570b19d031efec650f2b`.
4. Validate the processed-cache inventory, then build a fresh Qwen3 Chroma
   export. Do not update the existing non-Qwen collection in place.
5. Run dry-run validation, dimension probing, memory preflight at CUDA batch
   sizes 1 and 2, and a representative retrieval smoke test.
6. Update PodCast Chat to load the pinned model, apply the exact query
   instruction, and compare the complete representation identity.
7. Promote the Qwen3 release and active pointer only after both repositories
   pass validation.
8. Archive or delete the old export only as a separate approved cleanup.
