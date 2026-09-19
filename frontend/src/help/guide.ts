export interface GuideArticle { id: string; title: string; summary: string; body: string }

export const GUIDE_ARTICLES: GuideArticle[] = [
  { id: 'create', title: 'Create a database', summary: 'Scan a processed source, choose a destination, review, and apply.', body: 'Choose the source and asset filter, inspect the scan, set a destination, and review the exact effects. Create database runs only after the review is accepted.' },
  { id: 'add-existing', title: 'Add existing', summary: 'Register an export without rewriting it.', body: 'Inspect the existing export, verify its identity and source reference, then register it. Registration does not import, relocate, or overwrite files.' },
  { id: 'update', title: 'Update', summary: 'Review changes before applying an update.', body: 'Update compares the source with stored fingerprints. Missing records remain retained during an ordinary update; removal requires a separate exact review.' },
  { id: 'selection', title: 'Selection', summary: 'Use global and per-episode speaker rules.', body: 'All speakers includes future speakers except exclusions. No speakers is an explicit empty allowlist. Episode overrides and exclusions are independent.' },
  { id: 'managed-sources', title: 'Managed sources', summary: 'Link source roots and inspect release readiness.', body: 'Link and discover reads producer manifests into importer-owned state. Refresh does not publish. Prepare is explicit and still requires an import review.' },
  { id: 'deduplication', title: 'Deduplication', summary: 'Preview saved deduplication policy without activation.', body: 'Deduplication previews are advisory and model-free. Safe and Audit profiles require downstream compatibility evidence.' },
  { id: 'redundancy', title: 'Redundancy', summary: 'Run scoped, advisory analysis.', body: 'Select an analyzable release. Coverage preview is model-free; assessment channels are this-run choices. No action on this page deletes or activates content.' },
  { id: 'settings-transfer', title: 'Settings transfer', summary: 'Export and review one scoped settings file.', body: 'Transfers are versioned and field-scoped. The application never replaces the runtime configuration or applies an unreviewed full file.' },
  { id: 'gpu-repair', title: 'GPU repair', summary: 'Review an allowlisted environment action.', body: 'Diagnosis is read-only. Repair presents the exact environment and action for review; successful installation is not proof that CUDA is usable.' },
  { id: 'recovery', title: 'Recovery', summary: 'Understand interrupted and partial work.', body: 'Activity preserves durable job state. Retry creates a fresh review where supported; resume is reserved for a compatible frozen assessment.' },
]
