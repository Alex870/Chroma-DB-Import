# Roadmap

This roadmap defines how `Chroma DB Import` should support richer high-context preprocessing outputs while remaining fully compatible with the existing baseline export workflow.

## Compatibility Principles

- Keep deterministic import behavior as the default.
- Treat high-context preprocessing metadata as additive.
- Accept both older processed caches and newer richer manifests.
- Surface compatibility warnings before surfacing hard failures whenever possible.

## Shared Runtime Profile Model

- Recognize source-side metadata from preprocessing:
  - `runtime_profile`
  - `backend`
  - `model_name`
  - `model_capabilities`
- Record importer-side metadata:
  - embedding model
  - embedding dimension
  - selected speaker filter
  - importer runtime profile if needed

## Data Contract

- Extend executable import validation to accept:
  - old caches with minimal metadata
  - new caches with high-context fields
- Validate import manifests for:
  - source runtime profile
  - source preprocessing version
  - prompt/version manifest presence when available
- Record downstream compatibility warnings if `PodCast Chat` expects a different embedding model or export profile.

## Import Workflow

- Keep the current simple import flow as baseline.
- Add profile-aware preflight checks:
  - source cache schema version
  - embedding model mismatch risk
  - source runtime profile summary
  - structured-output provenance presence
  - judge-pass provenance presence
- Keep rebuild and update modes backward compatible.
- Preserve chunk-level resumability and embedding cache behavior.

## UI And UX

- Show source preprocessing profile in the UI.
- Show manifest compatibility warnings before generate/update.
- Add a validation view that highlights:
  - baseline-compatible caches
  - high-context caches
  - mixed-profile datasets
- Show export summary fields:
  - source profile distribution
  - embedding compatibility state
  - import manifest version

## Metadata And Export

- Extend `podcast.json` and `import_manifest.json` with:
  - source preprocessing runtime profile
  - source preprocessing backend
  - embedding model and dimension
  - compatibility warnings for chat consumers
- Keep all new fields optional for readers.

## Testing

- Add fixtures for:
  - old baseline processed caches
  - new high-context processed caches
  - mixed baseline/high-context batches
- Add tests for:
  - manifest compatibility warnings
  - import of old caches with missing new fields
  - export metadata stability

## Implementation Phases

1. Add profile-aware validation and manifest fields while keeping older caches valid.
2. Surface source runtime profile and compatibility warnings in CLI and UI.
3. Extend `podcast.json` and `import_manifest.json` with additive compatibility metadata.
4. Add mixed-profile test fixtures and import regression coverage.
5. Add UI summaries for source profile distribution and downstream compatibility.
