# corpus-release-v1

Corpus releases bind source caches and processed deltas to representation,
embedding, selection, and exact export-bundle identities. Staging never changes
the active pointer; promotion and rollback are atomic and require approval for
the exact plan ID. Legacy exports remain readable and are marked as
reduced-evaluability.

Production staging requires exact delta/reconciliation agreement, matching
representation and embedding identities, successful importer and Chroma
staging validation, and pinned smoke-query evidence. A promoted release
contains a consumer-readable `export/` directory whose metadata and bundled
`release.json` share one `corpus_release_id`.

Release plans include bundle-grounded work evidence: exact vector-operation
counts, embedding-cache reuse, export/database byte counts, and measured import
duration when the producer recorded it. Missing legacy timing is represented
explicitly and is never replaced with a synthetic estimate.

`remove_advisory` remains non-destructive. Actual removal requires a
`reconcile_approved` plan and an export produced in reconcile mode. Promotion
does not perform retention deletion; rollback and pruning each require their
own immutable approval identity.
