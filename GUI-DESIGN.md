# GUI design proposal: create and update databases with confidence

Date: 2026-09-07  
Status: Proposed design; no application behavior changed.

## 1. Recommendation

Replace the toolbar-centered workspace with a **database library**. Make **Create database** and **Update** the primary actions. Each database should remember its source, destination, content selection, and embedding profile. Routine updates should require selecting the database, reviewing the detected changes, and starting the update.

Keep the PowerShell launcher and its numbered menu. Separate maintenance, source preparation, and analysis into clearly named areas inside the GUI. Use a short guided flow for creation and a compact change review for updates; returning users should not repeat a creation wizard.

For implementation, first establish a UI-independent workflow service around the existing Python code. Use React with TypeScript in a local desktop webview as the preferred direction for a substantial redesign, subject to a small Windows integration prototype. A redesigned PySide6 interface remains a credible lower-cost option. The workflow changes deliver the usability improvement; changing rendering technology alone does not.

## 2. Review basis and limitations

This is a source-based walkthrough of the current working tree, including its existing uncommitted changes. It is not a live usability test or a visual inspection of a running application. Findings about discoverability and cognitive load are design judgments to validate with representative users.

Primary evidence:

| Source | What it establishes |
| --- | --- |
| `Run Chroma DB Import.ps1` | Five numbered entry points, including a separate managed-context entry |
| `README.md`, UI Workflow and Corpus Release Workflow | Intended everyday workflow and advanced release operations |
| `src/chroma_db_import/ui_window.py`, `_build_toolbar`, `render_global`, `render_contexts`, `show_redundancy` | Action hierarchy, settings, context orchestration, and analysis controls |
| `src/chroma_db_import/ui_window.py`, `generate`, `update`, `reconcile`, `handle_context_workflow_finished` | Current previews, confirmations, and operation transitions |
| `src/chroma_db_import/ui_models.py`, `ImportPlan` | Destination derivation and representation-specific storage |
| `src/chroma_db_import/ui_export.py`, `preview_ui_import_plan`, `update_should_skip_episode`, `export_chroma` | Validation, update eligibility, staging, and promotion |
| `src/chroma_db_import/managed.py`, `ui_workers.py`, `release_cli.py` | Managed catalog, background execution, and release capabilities |
| `tests/test_update_import.py` | Expected update behavior when speaker selection expands |

Existing strengths worth preserving include the folder preview, saved UI state, per-episode speaker selection, validation before import, embedding compatibility information, staged exports, managed partition isolation, and operation progress. The redesign should make these protections easier to understand.

## 3. Where the current workflow creates friction

| Current experience | User consequence | Design response |
| --- | --- | --- |
| Thirteen toolbar actions include Open, Contexts, Redundancy, Generate, Update, and Reconcile at the same level | The user must understand the tool inventory before knowing what to do | Start with databases and task-specific primary actions |
| The opening workspace is a tree rooted at Global Settings | Setup fields dominate before the user has chosen a task | Show recent databases and a clear first-run empty state |
| Source, output, identity, model, device, speakers, GPU installation, and logs share a settings workspace | Routine choices compete with infrequent technical configuration | Separate content, database settings, application settings, and diagnostics |
| Output depends on the podcast name, output root, and representation profile | A name or profile change can redirect an apparent update | Register the resolved destination and stable identity; display-name edits must not relocate storage |
| Generate also handles replacing an existing export | “Create” and “replace existing data” are conflated | Creation never overwrites; rebuild is a separate maintenance operation |
| Toolbar help describes Update as appending new episodes, while the worker also considers changed content fingerprints and newly selected speakers | Users cannot confidently predict whether corrections will be imported | Use one backend-produced preview to describe actual planned writes |
| Update may show a removal warning followed by a confirmation; Reconcile has another preview | Modal dialogs fragment one decision | Use one persistent review page with additions, changes, and retained/deleted records |
| Contexts has refresh, resume source processing, prepare/import, import newest, and preview actions | Users must infer the correct order and distinguish upstream work from database work | Present a state-dependent next action with source and database status shown separately |
| Managed settings reuse controls associated with the broader workspace | It is difficult to know which settings apply to which database | Scope settings visibly to the selected database; avoid implicit global carryover |
| Redundancy exposes policy, judge, candidate channels, jobs, bundles, labels, and evaluation together | A specialist workflow occupies everyday navigation | Put it under Advanced tools and guide its own stages |
| Progress and logs are embedded in changing detail panels | It is harder to return to a job and understand the final outcome | Add persistent activity and a structured completion report |

There is also wording drift around Generate: the dialog says “Delete and rebuild,” while the current export implementation builds in staging and then moves the destination aside during replacement. The new interface must describe the verified operation contract accurately, including recovery limits, rather than preserving outdated labels.

## 4. User model and navigation

The user's object is a **database**, whether its source is a managed Podcast-RAG context or a folder of processed files. Keep those source types explicit, but give them the same library and task structure. Unifying presentation must preserve their different validation and release rules.

Use these navigation areas:

| Area | Contents | Visibility |
| --- | --- | --- |
| Databases | Library, create, add existing, selected database | Default landing page |
| Activity | Running jobs, outcomes, recoverable interruptions, reports | Always accessible; running count shown |
| Advanced tools | Source connections, detailed validation, redundancy analysis, release administration | Secondary navigation |
| Settings & help | Default storage, compute device, environment diagnostics, migration guidance, help | Bottom of navigation |

Within a database, use **Overview**, **Content**, **History**, and **Settings** tabs. Put **Maintenance** in an explicit secondary menu with Rebuild, Remove outdated records, and supported restore operations. Archive belongs in the library menu, clearly described as hiding the entry without deleting files.

Use plain labels first: “Source connection,” “Database version,” and “Embedding profile.” Show partition IDs, corpus IDs, release IDs, collection names, and fingerprints in expandable technical details with copy controls. A source release and a database version are distinct entities and must remain separately identifiable there.

### Launcher

Preserve existing numbers and command-line action values for compatibility:

1. Check environment
2. Open database manager
3. Migrate settings and state
4. Set up or repair environment
5. Open managed sources
Q. Quit

Option 2 opens the library. Option 5 opens the same application at managed source connections, with links into discovered database entries. It should not feel like a second product. Environment checks should report concise results, with detailed diagnostics available when needed.

### Library wireframe

```text
Chroma DB Import                          [Create database]

Databases       Search databases...       [Add existing]
Activity (1)
                Database        Status                 Action
Advanced tools  Weekly Podcast  Last checked: 2 new     Update
                Team Meetings   Source incomplete      Review source
Settings & help Research        Up to date             Open

                Last checked 10:42 AM          Refresh status
----------------------------------------------------------------
Weekly Podcast: embedding 320 / 900 records     [View activity]
```

Counts are illustrative. “2 new” requires a completed check and a timestamp. Before checking, say “Not checked,” not “Up to date.” Empty state: “Create a database from processed podcast or meeting content,” with Create database and Add existing database.

## 5. Common path: create a new database

Use three short steps with a persistent summary. Back navigation preserves choices. Saving a draft is automatic and clearly distinct from creating a database.

### Step 1: Choose content

Offer **Podcast-RAG source** and **Processed files folder**. Remember the last source type, but do not require users to understand “managed” versus “legacy” before they can proceed.

For a connected source, show discovered podcasts or meeting contexts and readiness. Require one context per database; selecting several can later create separate jobs and entries, never silently combine partitions. For a folder, retain the existing folder preview and suggested locations. Explain that this path needs processed output, not raw audio or unprocessed transcripts.

After scanning, show eligible episodes, date range, speaker count, and excluded file count. Preserve the current reviewed-transcript default, but explicitly show “Reviewed speaker transcripts” and a Change link. If files exist but none match, explain the filter and offer to review other variants instead of reporting an empty folder.

Default to all eligible speakers for a new database. An optional **Choose content** panel exposes searchable episodes and speakers, global selection, per-episode exceptions, and a summary such as “All speakers except 2; 3 episode overrides.” Explain that episode-level context and shared summaries can remain when a speaker is excluded. Speaker filtering is not a guarantee that all mentions of that person disappear.

### Step 2: Name and location

Ask for a display name and suggest a storage location. Display the complete resolved database path, including any representation subdirectory, before proceeding. Generate a stable database ID and valid collection name; place them in technical details.

If the destination exists, offer **Use existing database** or **Choose another location**. Do not offer overwrite within creation. A rename after creation changes the catalog display name only; storage moves require a separate operation. This requires decoupling the current `ImportPlan` path calculation from the display name.

Show the configured Qwen3 embedding profile with a short readiness result. The project has one pinned production representation, so model revision, dimension, contextualization, and query-mode details are read-only technical information rather than selectable profiles. Run device/memory readiness checks before a long import. If the device is unsuitable, explain the actionable alternatives without silently changing the embedding identity.

### Step 3: Review and create

Run validation automatically and present:

- Database name, source, exact destination, and embedding profile.
- Eligible episode and record counts; excluded content with reasons.
- Device, model availability/download requirement, and disk-space readiness.
- Blocking issues and nonblocking warnings, each with a direct remedy.

Use **Create database** as the single execution action. Do not require a separate Dry Run or Validate step. Missing prerequisites appear beside the action and affected fields, not only in tooltips. Start a background job and take the user to its activity view.

Success reports “Database created,” episode/record totals, output location, and validation outcome, with **Open database folder** and **View database**. Offer downstream connection instructions; do not claim PodCast Chat is connected unless that connection has actually been verified.

## 6. Common path: update an existing database

From the library, **Update** checks the remembered source and opens one review screen. A returning user with ready content should only need **Update → Apply update**. The check performs no source processing or publishing.

Load the database's saved identity, actual export location, content policy, and embedding profile. Do not infer its target from the last global form values. An existing non-Qwen3 database is incompatible and must be rebuilt as a fresh Qwen3 export; installing a newer application must not query it with Qwen3 vectors.

```text
Weekly Podcast / Update
Source: Podcast-RAG / Weekly Podcast    Checked just now
Target: D:\...\Weekly Podcast\<profile>    Compatible

New episodes                 2
Changed episodes             1       View changes
Unchanged episodes          84
Records missing from source 18       Retained by this update

Content: saved speaker selection       Change for this update
Checks: passed                        View details

[Back]                                      [Apply update]
```

Keep episode counts and record counts separately labeled. The review must distinguish **detected source differences** from **writes this operation will perform**. Current eligibility checks are broader than the toolbar description, but this alone does not prove every detected change is reflected identically in final storage. Preview/execution agreement is an implementation prerequisite.

Rules:

- No differences: show “Up to date” with the check time and no import action; avoid loading the embedding model.
- New content or expanded speaker coverage: show exactly what will be added.
- Changed content: show records to replace/update and whether re-embedding is needed, using execution-derived counts.
- Content absent from the source or newly excluded: retain it by default and say so. Offer a link to Maintenance → Remove outdated records.
- Representation mismatch: block in-place update and offer Create separate database with the selected profile.
- Missing/corrupt metadata: show repair or registration guidance; do not guess the identity or mark it compatible.
- Source or settings change after preview: invalidate the preview and regenerate it before execution.

One review page is sufficient for an ordinary update. A removal operation gets its own explicit review listing affected records and reasons. Do not hide deletion inside a sticky “Mirror removals” setting on the normal update path.

### When a managed source is not ready

Show separate source readiness and active database health. Pending source work does not make a previously usable database broken.

| Source condition | Next action |
| --- | --- |
| Valid newer release available | Review update |
| Processing pending/interrupted | Review source, then explicitly Resume source processing |
| Processing complete, release not prepared | Prepare source release, then review import |
| Failed or quarantined items | Review issues with counts and producer details |
| No newer release | Up to date as of the last check |

Make upstream mutations explicit: “Resume source processing” and “Prepare source release” affect Podcast-RAG. They are never hidden side effects of Refresh or Check for updates. After source preparation completes, continue to the database review automatically rather than asking the user to discover the next button. Do not start database activation until that review is accepted.

## 7. Put less frequent work where users can find it

| Function | New home and interaction |
| --- | --- |
| Speaker and episode selection | Database → Content; searchable table with included/excluded state and exceptions |
| Source location and filename filters | Database → Settings → Source; changing these triggers a fresh preview |
| Model/profile/collection identity | Database → Settings → Embeddings; changing representation offers a separate build |
| GPU details and CUDA installation | Settings & help → Environment; show repair only when relevant |
| Dry Run | Incorporated into create/update review; retain “Export preview report” |
| Standalone validation | Advanced tools → Validate source or database; explicit target picker |
| Collection Info | Database → Overview → Technical details |
| Save/Load Settings | Automatic scoped persistence; Import/Export settings retained under Settings |
| Reconcile / Mirror removals | Database → Maintenance → Remove outdated records, with exact deletion review |
| Generate on existing output | Database → Maintenance → Rebuild; show scope, compatibility, space, and recovery implications |
| Source discovery and registry information | Advanced tools → Source connections; databases link back to their source |
| Exact deduplication review | Database → Content → Repetition handling, with policy and suppression evidence |
| Semantic redundancy | Advanced tools → Redundancy analysis; select database and frozen version first |
| Corpus release promotion, rollback, retention | Database → History or Advanced tools → Release administration, where supported |
| Legacy adoption/migration | Add existing database or Settings & help → Migration; retain launcher option 3 |

Redundancy analysis should have its own sequence: **Select version → Preview coverage → Assess → Optional judge pilot → Review/export results**. Put candidate channels, thresholds, judge configuration, label export, and evaluation in the relevant stage. Mark results as advisory; assessment does not modify the active database. Keep exact deduplication and semantic similarity distinct.

Release administration must preserve existing plan/approval contracts. A stored export snapshot is not automatically a supported rollback target. Expose restore, promotion, or prune actions only when the corresponding backend can validate and perform them for that database type. CLI-only operations can initially have clearly labeled instructions rather than nonfunctional GUI buttons.

## 8. Activity, errors, persistence, and accessibility

Every operation needs a durable job entry with its database, source snapshot, accepted settings, timestamps, stages, outcome, and report. Suggested stages are Checking source, Preparing, Embedding, Validating database, Activating, and Complete. Show measured record progress when a denominator exists; use an indeterminate indicator during model loading. Do not invent completion percentages or duration estimates.

Keep a compact running-job strip visible across navigation. The detailed view shows a plain-language status first and expandable logs below. Completion includes added/changed/skipped/retained counts, warnings, and the resulting version/path. “Completed with warnings” is distinct from Failed.

Cancellation and recovery are backend capabilities to implement and verify, not labels to add optimistically. Current redundancy cancellation does not establish safe cancellation for all import workers. Offer **Stop after current batch** only for jobs with cooperative stopping; otherwise explain that the current stage must finish. Closing the application during work must offer a supported wait/stop choice. Do not promise background continuation after closing until a separately managed worker can provide it.

On restart, inspect unfinished jobs and staging evidence. Offer Resume only when a validated checkpoint exists; otherwise offer Retry with an explanation of cache reuse and cleanup. Activation failure should identify whether the old database is still available. Never claim universal atomic replacement or automatic rollback based solely on the existing staging implementation.

Error messages should state what failed, whether active data changed, and the next action. Example: “The source folder is unavailable. Your active database has not changed. Reconnect the drive or choose its new location.” Preserve technical details for copying into a diagnostic report.

Persist application defaults, per-database settings, draft selections, and accepted job plans separately. Editing a draft must not rewrite the settings recorded for a running job. Explain whether speaker rules apply to newly discovered speakers; default new databases to include all, and preserve an existing explicit allowlist when updating. Importing settings must show a field-level review and must not silently overwrite operational configuration.

Provide keyboard navigation, visible focus, accessible labels, text as well as color for status, readable contrast, resizable tables, and layouts usable at Windows display scaling. Keep the primary action visible without scrolling through logs. Search and filter long episode lists; avoid rendering every document as a widget.

## 9. Framework decision

The following is an architectural assessment, not a measured performance comparison.

| Option | Benefits for this repository | Cost/risk | Recommendation |
| --- | --- | --- | --- |
| Reorganize PySide6 Widgets | Reuses current runtime, workers, native dialogs, and tests | Requires deliberate component/state separation to avoid another large main window | Best route for an incremental delivery |
| Qt Quick/QML with Python | Declarative desktop UI while retaining Qt/Python | Adds QML skills and integration work; still requires workflow extraction | Credible alternative if maintaining a Python/Qt stack is preferred |
| React + TypeScript in a local desktop webview | Suits reusable review screens, tables, navigation, and explicit view state | Adds frontend build tooling and a Python bridge; packaging and lifecycle need validation | Preferred candidate for the major overhaul |

React supports component-based UI and incremental integration into an existing web surface; it does not replace the Python importer or supply a desktop host. See [React integration documentation](https://react.dev/learn/add-react-to-an-existing-project). Qt Quick supplies QML and Python APIs for declarative interfaces; see [Qt for Python: Qt Quick](https://doc.qt.io/qtforpython-6/PySide6/QtQuick/). A candidate desktop host is pywebview, which provides native dialogs and Python/JavaScript communication; see its [official introduction](https://pywebview.flowrl.com/guide/).

Prototype React in pywebview before choosing the production shell. Test Windows folder selection, paths with spaces, display scaling, long-running progress, startup failure reporting, and shutdown during work. Ship compiled frontend assets locally; normal users should continue launching through PowerShell and should not need to run a frontend development server. Validate webview availability and installation requirements on the supported Windows environment.

Keep computation in Python workers. Expose a narrow typed interface for listing databases, scanning sources, generating previews, starting jobs, and reading activity. The UI should never construct arbitrary shell commands or implement embedding/reconciliation rules. If an HTTP transport is selected, bind locally and authenticate requests; if a direct bridge is selected, expose only explicit operations. No cloud hosting is required for this desktop design.

## 10. Implementation boundaries and delivery sequence

### Phase 1: Make workflow truth reusable

Extract services from the window for database registration, source checks, preview creation, and job execution. Reuse `ui_export.py`, `managed.py`, importer, staging, representation, and release contracts; preserve their distinct managed/folder behaviors behind adapters.

Introduce a database record containing stable ID, display name, source type/reference, explicit resolved destination, profile, content policy, and active version where available. Migrate saved state conservatively: register known exports by inspecting metadata, and ask the user to resolve ambiguous identities. Do not automatically move files or rewrite source registries.

Define a frozen preview containing database identity, source fingerprints/release, target identity, selected policy, actual proposed changes, warnings, and required approvals. Execution must validate the accepted preview against current state. Add a durable job lifecycle and prevent concurrent writers to the same destination; initially serialize embedding jobs to avoid GPU contention.

### Phase 2: Prove the two common paths

Build the library, three-step creation, two-action update, and activity views using the chosen prototype shell. Include both source adapters and Add existing database. Keep the present GUI available during the transition through a documented fallback. Verify representative existing exports before switching launcher option 2.

### Phase 3: Move specialist workflows

Add source readiness guidance, maintenance reviews, repetition analysis, release history, settings import/export, and diagnostics. Bring over specialist workflows only after checking their operation contracts; keep advanced CLI access available throughout.

### Phase 4: Harden and switch the default

Validate Windows packaging and recovery behavior, finish accessibility checks, update the guide and launcher labels, and remove duplicate entry points only after feature parity for supported workflows. The launcher retains its numbered structure.

This design adds only this document to the canonical C: repository. Implementation and runtime propagation are separate work. Any future runtime configuration changes must honor the repository rule requiring consultation and a field-aware merge that preserves unrelated operational values.

## 11. Acceptance criteria and usability validation

These are proposed targets, not measured results:

| Scenario | Passing result |
| --- | --- |
| First database from valid processed content | Completed in three setup steps without opening advanced settings or reading the guide |
| Routine update with ready content | Update → Apply update; no repeated folder, name, or model entry |
| Nothing new | Timestamped Up to date result without embedding work |
| Source correction or added speaker | Preview and final writes agree; no misleading “already imported” summary |
| Removed source content | Normal update explicitly retains it; deletion requires the maintenance review |
| Existing legacy profile | Update uses its recorded identity; new default models do not silently change its target |
| Rename or duplicate destination | Rename does not relocate data; creation cannot overwrite an existing export |
| Managed source incomplete | User sees source next steps and the separately usable active database |
| Source changes after review | Execution requests a fresh preview before applying changes |
| Failure during staging or activation | Accurate active-database status and a supported recovery action |
| Navigate away during import | Job progress and final report remain discoverable |
| Advanced analysis | Reachable within two navigation choices after selecting a database; no ordinary import dependency |

Test with a first-time user and a returning operator using small representative fixtures: create a database, update with one new episode, import a corrected episode, expand speaker selection, handle an unavailable source, and inspect a redundancy report. Ask users to identify the target database, explain what Apply will change, and find the active output without coaching. Record completion, wrong turns, and uncertainty rather than relying only on click counts.

Automated checks should cover preview/execution agreement, identity preservation, source snapshot invalidation, default retention, explicit deletion approval, same-target writer exclusion, settings migration, and recovery after interrupted activation. Add UI smoke coverage for the two common journeys. Framework selection should follow a working end-to-end prototype and these outcomes.
