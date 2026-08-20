# M6 Importer Hardening

`chroma-import-hardening scale-plan` estimates atomic-stage/rollback peak disk and bounded batch sizes before mutation. `benchmark-writes` measures real temporary-Chroma writes using deterministic synthetic vectors and removes its stores. Preflight reports Chroma/model capability without acquisition. Existing release jobs remain the owner of cancellation, resume, backup, restore, promotion, rollback, retention, and explicit deletion.
