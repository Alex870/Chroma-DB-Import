# Chroma DB Import

Imports pre-processed podcast RAG documents into a persistent Chroma collection.

This project intentionally does not call LM Studio and does not perform transcript preprocessing. It expects processed cache files produced by `Podcast-RAG-pipeline` under `processed_data`.

The desktop UI is the primary workflow. A headless CLI path still exists for automation, diagnostics, and recovery, but it now lives behind the script launcher and Python package entrypoint instead of a root compatibility wrapper.

## Repository Layout

- `src/chroma_db_import/`: package source, split into contract checks, importer core, diagnostics, UI models/helpers/export logic, and the main window shell
- `Run Chroma DB Import.ps1`: root bootstrap launcher for environment validation, desktop UI, legacy-state migration, and environment setup
- `scripts/`: PowerShell launchers and environment diagnostics
- `scripts/Migrate-LegacyChromaDbImportState.ps1`: guided migration assistant for importing config, caches, state, and repo-local directories from a legacy working directory
- `examples/`: editable config templates
- `docs/`: shared data-contract notes and roadmap material
- `tests/`: focused unit coverage for contract checks and update-import behavior
- `chroma_db_import_ui.py`: compatibility entry point for legacy UI commands

The direct Python dependencies are exact-pinned in `chroma_db_import_requirements.txt`. Use `scripts/Test-ChromaDbImportEnvironment.ps1` for the clean-machine CUDA/CPU, config, staging-recovery, and output-contract diagnostics before launching the UI.

## Architecture

- `contract.py`: shared schema and compatibility checks for processed documents, manifests, and `podcast.json`
- `importer.py`: Chroma write path, speaker filtering, embedding cache reuse, and resumable batch import
- `diagnostics.py`: preflight validation, manifest writing, bundle capture, and collection inspection helpers
- `ui_export.py`: export planning, update-mode merge logic, and metadata generation for `PodCast Chat`
- `ui_window.py`: the main desktop workflow shell, keeping widget code separate from export logic

## Launchers

For normal use, start with the root bootstrap:

```powershell
.\Run Chroma DB Import.ps1
```

It gives you a simple choice:

1. Run environment validation
2. Run the desktop UI
3. Migrate settings and state from a legacy directory
4. Create or refresh the CLI and UI environments

If you already have an older working directory with import state or a populated local Chroma export, choose `3`. The migration assistant opens a folder picker, warns once before overwriting or merging into existing targets, copies forward the runtime config plus durable state/assets, and rewrites repo-local absolute config paths to portable repo-relative paths in the new `chroma_db_import_config.json`.

## UI Workflow

```powershell
.\Run Chroma DB Import.ps1
```

Choose `2` for the desktop UI. The lower-level script remains available at `.\scripts\Run-ChromaDbImportUi.ps1` when you want to bypass the menu.

Use the toolbar from left to right:

- `Open`: choose a folder containing `*.processed_documents.json` files, such as `Podcast-RAG-pipeline\processed_data`.
- `Output`: choose a folder where self-contained exports will be written.
- `Generate`: rebuilds the selected podcast export from scratch. If the export folder already exists, the UI prompts before deleting it.
- `Update`: appends new episodes and also imports an already-listed episode when the current speaker selection includes at least one speaker that is not yet represented for that episode in `podcast.json`.
- `Dry Run`: validates the current selection and shows document counts, speakers, episodes, date range, and expected errors/warnings without writing Chroma.
- `Validate`: runs the processed-cache validation screen independently of import.
- `Settings`: edit podcast/output/settings/speaker selections. Use `Save Settings` / `Load Settings` inside this page to persist and restore them.
- `Guide`: view the expected new-export and update workflows.

The UI startup script prints a CUDA/PyTorch diagnosis before launching. To install a CUDA-enabled PyTorch build into the UI virtual environment, run:

```powershell
.\scripts\Run-ChromaDbImportUi.ps1 -InstallCudaTorch
```

By default this uses the PyTorch CUDA 12.8 wheel index:

```text
https://download.pytorch.org/whl/cu128
```

This is separate from the NVIDIA driver-reported CUDA version. A newer NVIDIA driver can normally run applications built against an older CUDA runtime such as the PyTorch CUDA 12.8 wheel.

The export layout is:

```text
Selected Output Folder/
  Podcast Name/
    chroma.sqlite3
    podcast.json
    ...Chroma internal files...
```

The tree has a single `Global Settings` root node. Episode nodes are ordered by ascending episode date. Speaker checkboxes under `Global Settings` are tri-state: checked means included for every episode where that speaker appears, unchecked means excluded everywhere, and partially checked means included for some episodes but not all.

Excluded speakers are omitted from the generated metadata and their speaker-specific documents are not inserted. Episode-level thesis documents and multi-speaker summary nodes are preserved to keep retrieval context useful.

Every UI export writes `podcast.json` and `import_manifest.json`. The manifest records importer version, selected config, source cache fingerprints, embedding model and dimension, selected speakers, validation results, source coverage, and compatibility warnings.

## Corpus Release Workflow

After an update or reconcile export has completed, bind it to the validated
`processed-delta-v1` that caused the update:

```powershell
conda run -n chroma-db-import python -m chroma_db_import.release_cli plan-release `
  "D:\path\to\processed-delta.json" `
  --store ".\state\corpus-releases" `
  --embedding ".\state\embedding-identity.json" `
  --selection "approved-speaker-selection-v1" `
  --export "D:\Pod Cast RAG\ChromaDB\podcast" `
  --output ".\state\corpus-release-plan.json"

conda run -n chroma-db-import python -m chroma_db_import.release_cli stage `
  ".\state\corpus-release-plan.json" `
  --store ".\state\corpus-releases" `
  --export "D:\Pod Cast RAG\ChromaDB\podcast"
```

Inspect the release plan before promotion, then provide its exact `plan_id`:

```powershell
conda run -n chroma-db-import python -m chroma_db_import.release_cli promote `
  ".\state\corpus-release-plan.json" `
  --store ".\state\corpus-releases" `
  --approve "release_plan_..."

conda run -n chroma-db-import python -m chroma_db_import.release_cli status `
  --store ".\state\corpus-releases"
```

Planning validates the delta identity, evidence closure, export fingerprint,
embedding identity, representation identity, and exact add/change/removal set.
The plan also records measured export/database size, exact reconciliation work,
embedding-cache reuse, and importer elapsed time when the producing export
provides it. Legacy exports without timing remain supported and report that
metric as unavailable rather than using a guessed duration.
Staging also requires successful source validation, Chroma staging validation,
and pinned retrieval smoke-query evidence. The staged `podcast.json` and
`import_manifest.json` receive the same `corpus_release_id`, and
`release.json` travels inside the release export.

Removals remain advisory by default. Use `--reconcile-removals` only when the
source export was produced by the UI's explicit Reconcile operation. Rollback
requires an approval generated by `plan-rollback`; retention deletion similarly
requires `plan-prune` followed by `prune`. Promotion never deletes old releases
automatically.

The embedding identity file is a small JSON object describing the actual
exported embedding space, for example:

```json
{
  "provider": "sentence_transformers",
  "model": "BAAI/bge-large-en-v1.5",
  "dimensions": 1024
}
```

The release records Podcast-RAG's processed-text representation fingerprint and
Chroma's vector representation ID separately; they are intentionally not
treated as the same contract field. When the release store already has an active
release, planning automatically uses it as the parent and compares its embedding
identity with the requested release.

## Embeddings

The default embedding model is:

```text
BAAI/bge-large-en-v1.5
```

This model converts each selected RAG document into a dense vector before insertion into Chroma. `PodCast Chat` later embeds the user's question with the same model and asks Chroma for nearby vectors, which is how the chat app finds semantically relevant podcast viewpoints before sending context to LM Studio.

`bge-large-en-v1.5` was chosen as the default because it is a strong general-purpose English retrieval embedding model with good semantic search behavior for natural-language questions, transcript excerpts, summaries, and viewpoint-style passages. It is also widely available through Hugging Face tooling, works on CPU, and can be accelerated with CUDA PyTorch when a compatible NVIDIA GPU environment is installed. That makes it a good fit for a Windows desktop workflow where reliability and easy setup matter, while still allowing faster imports on GPU-equipped machines.

The embedding model is configurable in the UI and in the CLI config, but the Chroma export and the chat client must agree on the model. If a database is generated with one embedding model and queried with another, vector distances become unreliable because the vectors no longer live in the same embedding space. The generated `podcast.json` records the embedding model and detected embedding dimension so `PodCast Chat` can display and use the expected values.

## Setup

```powershell
.\Run Chroma DB Import.ps1
Copy-Item .\examples\chroma_db_import_config.example.json .\chroma_db_import_config.json
```

Choose `4` to create or refresh both the CLI Conda environment and the desktop UI virtual environment.

Edit `chroma_db_import_config.json` so `processed_data_dir` points at the preprocessed cache directory and `persist_dir` points at the Chroma database directory you want to populate.

Option `4` installs CUDA-enabled PyTorch into both environments automatically. For direct CLI environment setup, run:

```powershell
.\scripts\Run-ChromaDbImport.ps1 -CreateCondaEnv -InstallCudaTorch
```

or, for an existing environment:

```powershell
.\scripts\Run-ChromaDbImport.ps1 -InstallCudaTorch -SkipDependencyCheck
```

For the desktop UI environment only:

```powershell
.\scripts\Run-ChromaDbImportUi.ps1 -InstallCudaTorch -NoLaunch
```

## Test

```powershell
.\Run Chroma DB Import.ps1
```

Choose `1` for environment validation. The lower-level script remains available at `.\scripts\Test-ChromaDbImportEnvironment.ps1`.

The test script checks `nvidia-smi`, PyTorch version, PyTorch CUDA runtime, `torch.cuda.is_available()`, and CUDA device names when available. If `nvidia-smi` sees the GPU but PyTorch reports CUDA unavailable, reinstall PyTorch with:

```powershell
conda run -n chroma-db-import python -m pip install --upgrade --force-reinstall torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```

## Advanced CLI Import

```powershell
.\Run Chroma DB Import.ps1
```

The CLI importer is still available for automation, diagnostics, and headless operation through `.\scripts\Run-ChromaDbImport.ps1`.

Useful options:

```powershell
.\scripts\Run-ChromaDbImport.ps1 -OneFile
.\scripts\Run-ChromaDbImport.ps1 -DryRun
.\scripts\Run-ChromaDbImport.ps1 -ValidationOnly
.\scripts\Run-ChromaDbImport.ps1 -EmbeddingBenchmark
.\scripts\Run-ChromaDbImport.ps1 -DiagnosticBundle
.\scripts\Run-ChromaDbImport.ps1 -ListCollections
.\scripts\Run-ChromaDbImport.ps1 -InspectCollection
.\scripts\Run-ChromaDbImport.ps1 -DeleteCollection
.\scripts\Run-ChromaDbImport.ps1 -ProcessedDataDir "D:\Pod Cast RAG\Podcast-RAG-pipeline\processed_data"
.\scripts\Run-ChromaDbImport.ps1 -PersistDir "D:\Pod Cast RAG\Podcast-RAG-pipeline\chroma_db_raptor_v2"
.\scripts\Run-ChromaDbImport.ps1 -CollectionName "whisper_rag_v2"
```

Before any Chroma write, the importer creates a preflight report at `state/preflight_report.json`. It validates required metadata, empty content, duplicate IDs, missing speaker/date metadata, malformed position cards, embedding-model mismatch risk, and existing manifest changes. `-DryRun` and `-ValidationOnly` stop after this phase.

Progress is tracked in `state/chroma_import_state.json`. Chunk-level batch state is stored under `state/import_batches`, so an interrupted large import can resume after completed batches. If `skip_existing_ids` is enabled, documents whose stable document ID or `node_id` already exists in Chroma are skipped.

The migration assistant is built around these same durable assets. It can copy forward `chroma_db_import_config.json`, resumable state, batch-state files, embedding caches, preflight reports, diagnostics, repo-local processed-cache folders, and repo-local Chroma persist directories from a legacy repo without forcing you to rebuild imports from scratch. When a legacy config stored absolute paths inside the old repo tree, the migrator rewrites those values to portable repo-relative paths in the new config.

Embedding cache files are stored under `state/embedding_cache` when `cache_embeddings` is enabled. This lets rebuilds avoid re-embedding unchanged processed documents when only selection or metadata changes.

Each completed CLI import writes `import_manifest.json` inside `persist_dir`. The manifest includes importer version, selected source files, content fingerprints, validation results, embedding model and dimension, selected speakers, and compatibility warnings for downstream chat clients.

`-EmbeddingBenchmark` embeds a small sample batch and prints throughput plus detected embedding dimension. `-DiagnosticBundle` writes config, memory/CUDA status, package versions, and state snapshots under `state/diagnostics`. `-ReleaseBuild` writes local release metadata and prints the one-command clean environment build recipe.

## Shared Data Contract

The expected transcript, processed-cache, Chroma metadata, and `podcast.json` fields are documented in [`docs/podcast_pipeline_contract.md`](docs/podcast_pipeline_contract.md). The executable checks live in [`src/chroma_db_import/contract.py`](src/chroma_db_import/contract.py), with a root compatibility wrapper at `chroma_import_contract.py`; use that module when changing any upstream or downstream project so the four-tool pipeline stays compatible.

## Managed contexts

Managed mode creates one independent Chroma database for each Podcast-RAG
partition. The producer handoff/release manifest is authoritative for
`partition_id`, `corpus_id`, and `episode_uid`; the importer never assigns a
partition from a folder or filename. See the authoritative
[`transcription-handoff-contract.md`](C:/temp/codex/Podcast-RAG-pipeline/transcription-handoff-contract.md).

Use the desktop app's **Contexts** action to link a Podcast-RAG project once,
discover valid handoffs/releases, inspect each context, and import its newest
release. Contexts and local preferences are stored in the private
`state/context_catalog.sqlite3` catalog, so JSON editing is not required.

The equivalent headless workflow is:

```powershell
chroma-db-import contexts link-source --root "C:\path\to\Podcast-RAG-pipeline" --output-root "D:\RAG\databases"
chroma-db-import contexts discover
chroma-db-import contexts list
chroma-db-import import --partition podcast-history
chroma-db-import status --partition podcast-history
chroma-db-import contexts set-dedup --partition podcast-history --profile safe
chroma-db-import import --partition podcast-history --dry-run
chroma-db-import contexts review-dedup --partition podcast-history
```

Each managed export is isolated under
`<output-root>\partitions\<partition_id>\releases\<release_id>\export` and
contains `chroma.sqlite3`, `podcast.json`, `import_manifest.json`, and a
downstream release record. Normal managed imports reject mixed partitions;
cross-context aggregation requires a separate future aggregation contract.

Managed contexts use a local SQLite catalog rather than hand-edited JSON. New
producer-defined contexts default to the `safe` deduplication profile. Existing
catalogs migrate conservatively to `off`; change that choice explicitly with
`contexts set-dedup`. `safe` suppresses only exact copies with matching source
span, source revision, producer metadata, and embedding input. `audit` retains
all eligible records while recording what Safe would suppress. `off` preserves
the existing v1 export behavior. Near matches are a bounded lexical report and
never suppress records in v1.

Safe and Audit exports declare `chroma-export-release-v2` and the
`dedup-aliases-v1` capability. They include a portable occurrence ledger so a
consumer can resolve direct aliases while retaining original provenance. PodCast
Chat must explicitly support that capability; this repository does not claim
live Chat integration. Use `contexts review-dedup` to inspect validated groups,
reasons, coverage, and near-report completeness before opening a database.
Changing deduplication, embedding, or representation settings creates a new
immutable downstream release/vector space. Rebuild-only migration is required
when adopting legacy flat folders; legacy content is never silently imported as
a managed context.

### Semantic redundancy assessment

Semantic redundancy is an opt-in, advisory assessment over a validated managed
v2 release. It never rewrites the active release, deletes evidence, or turns a
semantic relationship into an exact alias. The default retrieval mode remains
`ranked`; `semantic_mmr` and `preserve_occurrences` are bundle-side selection
modes for a consumer that explicitly supports the contract.

The model-free preview and assessment commands are:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
python -m chroma_db_import redundancy policy --catalog .\state\context_catalog.sqlite3 --partition podcast-history
python -m chroma_db_import redundancy configure-judge --catalog .\state\context_catalog.sqlite3 --partition podcast-history --base-url http://localhost:1234/v1 --model <explicit-installed-model-id>
python -m chroma_db_import redundancy models --base-url http://localhost:1234/v1
python -m chroma_db_import redundancy preview --catalog .\state\context_catalog.sqlite3 --partition podcast-history --release <release-id>
python -m chroma_db_import redundancy assess --catalog .\state\context_catalog.sqlite3 --partition podcast-history --release <release-id> --channels lexical,structural
python -m chroma_db_import redundancy review --bundle <published-bundle>
python -m chroma_db_import redundancy label-export --bundle <published-bundle> --output .\state\redundancy-labels.json
python -m chroma_db_import redundancy evaluate --bundle <published-bundle> --labels .\state\redundancy-labels.json --output .\state\redundancy-evaluation.json
# Optional measured query arm (JSON object: arm -> query_id -> ranked occurrence IDs)
python -m chroma_db_import redundancy evaluate --bundle <published-bundle> --labels .\state\redundancy-labels.json --queries .\state\redundancy-queries.json --query-results .\state\redundancy-query-results.json --output .\state\redundancy-evaluation.json
```

The desktop importer exposes the same workflow from the selected Context via
the **Redundancy** panel: scoped policy/model configuration, Preview, Assess,
bounded Pilot Judge, Resume/Cancel, bundle Review, label export, and frozen
evaluation. These actions run away from the UI thread and remain advisory;
they do not activate a bundle in the consumer.

Dense assessment requires the configured Chroma runtime and uses the stored
vectors without calling an embedding provider. A local judge pilot additionally
requires an explicitly configured LM Studio model; the client uses only the
loopback-compatible `/v1/models` and `/v1/chat/completions` endpoints. Missing
runtime, model, query measurements, or human-reviewed labels remain explicit
pending/insufficient-evidence states. A published redundancy bundle is portable
evidence plus candidate/judgment artifacts; it does not include catalog state,
credentials, or a request to automatically activate a retrieval mode.
Shared-input bundles attempt to materialize a separate cosine Chroma collection
containing one vector per verified identical embedding input, with a portable
JSON sidecar retained for validation. If Chroma is unavailable, the bundle
records the JSON fallback explicitly; it is not presented as a live compact
consumer backend. No semantic relationship deletes or rewrites evidence, and
full-base recovery remains an explicit consumer choice.

## Direct Python Usage

```powershell
python -m chroma_db_import --config .\chroma_db_import_config.json
python -m chroma_db_import --dry-run
python -m chroma_db_import --inspect-collection
```
