# Roadmap

This roadmap captures practical feature upgrades for `Chroma DB Import`, based on the current split where this project owns importing preprocessed podcast RAG documents into self-contained Chroma exports.

## Highest-Impact Improvements

Implementation status: all roadmap areas below now have an initial implementation. The importer has executable validation, dry-run and validation-only modes, manifest versioning, rebuild/update safety checks, resumable batch state, embedding caching, import reports, UI dry-run/validation actions, podcast metadata validation, embedding benchmarks, diagnostics, release metadata, and focused tests. Future passes should tune the UI collection-management surface against real Chroma exports and expand fixture coverage as new processed-cache edge cases appear.

- Add an import preflight report that validates every selected `*.processed_documents.json` file before any Chroma writes happen. The report should flag missing required metadata, empty `page_content`, duplicate `node_id` values, speaker/date inconsistencies, embedding-model mismatch risk, and malformed `position_card` records.
- Add a dry-run mode for both CLI and UI imports. It should show document counts by `node_type`, speaker, episode, date range, skipped IDs, newly inserted IDs, and expected Chroma collection changes without modifying the database.
- Add export manifest versioning. Each generated podcast export should include an import manifest with importer version, config, source cache fingerprints, embedding model, embedding dimension, import timestamp, selected speakers, document counts, and validation results.
- Add rebuild/update safety checks. Before deleting or appending to an existing export, compare the current manifest and selected source files so the UI can warn about changed embedding models, changed collection names, or partial prior imports.
- Add resumable chunk-level import state. Current state is file-oriented; large imports would be safer if embedding and Chroma insertion batches could resume after interruption without re-embedding already inserted batches.

## UI Workflow

- Add an import summary screen after `Generate` or `Update` showing total documents imported, skipped documents, speaker coverage, node-type distribution, time elapsed, embedding throughput, and any warnings.
- Add a searchable episode/speaker matrix that shows which speakers are included for each episode and how many documents each speaker contributes.
- Add a validation tab that can be run independently of importing. This would make the tool useful as a quality gate between preprocessing and chat.
- Add explicit collection-management controls: list collections, inspect collection metadata, delete a selected collection, rename export folder, and open the export folder in Explorer.
- Add a settings profile system for common workflows, such as full rebuild, host-only export, known-speakers-only export, and test subset export.

## Data Contract And Compatibility

- Promote `docs/podcast_pipeline_contract.md` into an executable schema check shared with the other projects.
- Validate `podcast.json` after generation, including date ranges, speaker IDs, speaker display names, embedding model, embedding dimension, and source file coverage.
- Record the source preprocessing pipeline version or commit hash when available.
- Add compatibility warnings in `PodCast Chat`-facing metadata if a database was built with a different embedding model than the chat settings expect.

## Performance And Scale

- Add batch-size tuning for embedding generation and Chroma writes, with live throughput reporting.
- Cache embeddings for unchanged processed documents so export rebuilds can avoid re-embedding content when only speaker selection or metadata changes.
- Add optional GPU embedding benchmark at startup to recommend CPU vs CUDA and a safe batch size.
- Add memory-use telemetry for large imports and surface warnings before importing very large collections.

## Testing And Quality

- Expand tests around update imports, duplicate IDs, speaker filtering, manifest generation, and malformed processed cache handling.
- Add a small synthetic processed-data fixture that represents leaf chunks, cluster summaries, episode thesis documents, and position cards.
- Add CLI smoke tests for `--one-file`, rebuild, update, dry-run, and validation-only workflows.
- Add snapshot tests for generated `podcast.json` so downstream compatibility regressions are obvious.

## Packaging

- Add a one-command local release build for the UI, including a clean virtual environment, pinned dependencies, and a generated version file.
- Add a troubleshooting command that writes a single diagnostic bundle with config, package versions, CUDA status, Chroma version, and recent import logs.
- Consider a portable Windows build once the UI workflows stabilize.
