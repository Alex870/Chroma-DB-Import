# corpus-release-v1

Corpus releases bind source caches and processed deltas to representation,
embedding, and selection identities. Staging never changes the active pointer;
promotion and rollback are atomic and require approval for the exact plan ID.
Three recent releases plus one known-good rollback target are retained. Legacy
exports remain readable and are marked as reduced-evaluability.
