# Stage 4: safe incremental import

Chroma imports now use a complete representation identity covering provider, model revision, dimension, normalization, pooling/query mode, contextualization, index schema, and implementation version.

Every cache and episode carries content/source identity metadata. `update` retains historical records by default. Destructive removal is available only through the explicit `reconcile` workflow after a preview and confirmation:

```powershell
$env:PYTHONPATH = "src"
python -m chroma_db_import --update --dry-run
python -m chroma_db_import --update --reconcile --allow-delete-missing
```

Import writes first go through a temporary collection or temporary export. Staging validates array lengths, unique IDs, scalar metadata, source/evidence links, embedding dimension/finite values, speaker/date coverage, and a pinned retrieval smoke query. A promotion report is written under `state/import_reports`; failed staging leaves the prior valid target untouched.

The UI exposes Generate, Update, and Reconcile plans with source/eligibility counts, invalid records, embedding compatibility, proposed changes, staging destination, and validation state. The golden metadata fixture at `tests/fixtures/golden_export` is the stable handoff shape for PodCast Chat and RAGScope.
