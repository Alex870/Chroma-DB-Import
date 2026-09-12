# GUI overhaul implementation plan

Date: 2026-09-07  
Design authority: [GUI-DESIGN.md](GUI-DESIGN.md)  
Status: Ready for sequential implementation. No implementation tasks have been completed by creating this plan.

## 1. Instructions to the implementing worker

Implement the database manager described in the design. Follow the tasks below in order. Finish one task and record its evidence before starting the next. A working screen with fake data does not count as an implemented workflow.

This document deliberately chooses the main implementation approach so you do not need to invent one: **React + TypeScript, compiled with Vite, hosted locally by pywebview, calling the existing Python backend through a narrow direct bridge.** Task 02 verifies that approach on Windows before substantial frontend work. Do not silently switch frameworks if that gate fails. Continue independent Python tasks and report the exact shell blocker.

### Repository and runtime rules

1. Read `AGENTS.md` before editing. The only source repository is `C:\temp\codex\Chroma DB Import`.
2. Record `git status --short` at the start. There are already unrelated modifications. Do not reset, discard, reformat, stage, or commit those changes as part of this work.
3. Use temporary synthetic data under `.test_tmp/gui/` for imports. Never test replacement, deletion, migration, or recovery against the user's real databases.
4. Do not copy anything to `D:\Pod Cast RAG\Chroma DB Import` without the user's propagation decision. Ask whether the completed changes should be copied and whether later changes in the same session should propagate automatically. Honor an answer already given in that session.
5. Do not overwrite D: configuration. A required runtime configuration change needs consultation and a field-aware merge preserving unrelated values, even if code propagation was authorized.
6. Do not automatically commit, push, install system software, run a real producer workload, or switch the user's runtime application just because a task is complete. Make the code and test evidence reviewable first.

All file paths below are relative to the canonical repository. Paths marked **new** are proposed outputs, not existing APIs. Verify existing function signatures before using them.

### Work log and stop rules

Create `docs/gui-implementation-progress.md` in Task 01. For every task record:

```text
Task: 01
Status: NOT STARTED | IN PROGRESS | PASS | BLOCKED
Files changed:
Checks run and actual results:
Manual checks and actual observations:
Unresolved issue, if any:
Next task:
```

Never mark PASS because code looks plausible. Do not turn off a failing assertion or weaken an existing data contract to pass a gate. Distinguish baseline failures from new failures. A missing dependency, skipped test, mock-only test, or unavailable GPU is not a successful real integration test.

If a task becomes too large, finish its numbered steps in order and record the next unfinished step. Do not replace the remaining implementation with TODO buttons. Ask for help when repository behavior contradicts this plan and resolving it would change data semantics; include the smallest failing fixture and the two conflicting requirements.

## 2. Fixed scope and architecture

Deliver these user paths:

- Database library, Add existing database, and automatic persistence.
- Three-step creation for processed folders and managed Podcast-RAG sources.
- Update → review → Apply update, using remembered database settings.
- Durable activity, useful errors, and honest interruption/recovery behavior.
- Content selection, source readiness, maintenance, redundancy analysis, history, settings, and diagnostics in their designated areas.
- Numbered PowerShell menu with existing action values preserved and a usable legacy GUI fallback.

Do not implement cloud hosting, authentication accounts, multi-user remote access, audio preprocessing, a new embedding algorithm, automatic model migration, or bulk deletion of database folders. Do not build a second importer in TypeScript.

```text
React screens
    |
Typed bridge client (JSON requests/results)
    |
Desktop bridge (allowlisted methods; no arbitrary commands)
    |
Workflow service ---- application catalog / previews / jobs
    |
Folder adapter       Managed-source adapter
    |                    |
Existing export, importer, representation, staging,
managed catalog, producer adapter, and release contracts
```

Decisions:

| Concern | Use |
| --- | --- |
| Frontend | React, TypeScript strict mode, Vite; npm lockfile |
| Initial styling | Plain CSS with reusable controls; no additional UI framework selection task |
| Frontend tests | Vitest and React Testing Library; small Playwright browser journeys with a test bridge |
| Desktop | pywebview; local compiled assets; native folder picker |
| Transport | Direct Python/JavaScript bridge; no separate REST service |
| Long operations | Python job service with one execution worker initially; UI calls return job IDs promptly |
| Progress delivery | Poll job changes at approximately one second while running; stop polling on completion/unmount |
| App state | New SQLite catalog under a configurable application state directory |
| Managed state | Existing `ManagedCatalog` remains authoritative for managed profiles/releases |
| Initial cancellation | Waiting jobs may be cancelled; active imports offer Wait until safe cooperative cancellation has been implemented and verified |
| Frontend development | Mock bridge only in explicit development/test mode; never silently substituted in production |

Read current official documentation while pinning dependencies; do not guess versions from this document. React can build the web interface, and pywebview provides the desktop bridge and native window. Its API exposes Python methods as asynchronous JavaScript calls and requires its GUI loop on the main thread. See [React integration](https://react.dev/learn/add-react-to-an-existing-project), [Vite setup](https://vite.dev/guide/), and [pywebview API](https://pywebview.flowrl.com/api/). Select compatible stable versions, record the Python/Node versions, and lock the actual resolved dependencies in Task 02.

## 3. File layout and reuse map

Create this layout incrementally; do not generate empty placeholders for the whole tree:

```text
src/chroma_db_import/
  workflow/                         # new, no Qt or webview dependencies
    __init__.py
    models.py                       # catalog, selection, preview, job DTOs
    catalog.py                      # app catalog and migrations
    selection.py                    # content policy resolution
    service.py                      # task-oriented application methods
    folder_adapter.py               # processed-folder workflow
    managed_adapter.py              # managed workflow
    planning.py                     # common preview envelope and checks
    jobs.py                         # queue, execution, event persistence
    recovery.py                     # interrupted jobs and activation journal
  desktop/                          # new, host-specific code
    __init__.py
    __main__.py
    bridge.py
    window.py
    assets/                         # generated production frontend bundle
frontend/                           # new
  package.json
  package-lock.json
  index.html
  src/
    main.tsx
    App.tsx
    api/{types,client,mockClient}.ts
    components/
    pages/
    styles.css
  tests/
scripts/Build-ChromaDbImportDesktop.ps1  # new
tests/test_gui_*.py                     # new backend checks
tests/fixtures/gui/                     # new synthetic inputs and expectations
docs/gui-implementation-progress.md     # new
docs/gui-verification.md                # new, actual evidence only
```

Existing modules to reuse:

| Existing file | Use it for | Do not do |
| --- | --- | --- |
| `ui_loader.py`, `asset_filters.py` | Processed-cache loading and transcript variant filtering | Create a different filename filter in React |
| `ui_models.py`, `ui_helpers.py` | Existing episode/export models and speaker semantics | Import the main Qt window into the workflow package |
| `ui_export.py` | Export planning, metadata, embedding cache integration, staging | Call the existing function blindly and assume preview counts are exact |
| `reconciliation.py` | Record comparisons and explicit removal contract | Treat episode counts as record counts |
| `representation.py`, `providers.py` | Pinned identity, dimensions, profile, device readiness | Replace a legacy identity with today's default |
| `managed.py`, `managed_lock.py` | Partition isolation, managed catalog/import, locks | Duplicate managed profile/release truth in the app catalog |
| `podcast_rag_adapter.py` | Inspect, resume pending producer work, publish release | Hide producer mutation inside Refresh |
| `release_cli.py`, `releases.py` | Existing release plans and approval requirements | Treat any snapshot as a valid rollback target |
| `redundancy_cli.py`, `redundancy_jobs.py` | Existing analysis operations | Treat advisory assessments as deletion approval |
| `ui_window.py`, `ui_workers.py` | Behavioral reference and legacy entry point | Instantiate widgets to obtain configuration or call a business operation |

## 4. Contracts to implement before screens

Use dataclasses and explicit validation in Python. Serialize only JSON-compatible primitives. Mirror public DTOs in TypeScript and test a shared example payload to detect drift. Internal document contents may remain in Python; do not send all document text to the library screen.

### Database record

Required fields:

| Field | Meaning |
| --- | --- |
| `id` | App-generated stable UUID; not a substitute for an existing downstream database ID |
| `display_name` | Editable library label; never used to recalculate existing storage |
| `source_kind` | `folder` or `managed` |
| `source_ref` | Folder path, or managed source root + verified partition/corpus identity |
| `target` | Folder: fixed resolved export directory; managed: fixed partition root and reference to active pointer |
| `downstream_identity` | Actual recorded database ID/collection/representation; null only for a draft/unresolved registration |
| `selection_policy` | Saved policy or reference to the authoritative managed policy |
| `settings_revision` | Increments when import-affecting settings change |
| `archived` | Hides entry; does not delete data |
| `last_check` | Timestamp, source snapshot and check result, or null |

For managed entries, compute the current active export from the validated pointer; do not pin an old release path as the permanent update target. Store managed profile references rather than a competing editable copy. Keep the original downstream identity separate from the library label.

### Selection policy

Use `all` or `allowlist` speaker mode, excluded speakers, per-episode overrides, asset filter/pattern, and explicit excluded episode IDs. For `all`, new speakers are included unless excluded. For `allowlist`, new speakers are excluded until selected. Resolve this policy in Python for each preview.

Use source/partition identity plus stable episode identity for overrides. Do not key new saved choices only by a mutable content fingerprint or display name. Legacy selections that cannot be mapped uniquely become reviewable unresolved choices; do not silently discard them.

### Frozen preview

Required fields: `schema_version`, `preview_id`, `database_id` or `draft_id`, `operation`, `created_at`, settings revision/hash, source snapshot/hash, target identity/fingerprint, resolved selection, representation identity, validation findings, planned effects, and required acknowledgments.

Operations: `create`, `update`, `rebuild`, `remove_outdated`. Source preparation and analysis have separate job types.

Planned effects contain **separate** episode totals and record totals. Record sets include insert, replace, metadata-only, unchanged, retained-missing, and explicit delete. Include backend-generated reasons and exact IDs in a stored artifact; the bridge can page large lists. Managed full-version builds must additionally disclose any records present in the active version but absent in the prospective version.

Hash canonical serialized input/effect content. Keep a random ID for lookup; a timestamp is not an identity proof. Store the full preview server-side. Apply accepts a preview ID and acknowledgments; it must not trust client-supplied counts, paths, or replacement plans.

### Job and result

Job states: `queued`, `running`, `succeeded`, `succeeded_with_warnings`, `failed`, `interrupted`, `cancelled`. Store the current stage separately. Stage names: checking, preparing, embedding, validating, activating, complete. Store an increasing event sequence for polling.

Results include actual writes, retained records, warnings, output/version, report path, and `active_database_state`: `unchanged`, `new_version_active`, `unavailable`, or `unknown`. Never map every exception to “No changes were made.”

### Bridge envelope

```json
{
  "ok": false,
  "error": {
    "code": "PREVIEW_STALE",
    "message": "The source changed after this preview. Review the latest changes.",
    "field": null,
    "details_id": "diagnostic-reference"
  }
}
```

Successful calls return `{ "ok": true, "data": ... }`. Use stable error codes: `SOURCE_UNAVAILABLE`, `SOURCE_INVALID`, `TARGET_EXISTS`, `IDENTITY_UNRESOLVED`, `PROFILE_MISMATCH`, `PREVIEW_STALE`, `TARGET_BUSY`, `VALIDATION_FAILED`, `OPERATION_UNSUPPORTED`, `JOB_FAILED`. Do not expose raw tracebacks as the primary message.

## 5. Ordered implementation tasks

### Task 01 — Establish baseline and fixtures

**Depends on:** nothing.  
**Files:** progress/verification documents, `tests/fixtures/gui/`, test helper module.

1. Read the design, local rules, reuse-map modules, and existing UI/update tests.
2. Record current Git status and the available Python/Conda environment. Reuse the project's environment; do not reinstall CUDA to run unit tests.
3. Run the existing baseline suite using the commands in section 6. Save failures/skips in the work log before editing behavior.
4. Build small synthetic processed-cache fixtures using the repository's contract fixture conventions. Include two episodes, two speakers, shared context records, and valid identity fields. Use existing fake providers as a pattern; do not download a large embedding model.
5. Add fixture variants: new episode; same ID with changed text; metadata-only change; expanded speaker selection; omitted episode; mismatched profile; missing metadata; mixed partitions; excluded filename variant.
6. Keep a hand-written expected effect list for each variant. Generate actual IDs using the real identity helper where necessary, but do not generate expected effects by calling the planner under test.

**PASS:** baseline is recorded; fixtures validate; expected scenarios are documented. Existing unrelated failures are identified rather than silently counted as success.

### Task 02 — Prove the desktop shell

**Depends on:** 01.  
**Files:** `frontend/`, `desktop/window.py`, `desktop/__main__.py`, desktop dependency pins, build script.

1. Create a minimal React/TypeScript Vite project. Add `typecheck`, `test`, and `build` scripts. Pin tested dependency versions and commit-ready lockfile content; do not change importer dependency versions.
2. Add pywebview in a separate exact-pinned desktop requirements file, leaving the legacy requirements usable. Record compatibility with the current Python environment.
3. Implement a window with a native folder picker, a JSON echo request, a synthetic ten-second progress job, and a readable startup failure page/message.
4. Use compiled local assets and a direct bridge. Wait for bridge readiness before requests. Never load external pages in the privileged application window.
5. Test from a directory containing spaces, with non-ASCII folder names, at 100% and 150% Windows scaling. Confirm no terminal window appears for hidden helpers.
6. Close the window during the synthetic job and verify the chosen close interception can keep it open to finish. Verify a missing frontend bundle reports a build/setup remedy.
7. Save the observed WebView runtime, dependency versions, and checks in `docs/gui-verification.md`.

**PASS:** the actual Windows webview loads the production bundle, round-trips JSON, picks a folder, stays responsive, and handles close/startup failure. Browser-only testing is insufficient. If this gate fails, do not switch launcher defaults; Tasks 03–09 may proceed independently.

### Task 03 — Add models and application catalog

**Depends on:** 01.  
**Files:** `workflow/models.py`, `workflow/catalog.py`, `tests/test_gui_catalog.py`.

1. Implement section 4 models and validation. No Qt/webview imports.
2. Add a versioned SQLite catalog at `<state_dir>/gui_catalog.sqlite3`, with databases, drafts, previews, jobs, and job-events tables. The default state directory may be the project's existing `state`; tests must inject temporary paths.
3. Implement transactional schema migration and reject an unknown newer schema with a useful error. Do not edit the existing managed schema in this task.
4. Add list/get/create/update/archive operations. Enforce unique normalized target identity. Resolve Windows case/path aliases; reject unresolved aliases rather than creating two writers to one database.
5. Use one SQLite connection per owning thread/operation; do not pass a UI-created connection into a worker.
6. Keep draft creation separate from a successful database registration. Persist timestamps in UTC.

**PASS:** restart preserves data; rename leaves target/identity unchanged; duplicate-target registration is rejected; archive leaves fixture files untouched; migration is idempotent; invalid payloads fail clearly.

### Task 04 — Register existing databases and migrate saved selections

**Depends on:** 03.  
**Files:** `workflow/service.py`, both adapters, `workflow/selection.py`, `tests/test_gui_registration.py`.

1. Add read-only export inspection using existing metadata/manifest helpers. Read the actual profile, collection, and identity; do not infer them solely from a folder name.
2. For managed entries, verify partition identity and active pointer through managed contracts. Do not call `adopt_legacy` as a side effect of ordinary registration.
3. Return a proposed registration with source details missing where not recoverable. Ask the UI to collect a missing source path; do not guess one.
4. Mark unreadable, contradictory, or incomplete identity as unresolved. Such entries can be viewed but cannot update until repaired or explicitly resolved.
5. Read `state/ui_state.json` and managed contexts as migration candidates. Show candidates for acceptance; preserve original files. Migration must be repeatable without duplicates.
6. Translate legacy speaker choices to the new selection policy where unambiguous. Preserve reviewed-transcript defaults for new databases and saved settings for existing ones.

**PASS:** BGE registration retains BGE; Qwen3 registration keeps its actual subdirectory; managed entries follow the active pointer; renames preserve destinations; ambiguous metadata blocks import; original state/config files are unchanged.

### Task 05 — Separate final destinations from staging destinations

**Depends on:** 04.  
**Files:** `workflow/folder_adapter.py`, narrow changes in `ui_models.py`/`ui_export.py`, `tests/test_gui_targets.py`.

1. Trace every read/write of `ImportPlan.export_dir` and every staging path in `export_chroma`.
2. Introduce an explicit folder-export destination input for the new workflow while preserving legacy callers. Capture the final destination once; construct the staging destination separately.
3. Do **not** simply add a fixed `export_dir` override that survives `replace(plan, output_root=staging_root)`. That would point staging writes at the active database. Refactor destination passing so staging always receives its own explicit path.
4. Place staging/backup paths next to the intended target on the same volume, with unique operation IDs and validated ownership. Preserve existing profile-specific storage and artifact contracts.
5. Creation fails if the final destination exists. Check again under the writer lock just before activation; a preview-time check alone is insufficient.
6. Keep folder target resolution separate from managed partition/release resolution.

**PASS:** changing the display name never changes a registered target; staging writes never touch the active target before activation; create cannot replace an existing folder even if another process creates it after preview; legacy target tests still pass.

### Task 06 — Make folder previews and execution agree

**Depends on:** 05.  
**Files:** `workflow/planning.py`, folder adapter, focused extraction in `ui_export.py`, `tests/test_gui_folder_planning.py`.

1. Extract deterministic document preparation/identity/fingerprint work shared by preview and execution. Preserve graph pruning, topic records, selection, representation, and metadata rules.
2. Inspect the existing collection without creating a missing collection and without loading an embedding model. Inventory all relevant existing records, including records from episodes completely absent from current input.
3. Build exact insert/replace/metadata-only/unchanged/retained-missing sets from existing reconciliation helpers. Distinguish detected episode changes from actual record writes. Include any generated topic/profile records in actual write totals.
4. Default update retains existing IDs absent from selected input. Same-ID changed text replaces that ID; new IDs insert; old IDs absent after rechunking remain listed as retained unless explicitly removed. Do not claim all old content vanished after a correction.
5. Make execution consume the same prepared plan, rather than recomputing a narrower episode loop that skips planned work. Metadata-only changes must not load/embed text unnecessarily.
6. Ensure final manifest and podcast metadata describe the actual resulting collection, including retained content. Do not report only newly selected input as total stored content.
7. Freeze source inputs for execution: capture a validated immutable snapshot or copy selected files into job-owned staging and verify fingerprints against the accepted preview. Detect source changes during capture. Never continue reading mutable originals midway through embedding.
8. Store the accepted preview. On Apply, under the writer lock, recheck target identity/content state, selection/settings, and source snapshot before writes. No differences returns a no-op without model loading.

**PASS:** fixture expectations match planned IDs and actual stored IDs/content/metadata; source mutation after preview fails with `PREVIEW_STALE`; omitted episodes are retained; metadata-only and no-op paths make zero embedding calls. Include a tiny real-Chroma test with a fake embedding provider, not only a mocked collection.

### Task 07 — Adapt managed sources without weakening their contracts

**Depends on:** 03, 04, 06.  
**Files:** managed adapter, service, targeted managed extensions if needed, `tests/test_gui_managed_workflow.py`.

1. Wrap discovery/status inspection, exact-release dry run, and import separately. Reuse `ManagedCatalog`, `run_managed_import`, and `PodcastRagSourceAdapter`.
2. Read profiles from `ManagedCatalog`; record the effective profile fingerprint in the preview. Do not let a stale global form override it.
3. Pin the exact upstream release ID and input fingerprints. Execution must never fall back to “latest” after a preview was accepted.
4. Represent source readiness separately from database health. Status checking must not call `resume_pending` or `publish_release`.
5. Add explicit source jobs for those mutations. Completing a publish job creates a reviewable database preview, not automatic activation.
6. Inspect the prospective stored record IDs after deduplication and compare them with the active version. A full managed-version replacement may remove records even if it never calls a collection delete method.
7. Preserve the design's normal-update retention rule. If the existing managed operation cannot retain missing active records with valid provenance/contracts, block normal Apply for that case and return `OPERATION_UNSUPPORTED` with a link to an explicit replacement/removal review. Never label a dropping full-version build as a retaining update. Record this limitation for review; do not invent producer release contents or silently merge incompatible releases.
8. Ensure supported managed creation/updates without those removals work end to end. Keep partition isolation, quarantine, validation, locks, dedup evidence, and activation contracts intact.

**PASS:** exact release/profile executes; a newer arriving release invalidates or remains separate from the pinned preview; mixed partitions fail; Refresh has no producer side effects; any loss of active records is visible and cannot occur through ordinary retaining Update.

### Task 08 — Durable jobs, locks, and activation recovery

**Depends on:** 06, 07.  
**Files:** jobs/recovery modules, targeted export promotion code, `tests/test_gui_jobs.py`, `tests/test_gui_recovery.py`.

1. Add a serial worker queue. Persist accepted job+preview in a transaction before starting. Start calls return promptly. Duplicate Apply requests for the same preview return the existing job rather than creating another import.
2. Use OS-backed target locks. Folder locks must live at a stable location outside renamed export directories. Managed imports keep their partition lock; avoid acquiring the same non-reentrant lock twice.
3. Make the locked boundary cover accepted-plan validation through activation for every writer to that target, including supported legacy/CLI paths. Test exclusion with separate processes, not just threads. If a writer cannot cooperate, detect it or block conflicting operation paths; do not claim complete locking.
4. Persist progress stage, bounded event logs, and final outcomes. Keep source/analysis jobs distinct from import jobs.
5. For folder activation, add a durable journal recording target, owned stage, backup, operation ID, and transition state. Validate staged output before replacement; record intent before filesystem moves; mark completion only after the new target is verified.
6. Keep the old export recoverable until completion is recorded. Recovery must distinguish old-target-present, target-moved-to-backup, new-target-present, and ambiguous states. Never delete the only known good copy.
7. On restart mark orphaned running jobs interrupted, reconcile journal evidence, and show actual active state. Do not automatically resume embedding from an unverified checkpoint.
8. Implement Cancel for queued jobs. For active import, expose `can_cancel=false` until a tested cooperative safe-boundary mechanism exists. Window close must wait instead of killing an active importer. Offer Retry after failure with a fresh preview and cache reuse where supported.

**PASS:** double Apply creates one job; cross-process conflicting writes fail; navigation/polling cannot terminate work; injected failures before/after each activation move recover or clearly report ambiguity; no recovery deletes arbitrary paths; final totals survive restart.

### Task 09 — Expose the typed application bridge

**Depends on:** 02, 08.  
**Files:** desktop bridge, service, frontend API types/client, `tests/test_gui_bridge.py`.

Implement these method groups as explicit methods. Names are the new API contract:

| Method | Result/behavior |
| --- | --- |
| `list_databases`, `get_database`, `list_jobs`, `get_job_events` | Read-only, paginated where appropriate |
| `pick_folder` | Native picker; cancellation returns null |
| `inspect_existing`, `register_existing` | Read proposal, then accept registration |
| `save_draft`, `get_draft` | Persist incomplete creation/settings choices |
| `scan_source`, `check_database`, `create_preview` | Queue expensive read-only work and return job ID |
| `get_preview` | Return preview summary and paged effect details |
| `apply_preview` | Validate accepted ID/acknowledgments, create/reuse execution job |
| `start_source_action` | Allowlisted inspect/resume/prepare action with explicit user choice |
| `archive_database`, `rename_database` | Catalog operations only |
| `open_database_folder`, `export_report` | Resolve known database/report ID server-side |

1. Validate every payload at the Python boundary, including enum values and ID existence.
2. Do not accept arbitrary Python, executable names, shell command strings, or arbitrary deletion paths. Producer commands use existing argument-list APIs.
3. Limit the bridge object to public operations; do not expose internal service objects. External help links open outside the privileged window.
4. Use the standard error envelope and copyable diagnostics. Production without a bridge must show a connection error, never fixture data.
5. Include version handshake and a clear mismatch message when stale frontend assets meet a newer backend.

**PASS:** malformed inputs fail safely; slow scans/imports return job IDs and leave UI responsive; API DTO examples pass in Python and TypeScript; production missing-bridge behavior is visible.

### Task 10 — Build navigation, library, and activity

**Depends on:** 09.  
**Files:** App, common controls, LibraryPage, DatabasePage, ActivityPage, frontend tests.

1. Build navigation exactly as the design: Databases, Activity, Advanced tools, Settings & help. Use Overview/Content/History/Settings inside a selected database.
2. Implement first-run empty state, searchable database table, Create database, Add existing, and read-only technical details.
3. Show unknown/not checked states with timestamps; do not infer “Up to date” from a prior successful import.
4. Add the persistent active-job strip and detailed activity page with expandable logs, actual counts, and warnings.
5. Implement a reusable loading/error/empty state, labeled fields, status badge with text, and primary-action footer. Use semantic buttons/tables/forms and visible keyboard focus.
6. Wire Add existing through inspection and registration. Wire Archive with the text “Hide from this library; database files remain.”

**PASS:** restart restores entries; archive does not delete files; a running job remains visible after navigation; no global toolbar mixes maintenance/analysis into the primary actions.

### Task 11 — Implement the three-step creation flow

**Depends on:** 10.  
**Files:** CreateDatabasePage and its three step components, selection panel, frontend tests.

1. Step 1: source kind + picker/connection, scan results, eligible/excluded counts, explicit asset filter, optional speaker/episode selection.
2. Step 2: name + suggested storage, resolved target path, profile/device readiness. Keep technical fields collapsed. Generate IDs in Python.
3. Step 3: backend preview, blocking remedies, warnings, final Create database action. This is the preview/validation step; no separate Dry Run requirement.
4. Preserve the draft when moving Back and when restarting. Prevent stale asynchronous scan results from overwriting a newer source selection by tracking request/draft revisions.
5. If destination exists, offer Use existing or Choose another location. Never show overwrite in creation.
6. On Apply, navigate to the job. On success, show actual output and View database/Open database folder actions.

**PASS:** complete both folder and managed-ready-source creation using the real service; a normal user can leave advanced fields closed; Back preserves input; mismatched response revisions are ignored; existing output remains untouched.

### Task 12 — Implement the returning-user update flow

**Depends on:** 11.  
**Files:** UpdateDatabasePage, PreviewSummary/EffectList components, frontend tests.

1. Library Update loads the registered database and queues the check/preview without asking for remembered fields.
2. Display source, exact target, profile compatibility, episode counts, actual record effects, retained records, and validation findings.
3. Up-to-date preview has no Apply button and creates no import job. Changes offer one Apply update action; do not add a second generic confirmation dialog.
4. Changing content selection edits a draft and requests a new preview. Saving it as the database policy is explicit; one-run changes must not silently become permanent settings.
5. Render stale preview, unavailable source, unresolved identity, and profile mismatch with the prescribed next actions. A new model offers a separate database flow.
6. Source-not-ready state links to the explicit source workflow. A newly published release returns here for review.

**PASS:** ready updates require Update then Apply only; selection/target/profile are remembered; no-op embeds nothing; changed/retained records match actual outcome; source changes after review cannot apply silently.

### Task 13 — Content, settings, and source connections

**Depends on:** 12.  
**Files:** ContentPage, DatabaseSettingsPage, SourceConnectionsPage, EnvironmentPage; service extensions.

1. Add searchable episode/speaker tables with global selection, explicit per-episode overrides, and shared-context explanation.
2. Show setting scope: application default, this database, or this preview. Increment revisions for import-affecting changes.
3. Lock existing representation identity for in-place updates. Device changes may affect speed; model/revision changes create a separate representation.
4. Add source connection discovery/status, with pending/failed/quarantined counts and separate active-database health.
5. Add explicit Resume source processing and Prepare source release actions; use supported producer adapter methods and structured arguments. Review precedes their mutation; successful prepare leads to import review.
6. Add environment diagnosis and an explicit repair action using supported launcher behavior. Never install CUDA on page load. Settings import shows a field-level proposal before changes; preserve unrelated operational fields.

**PASS:** global and episode selection persist predictably; newly discovered speakers follow saved policy; inspecting a source never resumes/publishes it; existing database settings do not drift when another entry is selected.

### Task 14 — Maintenance and version history

**Depends on:** 13.  
**Files:** MaintenancePage, HistoryPage, planning/service extensions, focused tests.

1. Rebuild is available only from Maintenance. Show current target, prospective profile, full work scope, and recovery behavior. Use staging and an operation-bound accepted preview.
2. Remove outdated records displays exact IDs/episodes and reasons. Require explicit acknowledgment tied to that preview. Ignore/reject sticky legacy removal flags on normal Update.
3. Inventory removals across the whole database, including absent episodes. Do not assume the current per-selected-episode loop covers them.
4. For managed replacements, show active-versus-prospective membership changes and preserve release approval/validation rules. Unsupported retention remains clearly blocked on routine Update.
5. Show version/history records with active state and source release separately. Enable promote/rollback/prune only for adapters with existing verifiable contracts and exact-plan approval support.
6. Where an advanced operation remains CLI-only, provide accurate copyable instructions and explicitly label it CLI-only. Do not add a button that claims completion without performing it.

**PASS:** routine update cannot delete missing IDs; explicit maintenance deletes precisely accepted IDs; stale deletion approval fails; failed replacement preserves/reports the recoverable old version; unsupported rollback has no active action.

### Task 15 — Redundancy analysis and specialist parity

**Depends on:** 14.  
**Files:** RedundancyPage/stage components, advanced bridge methods, analysis tests.

1. Select a database and freeze a supported database version before analysis.
2. Implement Preview coverage → Assess → Optional judge pilot → Review/export. Put policy and candidate channels in Assess, judge settings in the optional stage, and labels/evaluation with results.
3. Reuse current job/bundle APIs. Keep exact deduplication review under Content and semantic redundancy under Advanced tools.
4. Preserve current cancellation behavior only where supported; validate output statuses including partial completion.
5. Add standalone validation and report export with an explicit source/database target.
6. Compare the old GUI action inventory with the design's function-placement table. Record each operation as implemented, intentionally CLI-only, or still missing. Missing existing GUI workflows block default cutover.

**PASS:** analysis is advisory and leaves active export identity/content unchanged; output is version-bound; common imports never require analysis; every existing GUI operation has a usable new location or an explicit reviewed disposition.

### Task 16 — Package, verify, and switch the launcher

**Depends on:** 02–15 all passing, with any documented limitations reviewed.  
**Files:** build script, desktop assets packaging, `pyproject.toml`, launchers, `.gitignore`, README, verification document.

1. Build the frontend into `desktop/assets` using the build script. Use relative asset URLs. Include assets in Python package data and verify them in a built wheel, not just a source checkout.
2. Ignore generated assets/node_modules/test outputs as appropriate, but keep the frontend lockfile. Do not accidentally ignore frontend source through a broad existing pattern.
3. Normal startup loads already-built assets. It must not run npm, fetch dependencies, or require a development server. Setup/build instructions install locked dependencies and build once. Missing assets get a clear setup message.
4. Extend the UI launcher with an explicit implementation selector such as `-Ui Legacy|Modern`. Preserve `Workspace Default|Contexts`, `NoLaunch`, install flags, root action values, and exit-code handling.
5. Initially keep Legacy as default while testing Modern. After the final gate, make option 2 open the library and option 5 open Source connections in Modern. Preserve documented explicit Legacy launch.
6. Update setup/repair to include modern dependencies and asset preparation without changing existing CUDA/version choices unnecessarily. Keep options 1/3/4 and Q working.
7. Run all section 6 checks, including actual Windows desktop checks. Record remaining limits candidly. Do not mark all tests passed if GUI/GPU checks were skipped.
8. Ask about copying completed changes to the runtime and session propagation according to `AGENTS.md`; do not overwrite runtime config.

**PASS:** a clean supported environment launches the packaged UI without Node at runtime; original menu behavior and legacy fallback work; all common flows pass actual backend integration; no unreviewed data-semantic or recovery gap is hidden by the cutover.

## 6. Verification commands and manual script

These commands are instructions for the implementing worker. They have not been run as part of writing this plan. Run from the canonical repository, using the actual project environment. If Conda is unavailable, document the selected equivalent interpreter before continuing.

### Baseline and regression

```powershell
conda run --no-capture-output -n chroma-db-import python -m unittest discover -s tests -v
```

For headless Qt test runs only, set `QT_QPA_PLATFORM=offscreen` for that process/session and restore its previous value afterward. Do not interpret headless checks as proof of real desktop rendering.

Run focused new backend checks after their tasks:

```powershell
conda run --no-capture-output -n chroma-db-import python -m unittest discover -s tests -p "test_gui_*.py" -v
```

If package imports are unavailable, install the repository editable into the selected project environment using its normal setup path; do not patch tests with ad hoc absolute import paths. Run the full existing suite again after backend contract changes and before cutover.

### Frontend (after Task 02 creates these scripts)

Run each from `frontend`:

```powershell
npm ci
npm run typecheck
npm run test -- --run
npm run build
```

Add `test:e2e` for the browser journeys and document its exact command in the work log. Browser mock-bridge tests verify interaction only. The real Python/Chroma fixture checks and the desktop checks below are separate requirements.

### Manual acceptance script

Use disposable synthetic data and record PASS/FAIL plus observed output:

| Check | Action | Expected observation |
| --- | --- | --- |
| First run | Launch empty test state | Library empty state, Create and Add existing visible |
| Create | Choose valid folder, accept defaults | Exactly three setup steps; real database and metadata created |
| Back/draft | Change source, go Back, restart | Latest choices restored; no stale scan replaces them |
| Existing target | Attempt creation at created export | No overwrite; existing database unchanged |
| Register legacy | Add known legacy-profile fixture | Correct existing model/path, no conversion |
| No-op update | Update unchanged fixture | Up to date with check time; no embedding job |
| Add/change | Add episode and correct same-ID record | Review IDs/counts match stored result |
| Metadata only | Change metadata fixture | Updated metadata with zero embedding calls |
| Speaker expansion | Include previously excluded speaker | Only expected new/changed content; shared context preserved |
| Retention | Remove source episode | Records clearly retained by ordinary folder update |
| Managed removals | Prospective version lacks active records | No misleading retaining Apply; explicit supported maintenance path or clear block |
| Explicit deletion | Accept fresh removal review | Only listed records removed |
| Stale preview | Modify source after review | Apply requests a fresh preview |
| Profile change | Choose another representation | Separate build offered; existing update target preserved |
| Managed pending | Inspect incomplete source | Pending source status and usable active database shown separately |
| Managed prepare | Explicitly prepare synthetic producer release | Returns to database review; no hidden activation |
| Navigation | Leave activity during import | Running strip and final result remain available |
| Double Apply | Submit same accepted preview twice | One execution job |
| Close | Close during active import | Supported wait/stop behavior; no silent process kill |
| Interrupted activation | Inject failure at each journal transition | Correct recovery/status; no lost only-good copy |
| Redundancy | Run fixture assessment and export report | Version-bound advisory result; active export unchanged |
| Accessibility | Keyboard-only, 150% scaling, narrow window | Focus visible; forms/actions readable and reachable |
| Distribution | Launch built package without frontend source server | Local assets load, no npm needed at runtime |
| Launcher | Exercise options 1–5, Q, and Legacy selector | Preserved dispatch/exit behavior and correct modern landing page |

Do not run real producer commands for the synthetic producer checks. Use a controlled adapter fixture. Separately document any operator-approved real-source test if performed.

## 7. Final completion checklist

- [ ] Tasks 01–16 have concrete evidence in the work log.
- [ ] Creation and updates work through the real bridge/service, not demonstration data.
- [ ] New database creation cannot overwrite existing storage.
- [ ] Existing identities, profiles, and source contracts remain valid.
- [ ] Preview/execution agreement and retention semantics are tested against stored records.
- [ ] Managed full-version effects are explicit; unsupported operations cannot be applied.
- [ ] Activity, errors, and recovery describe actual active-database state.
- [ ] Specialist operations have the locations specified in the design.
- [ ] Desktop packaging, Windows scaling, folder dialogs, and shutdown are verified.
- [ ] Numbered launcher and legacy fallback remain usable.
- [ ] Existing regression results, skips, and limitations are reported accurately.
- [ ] Runtime-copy decision has been requested or honored; runtime configuration is preserved.

Final implementation report: list the delivered user workflows, commands/checks that passed, any failed/skipped checks, remaining limitations, and how to launch Modern or Legacy. Do not claim the overhaul complete while an unchecked item still blocks the user's common create/update paths.
