# React UI completion: sequential implementation guide

Version 1.0 · 2026-09-12 · All tasks initially NOT STARTED

Design authority: [React UI specification](gui-react-ui-design.md).
Coverage evidence: [75-item Qt audit](gui-react-parity-audit-and-design.md).

## 1. Instructions for the implementing worker

You are extending a working React + TypeScript + pywebview application. Do not build
a new application, repeat the original GUI overhaul plan, recreate Qt screens, or
copy the prototype's simulated handlers. Follow this document in order. Finish one
numbered task and record evidence before starting its dependent task.

### Working rules

1. Read `AGENTS.md`, then the UI specification and this guide.
2. Work only in `C:\temp\codex\Chroma DB Import`. The user has explicitly chosen
   **C: only** for this work. Do not copy to, test against, or change the D: runtime
   copy/configuration. Do not ask the propagation question again while that answer
   remains in force.
3. Record `git status --short` before editing. Existing unrelated changes belong to
   the user. Do not reset, clean, reformat, stage, commit or push them. No automatic
   commit/push is part of this guide.
4. Test with generated fixtures and an explicit temporary state directory under C:.
   Never construct the default live workflow service in tests. Pass `state_dir`.
   Do not point fake jobs, installers or migrations at production data.
5. Use existing pinned dependencies. Do not upgrade React, Vite, pywebview, Chroma,
   PyTorch or the embedding model to implement a screen. Missing dependencies are
   a setup issue, not permission to select a different stack.
6. Build vertically: Python validation/service → bridge → typed client/mock → screen
   → meaningful tests. Do not expose a production button backed only by a mock.
7. Keep slow work in the existing job infrastructure. Never run a database import,
   model call or installation in a React handler or a blocking bridge call.
8. New API names/types below are **proposed additions**, not methods already present.
   Verify existing signatures before editing. Prefer function names over line numbers,
   because the working tree may change.
9. Do not weaken validation, retention, identity, locking or cancellation rules to
   make the UI appear complete. When a necessary backend contract is missing,
   implement the task's contract first.
10. Do not run a real producer, download a model, install CUDA or replace a real
    export as a test. Use fixtures/mocks for effects; report hardware-only acceptance
    as not run if no suitable authorized environment exists.

### Task loop and work log

Create `docs/gui-react-ui-progress.md` at P00. Use one entry per task:

```text
Task: Pxx
Status: NOT STARTED | IN PROGRESS | PASS | BLOCKED
Files changed:
Backend/bridge implementation:
Frontend implementation:
Checks run and exact result:
Mocked boundaries:
Manual/native checks actually performed:
Audit IDs covered:
Open issue or next unfinished step:
```

A task is PASS only if its completion conditions pass. A mock-only browser test is
not backend integration; skipped tests are not passed tests. Record baseline failures
separately. Never label an unsupported placeholder complete. If blocked, record the
specific failure and continue tasks whose prerequisites are satisfied. Do not mark
the entire release complete while a required task is blocked.

If repository behavior contradicts this specification, first write the smallest
fixture that demonstrates the conflict. Resolve implementation details using the
specified semantics. Request design clarification only if resolving it would change
the user's approved behavior, data identity or authorized scope; do not invent a
new product contract silently.

## 2. Verified starting points and traps

| Existing file/symbol | Reuse / important limitation |
| --- | --- |
| `frontend/src/App.tsx` | Existing Library, Create, Update, ContentPanel, DatabaseSettingsPanel, MaintenancePanel, HistoryPanel, Activity, Advanced, Redundancy, Settings |
| `frontend/src/styles.css`, `content.css` | Current visual language; preserve shell and database tabs |
| `frontend/src/api/types.ts`, `client.ts`, `mockClient.ts` | Keep all three synchronized; mock must be explicitly enabled only |
| `desktop/bridge.py:ApplicationBridge` | Direct JSON envelope; allowlisted public methods; native folder picker injected |
| `desktop/window.py:create_window` | Native picker/window setup; preserve launcher and minimum-size behavior |
| `workflow/service.py:WorkflowService` | Existing orchestration, state directory, inspection and preview application |
| `workflow/catalog.py:AppCatalog` | Databases, drafts, previews, jobs/events, activation journal; additive migrations only |
| `workflow/jobs.py:JobRunner` | Existing executor and durable jobs; add operation adapters here |
| `workflow/selection.py` | Authoritative speaker and shared-document semantics |
| `workflow/planning.py`, `folder_adapter.py` | Scans, snapshots, exact planning, execution; never reimplement importer in JavaScript |
| `workflow/managed_adapter.py:ManagedAdapter` | Managed preview/execution; explicit upstream release is already supported through `source_ref.upstream_release_id` |
| `managed.py:ManagedCatalog`, `discover`, `run_managed_import` | Managed source/profile/release truth and locks |
| `deduplication.py`, `dedup_artifacts.py` | Policy resolution and validated dedup reports |
| `redundancy_cli.py`, `redundancy_jobs.py`, `redundancy_artifacts.py`, `redundancy_evaluation.py` | Existing analysis operations; validate/read their structured artifacts |
| `ui_support.py`, `ui_workers.py:CudaTorchInstallWorker` | Device semantics and installation behavior reference; do not instantiate Qt workers from Modern |

Paths in this table below `src/chroma_db_import` are abbreviated for readability.

Traps confirmed during document preparation:

* Archived listing already exists in Python service/catalog and bridge; TypeScript
  client currently omits its argument. Restore is missing.
* `get_database_content` currently reads stored `podcast.json` only. It does not
  provide a complete source inventory or all source-derived episode metrics.
* An absent episode override and an explicit empty override are different; the
  current content response can obscure this. Preserve that distinction in new DTOs.
* `get_database_content` already emits a `date`, but React does not display it.
* `start_source_action` allows only inspect/prepare. Producer Resume is unavailable.
* `_catalog_path` searches directories; `_refresh_catalog` can rediscover a different
  catalog. New context operations must use a persisted explicit connection reference.
* `import_profile_payload` and `resolve_managed_config` omit `embedding_device`.
  Saving a Qt profile does not actually guarantee device persistence. Store execution
  options separately from semantic profile identity.
* Managed preview currently resolves saved `ImportConfig`; do not assume its
  `selection_policy` DTO means modern per-episode selection is applied downstream.
* Legacy `selected_speakers=[]` does not represent an explicit modern No speakers
  policy. Introduce a versioned optional policy; never reinterpret old profiles.
* Redundancy `_release_path` needs an available export. A discovered JSON release
  manifest alone is not an analyzable release.
* Redundancy return code 2 may mean partial **or cancelled**. Inspect structured
  status as well as exit code. Preview creates no frozen assessment job/bundle.
* `export_report` currently saves under app state, not a user-selected destination.
* Current active-target helpers can fall back to another path when a pointer is
  missing/invalid. Open active folder must not silently open a stale export.

## 3. Required architecture and file changes

Do not refactor every existing component before adding features. Extract a page or
shared component when a task needs it, preserving callbacks and existing tests.

Proposed new files (create only when their task begins):

```text
frontend/src/components/
  ReportView.tsx
  SpeakerSelectionEditor.tsx
  EpisodeDetails.tsx
  FolderInspector.tsx
frontend/src/pages/
  SourceConnections.tsx
  RedundancyAnalysis.tsx
  SettingsHelp.tsx
frontend/src/help/guide.ts
src/chroma_db_import/workflow/
  inspection.py
  sources.py
  redundancy_adapter.py
  settings_transfer.py
  environment_repair.py
tests/test_gui_inspection.py
tests/test_gui_sources.py
tests/test_gui_redundancy.py
tests/test_gui_settings_transfer.py
tests/test_gui_environment_repair.py
frontend/playwright/parity.spec.ts
```

Add typed models to existing `workflow/models.py` and `api/types.ts` or a nearby
dedicated model module if necessary. Do not create empty placeholder files for the
whole list. Keep generated production assets generated; never hand-edit bundled JS.

## 4. Shared contract decisions

Implement these incrementally with the owning tasks. JSON uses existing snake_case;
TypeScript client method names use camelCase. Existing envelope remains:

```ts
type Envelope<T> =
  | { ok: true; data: T }
  | { ok: false; error: { code: string; message: string;
      field?: string | null; details_id?: string | null } };
type ContextRef = { connection_id: string; partition_id: string };
type ExecutionOptions = { embedding_device: string }; // auto, cpu, validated cuda:N
```

Keep `gui-api-v1` for backward-compatible optional additions. Add a `capabilities`
array to handshake and update its TypeScript type and mock. Gate new optional
screens on the required capabilities with actionable “Update desktop components”
text; do not silently fall back to mock data. If an existing required field's meaning
must change, use a versioned new payload instead of breaking v1 clients.

### 4.1 Data ownership and persistence

| Data | Owner |
| --- | --- |
| Database list/drafts/jobs/reports | Existing AppCatalog/state directory |
| Application defaults | New versioned AppCatalog settings row; not runtime config JSON |
| Source connection | AppCatalog stores ID, normalized root and explicit catalog reference |
| New connection's contexts/releases/profiles | Importer-owned `state_dir/managed/context_catalog.sqlite3`, using ManagedCatalog |
| Previously registered managed catalog | Preserve explicit reference; no automatic relocation or wholesale import |
| Context execution options | Versioned setting keyed by partition in its authoritative ManagedCatalog, separate from profile fingerprint |
| Semantic selection/dedup profile | Authoritative ManagedCatalog profile; changing semantic selection changes its fingerprint |
| Pilot/transfer/installer review | Immutable reviewed payload in app-owned state, with content hash and base revision |

New linking reads producer manifests and writes importer state only. Do not call a
producer-path catalog constructor merely to discover whether a file exists.
Read-only inspection must not create schema or seed policy defaults implicitly.
Use explicit initialization in Link/Save for an owned catalog. Existing catalog
mutation happens only through the selected scoped workflow, never startup discovery.

### 4.2 Inspection contracts

Extend inventory with explicit provenance and nullable unavailable values:

```ts
interface EpisodeInspection {
  episode_id: string;
  title: string;
  date: string | null;
  source_file: string | null;
  source_document_count: number | null;
  stored_document_count: number | null;
  included_document_count: number | null;
  node_counts: {
    leaf_chunk: number | null; position_card: number | null;
    cluster_summary: number | null; episode_thesis: number | null;
  };
  speakers: string[];
  override_mode: 'inherit' | 'custom';
  override_speakers: string[];
  comparison: 'new' | 'changed' | 'imported' | 'not_checked' | 'missing_source';
  checked_at: string | null;
}
interface Report {
  schema_version: 'gui-report-v1';
  report_id: string;
  kind: string;
  scope: { database_id?: string; context?: ContextRef; release_id?: string };
  generated_at: string;
  status: 'pass' | 'warnings' | 'partial' | 'failed' | 'unavailable';
  summary: Record<string, string | number | boolean | null>;
  findings: Array<{ severity: 'info' | 'warning' | 'error'; code: string;
    message: string; field?: string; path?: string }>;
  details: Record<string, unknown>;
}
```

Keep existing inventory fields until callers migrate. Response includes source
availability, stored availability and the exact policy fingerprint used for counts.
Paginate heavy inventories: `offset`, `limit` (default 100, maximum 500), `total`;
no silent truncation. Global operations use a backend scope/filter, not just loaded rows.

Context summary MUST include ref, display/type/corpus/workflow, local status,
source status with timestamp, active database status separately, release inventory,
matching registered database IDs, profile fingerprint and capabilities. A release
entry has `upstream_release_id`, resolved downstream/export identity if any,
`importable`, `analyzable`, and reasons when false. Return suggested next action as
a validated enum, with a reason; the UI chooses copy from the specification.

### 4.3 Proposed public bridge additions

All mutations validate scope on Python side. No method accepts arbitrary shell
commands. File pickers return null on cancel, and all chosen paths are revalidated
by the backend operation that consumes them.

| Method | Request → response | Owner task |
| --- | --- | --- |
| Existing `list_databases` | `{include_archived?: boolean}` → existing records | P02 |
| `restore_database` | `{database_id}` → restored record | P02 |
| `get_database_details` | `{database_id}` → Report | P03 |
| `inspect_content` | `{database_id, selection_policy?, offset?, limit?, search?}` → job with inventory/report result | P04 |
| `get_source_suggestions` | `{current_path?}` → validated labeled folders | P06 |
| `list_source_folders` | `{root, relative_path?}` → bounded immediate children | P06 |
| `get_app_defaults`, `save_app_defaults` | `{}` / `{changes, base_revision}` → versioned defaults | P07 |
| `list_source_connections` | `{}` → normalized connections | P08 |
| `link_source` | `{source_root}` → job; completion contains connection/discovery report | P08 |
| `discover_contexts` | `{connection_id}` → job | P08 |
| `list_contexts`, `get_context` | `{connection_id?, include_archived?}` / `{context}` → summaries/detail | P08 |
| `set_context_archived` | `{context, archived}` → refreshed detail | P09 |
| `open_context_folder` | `{context}` → verified opened active path | P09 |
| Existing `start_source_action` extension | `{context, action:'inspect'|'prepare'}` → job; old root/partition form remains supported | P10 |
| `save_context_defaults` | `{context, base_profile_fingerprint, profile_changes, execution_options?}` → revisions/detail | P11 |
| `preview_context_dedup`, `review_context_dedup` | `{context, upstream_release_id?}` → job/Report | P12 |
| `validate_database` | `{database_id, scope:'source'|'database'|'both'}` → job/Report | P13 |
| `get_redundancy_settings` | `{context}` → policy revision, judge config, availability | P14 |
| `save_redundancy_policy`, `save_judge_config` | `{context, changes, base_fingerprint}` → validated saved state | P14/P17 |
| `start_redundancy_action` | `{context, action, upstream_release_id?, channels?, frozen_job_id?, review_id?, artifact_id?}` → UI job | P14–P18 |
| `review_judge_pilot` | `{context, upstream_release_id, channels}` → frozen review bound to saved policy/judge/base | P17 |
| `open_redundancy_bundle` | `{path}` → validated artifact ID + Report | P18 |
| `pick_file`, `pick_save_file` | `{purpose: allowlisted enum}` → string or null | P19; introduce earlier if needed |
| `save_report_copy` | `{report_id, output_path}` → exported path | P19 |
| `export_settings` | `{scope, output_path}` → transfer summary | P19 |
| `preview_settings_import` | `{scope, input_path}` → immutable field-level proposal | P20 |
| `apply_settings_import` | `{review_id, selected_field_paths}` → atomic scoped merge result | P20 |
| `preview_environment_repair` | `{}` → frozen allowlisted installer review | P21 |
| `apply_environment_repair` | `{review_id}` → job | P21 |

For artifact-based redundancy operations, additional validated fields include label,
query/results and output paths. Define a discriminated request union by action;
reject fields that do not belong to that action. Renderer-supplied catalog paths,
process arguments or raw SQL are never part of these new APIs.

## 5. Ordered implementation tasks

### P00 — Establish baseline and progress log

**Prerequisites:** none. **Edit:** progress document only.

1. Read authority documents and inspect current Git status.
2. Confirm `.venv/Scripts/python.exe`, installed frontend dependencies and existing
   test locations. Do not install/change packages silently to fix baseline failures.
3. Run the baseline commands in section 6; record actual results and skipped tests.
4. Create progress entries P00–P23 as NOT STARTED, then mark P00 with evidence.
5. Record the 75 audit IDs and initial disposition using section 7.

**Done:** baseline is reproducible; no unrelated files or runtime data changed.

### P01 — Shared reports, typed errors and small component extraction

**Depends:** P00. **Edit:** types/client/mock, `ReportView`, models; App integration.

1. Define Report and capability fields from section 4; retain existing v1 envelope.
2. Implement ReportView with summary/findings/details, Copy, Save request and Close.
   Save can call existing state export initially; native destination arrives in P19.
3. Handle null metrics, empty findings, partial and unavailable status explicitly.
4. Reuse current CSS; no shell change. Extract only components required here.
5. Add a shared fixture serialized by Python and checked by TypeScript.

**Verify:** partial report is not styled as pass; null is not zero; Copy uses actual
report text; error/Close preserves origin; existing App tests still pass.

### P02 — Archive restoration, actual More menu and Open folder

**Depends:** P01. **Edit:** App Library/detail, client/mock, service/catalog/bridge,
new `inspection.py` active-export resolver.

1. Pass `include_archived` through TypeScript's existing listDatabases method.
2. Add restore_database: update only `archived=false`, preserve target and settings.
3. Replace ellipsis's direct archive handler with an accessible More menu.
4. Implement Active/Archived views and correct empty/no-search-results messages.
5. Add detail-header Open folder using existing bridge host opening mechanism.
6. Centralize strict active-export resolution. Validate pointer ID/path containment,
   identity and directory existence; distinguish missing from invalid. No fallback
   to previous release, no mkdir, no storage inferred from display name.

**Verify:** hide/restore leaves fixture files byte-identical; renamed display label
does not relocate; malformed/path-escaping pointer fails; folder picker/open function
is mocked in tests; selected database survives list refresh when still present.

### P03 — Database detail report

**Depends:** P02. **Edit:** inspection/service/bridge, Overview, ReportView tests.

1. Read manifest/podcast metadata from P02's verified active target.
2. Use existing metadata validators. Return all fields in design §5.2.
3. Preserve malformed/missing findings rather than returning empty healthy metadata.
4. Add Database details disclosure, Copy details and Save report.
5. Keep upstream/downstream IDs and actual resolved collection separate.

**Verify:** valid, missing manifest, malformed JSON and mismatched identity fixtures;
no source scan/model import on simple Overview. Unknown node/version fields are null.

### P04 — Source-aware episode inventory and included counts

**Depends:** P03. **Edit:** inspection/service/jobs, models, existing selection/planning.

1. Add inspect_content as a read-only job. Load source through existing loader and
   asset filter; read stored metadata through strict target resolver.
2. Join by verified stable episode identity/fingerprint, not title alone. Return
   source-only and stored-only rows, plus comparison timestamp and provenance.
3. Return complete EpisodeInspection fields, distinguishing inherit from empty override.
4. Compute included counts with `workflow.selection.select_documents`; do not count
   unchecked speaker names as a proxy for documents.
5. Add pagination/filter scope and return total matching IDs/count needed for bulk
   actions. Keep large document text on Python side.
6. On missing source, return stored inventory and source-unavailable finding; source
   metrics remain null. A selected policy is not itself proof of stored contents.

**Verify:** changed/new/imported/missing-source cases; explicit empty override vs
inherit; unattributed/shared documents; 501+ episodes pagination; source failure
does not erase stored list; newest late response cannot overwrite newer scope.

### P05 — Shared content/speaker editor and episode details

**Depends:** P04. **Edit:** SpeakerSelectionEditor, EpisodeDetails, Create/Content/
Update selection editor integration, React tests.

1. Implement the exact table of selection behaviors in design §5.3.
2. Use native `indeterminate` and accessible mixed state for global checkboxes.
3. Keep episode title action distinct from inclusion checkbox. Render nullable metrics.
4. Bulk episode actions address all filtered matches, not only the current page.
5. Per-episode All/No changes only that override; global All/No resets overrides as
   specified and preserves excluded episodes. Display reset scope before action.
6. Keep selections as a draft. Save defaults persists; Review selection passes a
   one-run policy and does not save. Preserve search/scroll/expanded episode.
7. For managed one-run review, route through P11 once available; do not claim it
   works until P11's real preview/execution tests pass.

**Verify:** keyboard use, mixed global state, filtered bulk scope, empty override,
future-speaker all-vs-allowlist semantics, and one-run review leaves saved policy intact.
**Completion note:** P05 UI can pass independently; managed behavior remains P11's gate.

### P06 — Folder inspection, suggestions and Custom in Create

**Depends:** P05. **Edit:** FolderInspector, Create, source suggestions/tree service.

1. Add Custom option to Create using existing asset_filter/asset_pattern backend
   semantics; preserve Reviewed/Cleaned/Raw/All.
2. Suggestions include valid current/recent folders and validated conventional
   processed_data locations. Do not hardcode a D: source or copy Qt parent arithmetic.
3. List immediate child directories only; resolve paths within the chosen root,
   handle permission errors and avoid following links out of scope.
4. Inspector shows scan summary, 12 sample names, exclusions and date provenance.
   Candidate navigation does not commit the folder until Use folder.
5. Add stale-scan protection when source/filter changes. Empty/zero-match state
   explains filter choices and prevents zero-eligible Create.
6. Reuse inspector from database folder settings. Changing source invalidates review.

**Verify:** cancelled picker/inspector preserves old choice; empty Custom blocked;
excluded-only folder not ready; source/filter change drops old counts; deep/large
tree stays responsive; all five filters round-trip to Python.

### P07 — Application defaults and device execution settings

**Depends:** P06. **Edit:** catalog/models/service, planning/adapters, settings panels.

1. Add versioned app-default settings storage with revision and supported output,
   filter/pattern/device fields. Additive migration preserves existing rows.
2. Add optional `execution_options` to database records/drafts/frozen previews,
   default `{embedding_device:'auto'}` for absent legacy data. Update serializers,
   SQL storage/migration, TypeScript and settings hash. Do not put it in vector identity.
3. Source device options from existing `embedding_device_options` semantics; verify
   saved explicit CUDA device at review and before launch. Auto resolves visibly.
4. Carry reviewed execution options into FolderAdapter.build_plan and managed execution,
   not an unrelated global config. Recheck preview hash on changes.
5. Defaults prefill new drafts only; saved choices and user edits win. Saving app
   defaults does not iterate over and rewrite existing databases.
6. Add New database defaults and database Import settings; contextualization/model
   remain read-only.

**Verify:** old catalog opens unchanged; default affects only new draft; saved device
reaches fake executor; device change invalidates review without changing representation
fingerprint; explicit missing CUDA errors; Auto reports CPU fallback.

### P08 — Explicit source connections and managed context inventory

**Depends:** P07. **Edit:** new sources.py, AppCatalog connection storage, ManagedAdapter,
service/jobs/bridge/types; `test_gui_sources.py`.

1. Persist opaque connection ID, normalized root, explicit catalog path and timestamps.
2. New links use importer-owned managed catalog from section 4.1. Existing registered
   catalog paths are not automatically migrated/replaced.
3. Implement Link/discover/list/get using existing discover and ManagedCatalog APIs;
   open one catalog per worker operation, close it afterward, respect locks.
4. Discovery result includes rejected candidates with path/reason and release-less
   contexts. Repeated linking of identical source/catalog is idempotent.
5. Match registered databases by explicit partition/corpus/catalog/output identity.
   Conflicting roots/identities produce a conflict, not overwrite or a guessed merge.
6. Derive importability/analyzability independently. Redundancy capability requires
   a resolved valid analysis export, not merely a release manifest.
7. New APIs resolve context through connection_id. Pass explicit catalog reference
   through adapter methods; stop re-searching a different catalog after prepare.

**Verify:** fixture with no release, two contexts, invalid manifest, conflicting ID,
duplicate root and multiple registered exports. Assert producer tree byte-identical
after Link/discovery. Startup/listing must not write producer catalog/schema.

### P09 — Source connections browser, archive and cross-navigation

**Depends:** P08. **Edit:** SourceConnections page; replace current Advanced form;
App scoped navigation state; bridge active-folder method.

1. Implement the approved two-pane browser and three local tabs without changing sidebar.
2. Search and Active/Archived filters; preserve selected ref across refresh.
3. Render Status detail from P08 data, with separate timestamps and unknown states.
4. Implement Archive/Restore via `ManagedCatalog.set_local_status`, not database archive.
5. Open active folder uses P02's strict resolver. Missing export hides the action
   or offers a specific error; no empty folder creation.
6. View source/View database/Analyze redundancy pass explicit scope. Multiple matches
   use a chooser; unmatched valid export uses prefilled Add existing.
7. Guard asynchronous response identity; switching contexts drops only the old
   transient request, not the other context's persisted drafts/jobs.

**Verify:** context with no database is usable; archived context/database independent;
deep link overrides last selection; late A response cannot populate B; no new global
Quality/Library navigation is introduced.

### P10 — Context refresh, prepare, create and review handoffs

**Depends:** P09. **Edit:** source action service/adapter/jobs, SourceConnections,
Create/Update entry callbacks.

1. Extend inspect/prepare payload to ContextRef while keeping legacy calls compatible.
2. Implement action priority in design §7.2 using returned capabilities and current jobs.
3. Refresh never publishes. Prepare calls publish explicitly, then discovers using
   the same authoritative catalog and returns the exact release ID.
4. Create/review payload uses `source_ref.upstream_release_id` from that result.
   No database invokes prefilled Create; existing database invokes current Update review.
5. Preserve prepared release on preview failure. Retry review never republishes.
6. Pending producer work shows recovery guidance; do not add a fake Resume.
7. Keep newer ready release import available when later producer processing is pending.

**Verify:** inspect has no producer mutation; prepare cannot auto-apply; release changes
between prepare and review do not substitute latest; existing retained-record blocker
is preserved; no-context/no-release/preview-failure states have recovery.

### P11 — Managed defaults and real modern selection semantics

**Depends:** P10 and P05. **Edit:** sources.py, managed.py, ManagedAdapter, profile
normalization, Source Import defaults, shared selection integration.

This is a contract task, not just form wiring. Do its steps in order.

1. Save device under a versioned context execution-options setting, separate from
   `import_profile_payload` and semantic profile fingerprint. Resolve it into the
   base ImportConfig at preview and execution; explicit one-run option wins.
2. Add an optional versioned `selection_policy` entry to managed saved profiles.
   Absent means preserve legacy `selected_speakers` behavior. Present uses the modern
   SelectionPolicy exactly, including explicit empty arrays and episode exclusions.
3. Extend `run_managed_import` with an optional keyword-only modern selection policy
   argument, default None. Use the existing resolver on verified source episodes
   before dedup/planning, in both dry-run and actual execution. Do not alter legacy
   callers when the argument is absent.
4. Include a present modern policy in the effective semantic profile fingerprint
   and downstream release derivation; do not reuse an export built with different
   selection. Preserve existing fingerprints for legacy profiles with no new field.
5. ManagedAdapter binds effective policy into preview and passes the exact same policy
   on execute. One-run policy must not save the profile or change the baseline used
   for stale-default detection.
6. Save import defaults merges only selected supported fields; optimistic fingerprint
   check prevents overwriting a concurrent save. Keep immutable representation fields.
7. Implement Source Import defaults with live included counts and explicit saved scope.

**Verify:** real synthetic managed dry-run/execution agree for default all, nonempty
allowlist, explicit empty, per-episode override and excluded episode; legacy empty
selection retains prior meaning; different policies yield different release identity;
one-run policy does not persist; device round-trip does not change semantic identity.
**Do not pass:** if frontend selections are merely echoed back without affecting records.

### P12 — Dedup settings, prospective preview and active review

**Depends:** P11. **Edit:** sources.py, service/jobs, Source Deduplication tab.

1. Expose all six existing dedup policy controls using `resolve_dedup_policy` validation.
2. Save policy with profile concurrency check; preserve other profile fields.
3. Prospective preview calls managed dry-run with pinned release and saved profile;
   it must not embed, activate, or persist edits implicitly.
4. With unsaved form changes provide Save and preview / Discard edits and preview
   saved defaults, as specified. Label the actual saved revision in the report.
5. Active review validates dedup artifacts from strict active export, then maps
   stored/suppressed/would-suppress/groups/edges/coverage into Report.
6. Render downstream compatibility requirements without claiming unverified support.

**Verify:** Off/Safe/Audit and advanced values reach resolver; invalid values do not
save; preview and active report refer to different correct snapshots; no-active
empty state; no model invocation or pointer change on prospective preview.

### P13 — Standalone validation and full import reports

**Depends:** P12. **Edit:** inspection/service/jobs, Maintenance, Update report disclosure.

1. Reuse validation/planning functions, making read-only source validation independent
   of Qt's global build_plan prerequisites.
2. Validate source/database/both; default based on available scope and remember it.
3. Extend full preview report with all G26 fields; preserve exact operation semantics.
4. Display all per-file findings with pagination, not Qt's first-ten truncation.
5. Preview import never auto-applies. Rebuild/removal continue through existing exact
   review only; do not advertise unsupported managed maintenance as available.

**Verify:** corrupt metadata, missing source, zero eligible records, warning-only and
profile mismatch fixtures; full report remains exportable; validation calls no embedding;
existing removal/rebuild tests unchanged.

### P14 — Redundancy adapter, policy and eligibility contracts

**Depends:** P13. **Edit:** redundancy_adapter.py, service/jobs/bridge/types.

1. Wrap the existing analysis implementation; do not build another analysis algorithm.
2. Extract reusable structured entry functions from CLI dispatch only where needed,
   retaining CLI output/exit behavior. Do not scrape prose or globally redirect stdout
   from a background thread as a data protocol.
3. Expose pure eligibility/resolve-export validation for release picker; preserve scope
   and hashes. Read-only settings get must not seed catalog policy via a lazy writer.
4. Save policy with fingerprint concurrency check; run channel overrides stay separate.
5. Define action union: preview / assess / resume / pilot / label_export / evaluate.
   Freeze requests and reject unrelated fields, arbitrary commands and catalog paths.
6. Connect operations to durable UI jobs with a separate `assessment_job_id` when one
   exists. Coverage preview has only a UI/report job ID, not a fabricated assessment ID.

**Verify:** settings read does not mutate; discovered-only release is not analyzable;
wrong context/export rejected; CLI regression tests pass after any extraction;
all errors use structured bridge envelopes.

### P15 — Redundancy page and coverage preview

**Depends:** P14. **Edit:** RedundancyAnalysis page; replace CLI-only placeholder.

1. Add context/release selectors, advisory note and four local tabs.
2. Default to latest analyzable release, with truthful label. Non-analyzable releases
   offer Import required/View source, not a disabled unexplained workflow.
3. Implement Preview coverage and Report, storage/retrieval settings and Save policy.
4. Keep old-scope reports scoped; switching selectors never relabels results.
5. Show scoped recent interrupted/partial job and Find frozen job by ID.

**Verify:** no release, only discovered release, valid export, scope-switch and model-
free preview fixture; ordinary page load never starts assessment/model/download.

### P16 — Assessment, cooperative cancel and frozen resume

**Depends:** P15. **Edit:** redundancy adapter/jobs/recovery, Assessment/Results/Activity.

1. Assessment passes selected channels and explicit `judge_requested=False`.
2. Preserve frozen assessment ID/spec and link it to UI job, scope and durable report.
3. Use existing cooperative cancellation support; if using cancel-file signaling,
   file is backend-generated inside app job state, never renderer-chosen. Map running
   Cancel to this signal; mark cancelled only when backend confirms.
4. Inspect structured result plus exit status: complete → succeeded; partial preserved
   bundle → succeeded_with_warnings; cancelled → cancelled; failures → failed.
5. Resume uses frozen spec. Backend's mismatch checks remain intact; policy/model/base
   changes can block resume. Show why; do not temporarily rewrite saved policy to force it.
6. Recovery after application restart reconnects or marks interrupted honestly; no
   automatic fresh assessment or duplicate judge run. Retry support must be kind-aware.

**Verify:** no channels blocked; saved judge permission cannot trigger ordinary judge;
partial vs cancelled code-2 cases; queued duplicate clicks; cooperative cancellation;
restart; resume with modified policy/base/model rejected and bundle retained.

### P17 — Judge configuration and explicit pilot review

**Depends:** P16. **Edit:** judge save/review/apply adapter, Judge pilot UI.

1. Add separate policy and connection saves with validation and fingerprint check.
2. Model field starts empty; placeholder is not a model ID. No network/model action on save.
3. Review binds selected export hashes, channels, saved policy/judge config fingerprints,
   candidate fraction, maximum calls and supported calculated upper bound.
4. Run pilot accepts review ID only, revalidates all identities and passes judge true.
5. Persist partial judge outcomes/caps and expose compatible Resume through P16.

**Verify:** unchecked/unsaved opt-in blocked; empty model blocked; save calls no model;
review changed before Run blocked; fake local judge records no more than cap; regular
Assess still makes zero judge calls; timeout produces truthful partial/failure report.

### P18 — Bundle validation, label export and evaluation

**Depends:** P17. **Edit:** redundancy adapter, Results page, native pickers if not yet added.

1. Native bundle picker → existing validator → backend-owned artifact ID/Report.
2. Validate scope before display. Different valid scope offers View on that scope;
   malformed bundle stays an error with exact reason.
3. Label export uses selected validated bundle and chosen output path; preserve the
   CLI's example-selection semantics and report selected/excluded counts.
4. Evaluation form collects required reviewed labels plus optional queries/results
   and output path. Validate with existing evaluation functions before job launch.
5. Canceling a picker leaves state intact; output collisions require native overwrite
   acknowledgment. Keep active database and source export unchanged.

**Verify:** checksum/schema/scope mismatch, missing labels, optional inputs absent,
query mismatch, cancelled picker, successful fixture evaluation and durable report.
Deep evidence browser is optional and must not delay these required routes.

### P19 — Native file transfer and scoped settings export

**Depends:** P18. **Edit:** desktop picker injection, settings_transfer.py, SettingsHelp,
ReportView Save; tests.

1. Inject native file/save pickers through ApplicationBridge like existing folder picker.
   Use purpose enums for filters; no renderer-supplied executable commands.
2. Save report copy resolves a backend-owned report ID and writes to user-picked path.
   Do not let report IDs escape app report directory.
3. Define `gui-settings-transfer-v1` with `scope_kind`, `exported_at`, identity/reference
   metadata and `settings`. One scope per file; no database content/active pointer/jobs.
4. Export app defaults, selected database selection/options, or selected context profile/
   options; optional non-secret redundancy config explicitly selected. Exclude secrets.
5. State included/excluded fields. Do not read and serialize an entire runtime config.

**Verify:** actual temp-file contents round-trip; no unrelated settings/secrets; no
path traversal via report ID; cancelled native dialogs do not write; existing report
state export still works for callers that use it.

### P20 — Reviewed settings import and legacy mapping

**Depends:** P19. **Edit:** settings_transfer/catalog transactions, SettingsHelp preview.

1. Read/validate versioned file with explicit chosen target scope. Unsupported schema
   or oversized/invalid input produces a report without mutations.
2. Normalize compatible legacy v1 UI state fields actually present. Map speaker
   fingerprints to episode IDs only when unique verified evidence exists; list
   unmatched/ambiguous entries. Reject incompatible pinned identity values.
3. Build immutable proposal: input file hash, target identity/revision, allowed field
   paths, old/new values, compatibility/reason. No free-form arbitrary field paths.
4. Apply selected fields only after re-reading file/target hashes and revalidation.
5. Commit one scope transactionally. For managed profile + execution options in the
   same catalog, use one transaction; do not call helpers that commit halfway through.
   Omitted fields remain untouched. On failure rollback the entire scoped merge.
6. Increment appropriate revisions/invalidate previews; return applied/skipped report.
7. The application does not replace top-level runtime config or partition registries.

**Verify:** omitted defaults preserved; incompatible field disabled; changed file/stale
target blocked; injected unknown path rejected; mid-apply failure rolls back; explicit
empty selection preserved; legacy ambiguous mapping does not guess; no production data.

### P21 — Reviewed environment repair workflow

**Depends:** P20. **Edit:** environment_repair.py, service/jobs/bridge, Environment UI.

1. Inspect supported launcher/Qt installer for the actual dependency action. Use
   current interpreter/environment; do not select a different Python installation.
2. Backend constructs allowlisted argument vector, environment identity, action/hash,
   expected impact and immutable review. Renderer cannot supply command text.
3. Install accepts only review ID; revalidates environment/action and job exclusivity.
4. Launch without shell string interpolation, capture progress/outcome into Activity,
   and honor current safe cancellation semantics. No surprise elevation prompts.
5. Re-run diagnosis after exit. Show installation outcome separately from CUDA usable.
6. Keep Copy repair command as fallback with accurate limitations. A missing native
   installer integration is not task PASS merely because fallback exists.

**Verify:** fake runner receives correct environment/args; no invocation before Apply;
stale review rejected; failure exit and CUDA-still-unavailable distinguished; startup
does not install. Live hardware acceptance requires separately authorized environment;
record NOT RUN if unavailable, not fabricated success.

### P22 — Guide, contextual help and accessibility pass

**Depends:** P21. **Edit:** guide data, SettingsHelp, help links, accessible controls.

1. Write all Guide articles listed in design §9 using current Modern behavior.
2. Add local search, contextual article links, Back preserving draft/origin.
3. Validate keyboard tabs/menu behavior, mixed checkbox state, field labels, focus
   restoration, live result announcements, no color-only statuses and 200% zoom.
4. Confirm current desktop minimum 960×640 remains usable; no hard-width clipping
   introduced by source split or judge/evaluation forms.

**Verify:** keyboard-only update, episode inspection, source context change, bundle
report and settings proposal; help navigation preserves unsaved values; no stale Qt
instructions about editable models or deleting during Create.

### P23 — End-to-end parity and packaged application gate

**Depends:** P00–P22. **Edit:** parity browser tests, verification/progress docs,
generated desktop assets only through approved build script.

1. Run all targeted tests and full GUI suites; distinguish fixture and native evidence.
2. Run journeys in section 6 with a real Python bridge/service against temporary fixtures.
   Browser mock journeys are an additional rendering check, not a replacement.
3. Build/package with existing script after checking its resolved cleanup targets
   stay inside C: generated directories. Never use it against the D: runtime copy.
4. Launch packaged Modern with explicit temporary state/source/output and verify
   native picker/Open folder/report save. Preserve launcher menu/Legacy fallback.
5. Check every audit ID in section 7 and record implementation/test evidence.
6. Update `docs/gui-specialist-parity.md` and verification documents only for features
   now genuinely delivered; do not mark backend-only paths as usable GUI parity.
7. Report remaining hardware/native gaps precisely. No runtime propagation or commit.

**Done:** every required task has evidence, no silent mock fallback, approved shell
preserved, no unintended files/configuration changed. If a native/hardware check
cannot run, report the limitation and do not claim full native acceptance.

## 6. Commands, fixtures and acceptance journeys

Commands are examples for the verified repository layout. Run each separately and
stop to inspect nonzero exit status; do not mask failures by running a successful
command afterward. Use the installed `.venv` Python unless the user specifies another.

### Python baseline and final regression (repository root)

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
& '.venv/Scripts/python.exe' -m unittest discover -s tests -p 'test_gui_*.py'
```

Also run affected domain suites individually: `test_asset_filters.py`,
`test_managed_contexts.py`, `test_dedup_managed.py`, `test_redundancy_cli.py`,
`test_redundancy_jobs.py`, `test_redundancy_evaluation.py`, `test_redundancy_artifacts.py`,
`test_gui_selection.py`, `test_gui_recovery.py`, and any new targeted file.
Use the same `-m unittest discover -s tests -p '<file>'` form. Avoid running live
installer/GPU workloads. Existing Qt smoke tests require offscreen mode and isolation
of default catalog/state writes; do not run them blindly against normal state.

### Frontend (working directory `frontend`)

```powershell
npm run typecheck
npm run test -- --run
npm run test:e2e
```

Run each line as a separate checked operation. Targeted Vitest tests can be used
during each task; run the full set at P23. `test:e2e` builds and uses the existing
Playwright global setup at port 4173; do not start a competing server. Tests install
a fixture bridge explicitly. Never set production to mock mode to avoid bridge errors.

### Packaged build (repository root, P23)

```powershell
& './scripts/Build-ChromaDbImportDesktop.ps1' -SkipInstall
```

This script clears generated build/assets directories. Inspect resolved paths and
existing modifications first. Native launch accepts explicit state directory through
the existing desktop entrypoint; inspect its current `--help` for syntax before use.

### Minimum fixtures

Create fixtures under `tests/fixtures/gui/parity/` and runtime copies under a fresh
test temp directory. Each test owns its state and output; no cross-test live catalog.

* Folder: new/changed/imported/stored-only episodes; two speakers; unattributed and
  shared nodes; all node metrics; invalid and excluded caches; over 500 inventory rows.
* Managed: two contexts, release-less context, explicit catalog, valid export/pointer,
  ready release with later pending work, invalid/quarantined candidate, bad pointer,
  conflicting identity and changed saved profile.
* Redundancy: valid complete/partial bundles, mismatched/corrupt bundle, frozen
  interrupted/cancelled jobs, fake bounded judge and compatible/incompatible labels/queries.
* Settings: modern files by scope, partial legacy JSON, incompatible model, unmatched
  episode fingerprint, edited-after-preview file and transaction-failure injection.

### Required journeys

| ID | Journey | Required result |
| --- | --- | --- |
| J01 | Update configured folder database | Update → Apply; exact preview/execution match; missing records retained |
| J02 | Filter episodes, clear shown, set explicit empty override, review | Only declared scope changes; included counts match backend; saved policy unchanged until Save |
| J03 | Link source with no database | Discovered context visible; prefilled Create from validated ready release |
| J04 | Prepare source then newer release appears elsewhere | Review/import uses prepared release; no auto-activation or republish on retry |
| J05 | Change context device/selection then restart | Options restored and used; semantic identity changes only for semantic selection |
| J06 | Dedup Save and preview; active review | Correct profile/snapshot and counts; no model/activation in prospective preview |
| J07 | Preview/Assess/Pilot/Cancel/Resume | Distinct operations, bounded judge, honest partial/cancelled states, frozen mismatch rejection |
| J08 | Open bundle, export labels, evaluate | Validated scope, native output, real fixture report; no active database mutation |
| J09 | Export/import settings and inject mid-merge failure | Scoped, stale-checked, all-or-nothing application; unrelated values preserved |
| J10 | Archive/restore context and database separately | Visibility only; no file deletion or cross-scope archive |
| J11 | Repair environment through fake runner | No launch before approval, reviewed environment, diagnosis after result |
| J12 | Native packaged navigation/pickers/reports at 960×640 | Responsive UI, no mock substitution, accurate paths and selectable text |

## 7. Audit traceability and completion accounting

Each range below is inclusive. All 75 audit IDs appear exactly once. Use this table
to populate the progress log, then replace task references with concrete evidence
when implemented. Preserved/excluded rows still require regression/documentation checks.

| Audit IDs | Tasks / required disposition |
| --- | --- |
| G01–G05 | P06: folder browser, suggestions, scan and zero-eligible handling |
| G06 | P07: future output defaults |
| G07–G08 | P02/P03/P23: preserve rename/identity contract; no custom identity extension |
| G09–G10 | P06: all five filters and scan invalidation |
| G11 | P03/P07: pinned representation/read-only details |
| G12 | P07/P11: actual saved device execution behavior |
| G13 | P03/P07: revision/contextualization read-only; editing excluded |
| G14–G15 | P04/P05: inventory/counts/global selection |
| G16 | P19/P20: scoped transfer; legacy limitations explicit |
| G17–G18 | P21: diagnosis and reviewed repair |
| G19–G20 | P22: contextual help and Guide |
| G21 | P01/P16/P23: durable progress/log regression |
| G22–G25 | P13/P23: retain safer Create/Update/rebuild/exact removal; hidden mirror excluded |
| G26–G27 | P13: full preview and standalone validation |
| G28 | P03: metadata/collection details |
| G29 | P02: verified direct folder opening |
| E01–E05 | P04/P05: comparison, preserved state and all metrics |
| E06–E07 | P05/P11: global/episode selection and shared content semantics |
| E08 | P01/P16/P23: outcome totals, warnings and copyable failure |
| M01–M09 | P08/P09: connection/browser/status/identity details |
| M10–M12 | P10: prepare/review; producer Resume explicitly excluded |
| M13 | P11: scoped semantic defaults and execution options |
| M14–M16 | P09: independent archive/restore, active folder, analysis link |
| M17–M20 | P12: dedup policies, compatibility and both reports |
| R01–R03 | P14/P15: analyzable scope and saved policy |
| R04–R06 | P17: separate judge permission/configuration and saves |
| R07–R09 | P15/P16: channels, model-free preview and nonjudge assessment |
| R10 | P17: saved opt-in and explicit bounded pilot |
| R11–R13 | P16: frozen resume, cancellation and partial coverage |
| R14–R16 | P18/P19: bundle, label export and evaluation |
| R17–R18 | P01/P16/P23: durable advisory outcomes, no activation/deletion |

### Final worker response template

Report completed tasks, visible changes, exact test results and native/hardware
limitations; link changed design/progress/verification documents. State that work
remains in C:. Do not claim runtime deployment, D: propagation, commits or complete
Qt parity unless actually performed/verified and within the user's authorization.
