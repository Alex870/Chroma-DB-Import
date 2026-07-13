# Roadmap

`Chroma DB Import` is the contract boundary between processed podcast artifacts and self-contained Chroma exports. The roadmap prioritizes repeatability, vector-space integrity, and traceability over source-runtime labels.

## Principles

- Make imports deterministic, inspectable, and safe to resume.
- Keep old valid caches readable while making metadata additive and versioned.
- Never mix incompatible embedding spaces in one collection.
- Treat `podcast.json` and import manifests as downstream contracts.
- Explain exactly what an update inserted, skipped, replaced, or removed.

## Current Foundation

- PySide import UI, episode/speaker selection, rebuild/update modes, metadata generation, CUDA diagnostics, and incremental-import tests.

## Priority 1: Export Contract And Provenance

- Define versioned schemas for `podcast.json` and `import_manifest.json`.
- Record collection name, Chroma version, embedding model/fingerprint/dimension, selected speakers, source-cache IDs, counts, and timestamp.
- Keep document-level provenance back to processed node IDs, episode, speaker, timestamps, and source text hash.
- Preserve optional source-model metadata as provenance, not a compatibility requirement.
- Share fixtures with Podcast Chat and RAGScope.

## Priority 2: Safe Incremental Import

- Use content hashes to classify source episodes as new, changed, unchanged, or deleted.
- Update when newly selected speakers add eligible nodes, even if an episode was previously imported.
- Retain historical vectors by default; make deletion a deliberate reconcile mode.
- Validate in a staging collection or recoverable transaction boundary before promotion.
- Emit a machine-readable inserted/updated/skipped/omitted/failed report with reasons.

## Priority 3: Embedding-Space Governance

- Block reuse when model, normalization, or dimension differs.
- Provide explicit migration exports rather than silently mixing vector spaces.
- Run a pinned-query embedding smoke test before large generation jobs.
- Cache embeddings by content hash plus embedding fingerprint, with clear invalidation.

## Priority 4: Data Quality Gates

- Validate hierarchy, speaker, date, and provenance fields before import.
- Distinguish missing optional metadata from retrieval-breaking violations.
- Surface speaker/date/node-type coverage, duplicate content, exclusions, and export health.
- Ensure omitted speakers are absent from both vectors and metadata.

## Priority 5: Tests And Interoperability

- Test generate, update, migration, rollback, and collection validation with synthetic multi-episode fixtures.
- Verify Podcast Chat scanning and RAGScope provenance reads for generated exports.

## Sequencing

1. Publish export/manifest schemas.
2. Implement source-hash classification and reports.
3. Add embedding migration safeguards.
4. Add staging validation.
5. Expand cross-project contract tests.
