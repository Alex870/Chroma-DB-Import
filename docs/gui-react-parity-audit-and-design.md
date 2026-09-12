# React feature completion: Qt audit and proposed UI

Date: 2026-09-12. Status: design for operator review; application code unchanged.

> Follow-up: the user approved the rendered direction. The detailed
> [UI specification](gui-react-ui-design.md) and
> [sequential implementation guide](gui-react-ui-implementation.md) now govern the
> implementation. This document remains the audit/evidence record. Work stays in C:.

This document supersedes `gui-simple-workspace-design.md`. **Qt is the legacy
interface. React is the newer interface being extended.** Preserve React's dark
sidebar, teal actions, database table, detail card, five database tabs, creation
flow, reviewed updates and durable Activity. Do not recreate Qt's toolbar or replace
React with a different library shell.

## 1. Validation method and scope

Audited every navigation surface in the current C: working tree:

* Qt toolbar and its dialogs; Global Settings; episode tree/detail; Managed Contexts;
  Semantic Redundancy; shared progress, messages and saved-state behavior.
* Widget construction, layout attachment, signal connections, action handlers,
  operation arguments, policy persistence and applicable worker behavior.
* React `App.tsx`, `styles.css`, bridge client/types and existing design documents.

Qt uses panels and a toolbar, not literal tab widgets. “All tabs” here includes
those panels, secondary dialogs and cross-panel state. The reviewed source is the
current working tree, including pre-existing uncommitted work, not a historical
Qt release. If an older build had more functionality, that is a separate baseline.

### Executed validation

| Run | Result | What it establishes |
| --- | --- | --- |
| Targeted offscreen Qt audit | 39/39 checks passed | Actual widgets constructed; layout membership, choices, selection, archive/restore dispatch, report dialogs, import cancellation, policy behavior, redundancy arguments, settings file handling and empty-folder gate exercised |
| Existing `test_ui_smoke.py` | 9/9 passed, none skipped | Qt construction, panel lifecycle, failure dialog, prerequisites, source loading, pinned identity rejection, progress and queued import |
| `test_update_import.py` | 3/3 passed | Existing update eligibility tests |
| `test_managed_contexts.py` | 8/8 passed | Fixture discovery, validation, identity, managed destinations and import behavior |
| `test_redundancy_cli.py` | 1/1 passed | Existing CLI test |
| `test_redundancy_evaluation.py` | 13/13 passed | Existing label/evaluation tests |

The targeted run uses real PySide6 widgets offscreen, mocked catalogs/dialogs and
operation launches, and temporary settings files. Passing checks sometimes confirm
a limitation, such as an unattached widget; they do not mean that limitation is
correct product behavior. No live source processing, production database writes,
model calls, CUDA installation or D: configuration changes were performed. This
is a source and isolated behavior audit, not an end-to-end production certification.

Audit harness/results were saved with this task's review materials as
`qt-audit.py` and `qt-audit-results.json`; the findings and coverage below are the
durable repository record. New application tests are not part of this design task.

## 2. Corrections to the supplied comparison

| Earlier implication | Validated current Qt behavior | Design consequence |
| --- | --- | --- |
| Model/profile/revision selection is a Qt feature | One Qwen3 profile; model is read-only; revision is read-only and not attached to the Global panel | Preserve pinned representation. An arbitrary model selector is a new product capability, not missing parity |
| Contextualization is a visible explicit setting | Combo is constructed with minimal/full/none and read by plans, but not placed in a visible panel | Label any new selector as an extension requiring representation validation |
| Mirror removals is a visible global checkbox | Checkbox is constructed/read but not attached to a panel | Preserve React's separate exact removal review; do not introduce the old hidden switch |
| Qt can resume producer processing | No context Resume button; the worker's resume branch returns `producer_action_external` without resuming processing | Offer producer recovery guidance. A genuine Resume requires a new producer capability, not a cosmetic React button |
| Qt settings save/load is complete | Payload includes paths, identity, asset filter/pattern, model/revision/device and episode speaker map; omits contextualization, dedup policies and managed catalog state | Specify a versioned, scoped settings-transfer contract; do not promise a full migration from this JSON |
| Dedup review is a group/reason browser | Visible dialog shows aggregate counts and coverage only | Restore counts first. Inspectable group/reason rows are a useful enhancement, not demonstrated Qt functionality |
| Review Bundle is an interactive result browser | Directory picker dispatches CLI review; logs/completion convey the result | A structured React results browser is a proposed improvement requiring report data |
| Save Policy saves all visible choices | Storage, retrieval and judge settings are saved; channel checkboxes are passed to Assess but not copied into the saved policy | Separate saved defaults from one-run channels, or deliberately fix persistence with an explicit contract |
| Folder preview fully inspects sample content | Shows up to 12 filenames, filter totals and filename-derived dates; sample entries have no content-opening handler | Do not imply file contents are currently previewed. Rich sample inspection is an enhancement |

Additional details missing from the comparison: **All processed assets** filter;
imported/changed episode labels; independent Save Judge Configuration; Cancel and
partial-coverage outcomes; exact dedup Audit “would suppress” counts; plan and
profile fingerprints; producer declared/completed counts, cache count, latest
handoff, workflow/corpus identity; machine-specific portability information;
per-field help; operation elapsed time and skipped-document totals.

## 3. Complete surface inventory and React destination

The inventory contains 75 individually tracked capability rows. Primary source
anchors in `src/chroma_db_import/ui_window.py`:

| Surface | Methods |
| --- | --- |
| Folder browser and toolbar | `ProcessedFolderPreviewDialog`, `_build_toolbar`, `update_action_states` |
| Global settings/state | `render_global`, `build_state_payload`, `apply_state_payload`, `install_cuda_torch`, `show_workflow_guide` |
| Episodes and selection | `rebuild_tree`, `render_episode`, `set_all_global_speakers`, `set_all_episode_speakers` |
| Reports and imports | `generate`, `update`, `reconcile`, `dry_run`, `validation_report`, `collection_info`, `open_export_folder` |
| Context browser/profile | `render_contexts`, `context_selection_changed`, `save_context_profile`, `review_selected_context`, `preview_selected_context` |
| Source orchestration | `_start_context_workflow`, `handle_context_workflow_finished`, `import_selected_context`; `ManagedContextWorkflowWorker.run` in `ui_workers.py` |
| Redundancy | `show_redundancy`, `save_redundancy_policy_from_ui`, `save_redundancy_judge_from_ui`, all preview/assess/pilot/resume/review/export/evaluate handlers |

Legend: **Keep** = existing React behavior; **Add** = missing visible capability;
**Extend** = React has part; **Different** = intentional safer/new contract;
**Not visible** = constructed/backend-only in current Qt.

### Global Settings, toolbar and folder browser

| ID | Verified Qt capability / detail | React disposition and proposed location |
| --- | --- | --- |
| G01 | Open processed folder; recursive cache discovery | Extend Create → Content → Browse/Inspect folder; reuse from database Settings |
| G02 | Suggested valid current/repository/sibling/parent folders | Add suggestions below folder input; select once to scan; validate suggestions rather than copy Qt's path assumptions |
| G03 | Folder tree, scan debounce, selected path and explicit Open/Cancel | Add expandable folder inspector with Use folder / Cancel; selection remains a draft |
| G04 | First 12 sample filenames; matching/excluded counts; filename date hints | Extend scan details with filenames and exclusion reasons; real metadata dates when available, otherwise label estimates |
| G05 | Empty folder prevents Open; zero matching assets can still enable Open when other caches exist | Improve: explain no matches and offer Change filter; block creation with zero eligible records |
| G06 | Output parent chooser and persistent path | Add Settings & help → New database defaults; retain explicit resolved destination in Create |
| G07 | Podcast name affects legacy export destination | Different: React display rename remains independent of storage |
| G08 | Editable database ID and collection | Different: generated stable identity; show actual resolved collection read-only. Custom identity needs separate backend contract approval/design |
| G09 | Reviewed, cleaned, raw, **all**, custom pattern | Extend Create to include Custom; retain other variants and existing settings/update controls |
| G10 | Filter/pattern change reloads assets; selected/discovered totals | Keep automatic scan refresh; preserve valid selection, flag stale review and explain excluded assets |
| G11 | Single Qwen profile, representation warning, read-only model | Keep; surface dimension and query-provider compatibility under Technical details |
| G12 | Device selector with actual CUDA device names; GPU status | Add database Import settings with Auto/CPU/available device; display Auto's resolved device and fallback reason |
| G13 | Revision exists but is not attached; contextualization also unattached | Expose revision read-only; contextualization is a proposed extension, gated by supported representation semantics |
| G14 | Episode/document totals and global date range | Extend Content/scan summary; distinguish available, selected, included and stored |
| G15 | Global tri-state speaker checkboxes and Select All/None | Add selectable speaker list above episodes, with All speakers / No speakers and mixed-state indicators |
| G16 | Save Settings / Load Settings JSON | Add scoped Settings transfer and field-level import preview; preserve episode speaker selections and validate paths/identity |
| G17 | GPU Details with copyable diagnostics | Extend readable Environment report and Copy diagnostics |
| G18 | Install CUDA PyTorch, confirmation, busy gating, progress/failure/recheck | Proposed Environment → Repair GPU support review; retain copy command until managed installer contract exists |
| G19 | Per-field help buttons and toolbar tooltip/status help | Add concise contextual help on advanced fields and linked Guide articles; avoid filling every row with icons |
| G20 | Guide dialog covers create/update/device workflow | Add actual Guide section to Settings & help; rewrite stale legacy workflow instructions |
| G21 | Global/episode panels share elapsed progress and log | Keep durable Activity/job strip; retain elapsed time and exact totals in job details |
| G22 | Generate gates prerequisites and confirms existing destination | Different: keep React no-overwrite Create and staged Maintenance rebuild |
| G23 | Update previews added/changed/metadata-only/unchanged/removed and asks Apply | Keep Update → Apply update; never make users repeat Create |
| G24 | Reconcile explicitly previews deletion, embedding compatibility and staging | Keep exact-ID Maintenance removal review and stale-plan protection |
| G25 | Hidden mirror checkbox alters update semantics | Not visible; do not expose it. Keep ordinary updates retaining missing records |
| G26 | Standalone Dry Run: eligibility, invalid/source counts, validation, reconciliation, embedding identity, staging, dates/errors/warnings | Extend Update review → Full report and Maintenance → Preview import; standalone read-only route |
| G27 | Standalone Validate: document totals, errors/warnings and first issue per file | Add Maintenance → Validate source/database; structured report with all issues rather than Qt's first ten file entries |
| G28 | Collection Info: export/collection, manifest/importer versions, model/dimension, source count, metadata validity and episodes | Extend Overview → Database details; Copy/Save report and explicit missing/invalid metadata states |
| G29 | Open Export derives path and creates it if absent, then Explorer | Add Open folder in database detail header using registered target; do not create a nonexistent export merely to open it |

### Episode tree and detail

| ID | Verified Qt capability / detail | React disposition and proposed location |
| --- | --- | --- |
| E01 | Date/title tree; imported and changed prefixes from fingerprints | Extend Content with Imported / Changed / New / Not checked labels from validated inventory; do not reproduce path-dependent legacy lookup |
| E02 | Preserve selected/expanded tree key and panel scroll | Preserve React search, selection, expansion and focus when opening/closing details |
| E03 | Title, date and full source file | Add episode title drill-down in Content; copyable source path |
| E04 | Total documents; leaf chunks; position cards; cluster summaries; episode theses | Add compact counts in episode details, showing unavailable explicitly |
| E05 | Included-document count after speaker selection | Add live selection summary; distinguish prospective inclusion from current stored content |
| E06 | Individual episode speaker checkboxes and Select All/None | Replace comma-only override editing with checkboxes; explicit Use database default / Custom speakers |
| E07 | Global/per-episode inclusion and shared summary preservation | Preserve semantics; No speakers is not automatically Remove episode. Episode exclusion remains a distinct React control |
| E08 | Import completion: inserted/skipped documents, imported/skipped episodes, elapsed time, warning count; copyable failure | Keep Activity; ensure every total is labeled and full warnings/failure details can be copied |

### Managed Contexts

| ID | Verified Qt capability / detail | React disposition and proposed location |
| --- | --- | --- |
| M01 | Link Source Root persists connection and discovers manifests | Add Source connections → Link source; picker then automatic discovery |
| M02 | Discover / Refresh all linked roots; counts and invalid-candidate warnings | Add Discover contexts, separate from Refresh status; list rejected candidates/reasons |
| M03 | Context list: name, type and local/producer status; context selection | Replace the two-field source form with context browser; include contexts without a database |
| M04 | Selection binds partition/corpus/workflow; resets stale status/pending release | Carry immutable context ID across tabs/operations; no implicit use of the last database selected elsewhere |
| M05 | Release count/newest status, active pointer including invalid pointer | Status tab: latest source release and active database release independently |
| M06 | Active episode/doc counts, last import result, quarantine/failure release count | Status summary plus expandable release/issues list |
| M07 | Database root and masked-portable-metadata notice | Details disclosure with Copy path and export portability explanation |
| M08 | Refresh Source Status; cached producer status | Refresh status button, checked-at timestamp, distinguish cached/unknown/current |
| M09 | Completed/declared, pending, failed, interrupted, cache count, latest handoff/release, warnings | Compact processing summary; full counts/provenance under Source details |
| M10 | Prepare and Import Latest publishes then previews and asks to activate | Prepare update → existing React review → Apply update; keep preparation distinct from activation |
| M11 | Pending processing stops preparation and directs user to producer | Review source issues with producer recovery instructions. Actual in-app Resume is an optional new integration, currently unsupported |
| M12 | Import Newest Release launches import without the prepare confirmation path | Review latest release → existing React review. Do not carry over direct unreviewed import |
| M13 | Save Current Profile: pinned identity, device/contextualization, selected speaker union, dedup policy and fingerprint | Import defaults tab scoped to this context, including speaker policy; Save import defaults. Do not inherit unrelated global episode speakers |
| M14 | Context archive/restore | Context More menu and Archived filter; independent of hiding a database catalog entry |
| M15 | Open Active Database resolves active release export | Open active folder in selected context header; missing/invalid pointer has a specific error |
| M16 | Semantic Redundancy button passes selected context | Analyze redundancy shortcut opens existing Redundancy page with context/release preselected |
| M17 | Dedup off/safe/audit, near report, retrieval collapse, Jaccard threshold, length ratio, maximum block records | Deduplication tab: profile first; Advanced matching options collapsed; save scope explicit |
| M18 | Safe/Audit require downstream dedup-aliases-v1 support; profile affects next import | Display compatibility/readiness beside policy summary and in import review |
| M19 | Review Deduplication validates active artifacts; stored/suppressed/would-suppress, exact groups, near edges, coverage | Deduplication → Review active release; structured aggregate report first, optional group drill-down enhancement |
| M20 | Preview Next Import is model-free, no activation; eligible/stored/suppression/groups and plan fingerprint | Deduplication → Preview next import; display saved policy revision, prospective counts and copyable plan fingerprint |

### Semantic Redundancy

| ID | Verified Qt capability / detail | React disposition and proposed location |
| --- | --- | --- |
| R01 | Requires selected context and discovered release | Existing Redundancy page gets context and frozen-release selectors; allow context entry directly, no database required |
| R02 | Vector storage full/shared_input | Analysis settings disclosure with explanation of private artifact storage and preserved evidence |
| R03 | Retrieval recommendation ranked/semantic_mmr, advisory only | Same disclosure; never imply saving changes Podcast Chat retrieval |
| R04 | Explicit judge enablement, fraction and maximum calls | Judge pilot section; saved opt-in plus explicit Run pilot; show bounded scope |
| R05 | LM Studio URL, explicit model ID, optional fingerprint | Judge connection disclosure; no automatic model selection/download; credentials not in settings export |
| R06 | Save Policy and Save Judge Configuration are independent | Preserve explicit scoped saves, revision/fingerprint and unsaved-state feedback |
| R07 | Lexical, Source/structure, Dense channels; at least one required | Visible compact channel choices for Assessment; distinguish saved policy from this run's overrides |
| R08 | Preview is model-free lexical/source coverage, not a bundle/job creation | Coverage action creates a readable report; do not imply dense preview or frozen assessment created |
| R09 | Assess runs selected channels without judge | Run assessment; prerequisite/result coverage per channel |
| R10 | Pilot requires checkbox AND saved judge-enabled policy; validates judge config | Show missing prerequisites inline; Run pilot uses frozen reviewed settings |
| R11 | Resume accepts frozen job ID | Resume on interrupted result; secondary Find job by ID preserves external-job recovery |
| R12 | Cancel invokes worker cancellation; concurrency guard | Keep persistent job Cancel while supported; display Cancelling until acknowledged |
| R13 | Partial exit code preserves bundle, warns about unavailable channels/judge | Separate Complete / Partial coverage / Failed / Cancelled states; expose pending work and usable bundle explicitly |
| R14 | Review Bundle selects directory and validates | Open bundle from Results; validate schema/release then show summary and findings |
| R15 | Export Labels chooses bundle and output JSON | Export label examples from selected bundle; retain file selection for external bundles |
| R16 | Evaluate selects bundle, reviewed labels, optional queries and query results, report output | Evaluation form reveals optional inputs together; preserves chosen bundle and export destination |
| R17 | Logs plus output/failure/completion messages | Reuse Activity and shared report viewer; retain raw report/log as disclosure |
| R18 | Operations preserve active pointer and original occurrences | Prominent advisory status; no delete/apply-to-database action on this page |

## 4. Proposed changes to the existing React UI

### Navigation: retain the existing shell

Keep **Databases**, **Activity**, the **Advanced tools** grouping with **Source
connections** and **Redundancy analysis**, and **Settings & help**. Keep the five
database tabs: **Overview / Content / History / Settings / Maintenance**. No new
top-level Quality area, merged context/database Library, or Global Settings tree.

Add contextual shortcuts without removing the existing destinations. A managed
database's Overview gets **View source**; Source connections gets **View database**;
its dedup panel gets **Analyze redundancy**. Each passes the selected immutable
identity and has a Back link preserving the originating state.

### Databases: frequent operations stay short

Keep current Create database and Add existing header actions. Keep Update on each
database row and in the detail header. Add **Open folder** to the detail header;
optionally place it on rows if the operator uses it frequently. Replace the current
ellipsis-as-archive behavior with a real More menu. Add Active / Archived filter
and Restore in the archived view.

Keep the table plus selected detail card arrangement in this iteration. Content
adds a speaker selector, searchable episode inventory and expandable episode
details. Bulk controls state the scope: All speakers vs Select all shown episodes.
Imported/Changed labels describe the last verified comparison, with timestamp.

Overview gains Database details and View source. Maintenance gains **Validate**
and **Preview import** beside existing rebuild/removal routes. Full reports use
Summary / Findings / Technical details sections with Copy / Save report.

Create remains Content → Location → Review. Content gains suggestions, an expanded
folder inspector and Custom pattern. The inspector must not become a mandatory
extra page. Source scan loads when a folder is chosen; default filters stay explicit.

### Source connections: finish the existing page

Page header: **Link source** and **Discover contexts**. Left pane: searchable
contexts with type/status and Active/Archived filter. Right pane: selected context,
primary next action, Open active folder, View database and More menu.

Local tabs: **Status / Import defaults / Deduplication**.

* Status shows source readiness and active database separately, then expandable
  producer/release details. Latest ready release → Review latest release. Processed
  content without a release → Prepare update. Pending producer work → Review source
  issues with external recovery instructions. No database → Create database using
  discovered identity and saved defaults. Existing prepared release remains usable
  even if later source work is pending.
* Import defaults shows the pinned profile, device and speaker policy scoped to this
  context. Additional identity details are read-only. Never save global folder
  speaker selections to an unrelated context. Empty/custom/all speaker policies
  need distinct representations; an empty selection must not accidentally mean all.
* Deduplication leads with Off/Safe/Audit and last saved revision. Explain effects:
  use backend-defined policy descriptions; show near report and retrieval collapse
  separately from storing exact duplicates. Threshold, length ratio and block limit
  sit under Advanced matching. Save defaults, Review active release and Preview next
  import remain distinct. Unsaved changes require Save before a saved-policy preview,
  or an explicitly marked one-run preview; never silently preview old defaults.

Archive keeps files and releases. Restore only restores visibility/status. Context
archive is not database archive. Broken pointers, no releases, no linked sources,
rejected manifests and unavailable roots have specific empty/error states.

### Redundancy analysis: useful first screen, specialist depth on demand

Retain the existing sidebar destination. Select context/release once, then keep
them visible. Use local sections **Coverage / Assessment / Judge pilot / Results**;
these are reusable tasks, not a mandatory multi-step wizard.

Start with Preview coverage. Show lexical/structural/dense coverage individually
after assessment, including why a requested channel is pending. Analysis settings
contains storage/retrieval preferences; judge setup is separate and opt-in.
Saving policy or judge connection never starts a job. Assessment must disclose
saved revision plus run-specific channels and stop if these change before launch.

Results preserves the selected bundle. Open existing bundle, Resume interrupted
assessment, Export label examples and Evaluate reviewed labels are discoverable
without repeated context/path entry. Evaluation exposes required bundle/labels,
optional queries/results and output location in one form. A partial bundle is
viewable with clear coverage limitations; it is not labeled a clean success.

No retrieval activation, deletion or promotion is added here. Deep group/pair
inspection is an enhancement where existing output contracts can supply evidence;
aggregate validated reports are the minimum parity deliverable.

### Settings & help

Keep current Environment and Legacy-state migration. Add **New database defaults**,
**Settings transfer**, and **Guide** as compact expandable sections. Database/context
settings remain scoped and accessible from their own views. New output defaults do
not relocate existing targets.

Settings transfer exports chosen scopes: application defaults, selected database
selection/settings, selected context import defaults, and optionally non-secret
redundancy configuration. Explain excluded runtime/catalog state. Import validates
schema, paths, identity and model compatibility; shows old/new per field; applies
selected compatible changes and preserves unrelated values. Omitted legacy fields
are reported as “not supplied,” not reset to defaults.

Environment diagnosis leads with readable GPU/device results. Repair GPU support
shows the target environment and exact action before installation, then progress
and verification. Until that contract is implemented, keep the existing copyable
repair command. This design does not grant permission to modify runtime settings.

Contextualization editing, arbitrary model selection and custom database identity
are not required to reproduce the *visible current Qt* UI. Show effective supported
values; evaluate editable contextualization as a separate extension. The earlier
mockup's unconditional minimal/full/none selector is withdrawn.

## 5. Huffman-inspired effort budget

Frequency ranking is an assumption pending operator feedback. Optimize both actions
and decisions; do not skip exact-write review to make a click count look better.

| Task / starting point | Target path | Cost |
| --- | --- | --- |
| Update configured database / visible Databases row | Update → Apply update | 2 activations; additional acknowledgments only when required |
| Open folder / selected database | Open folder | 1 |
| Inspect episode / selected Content tab | Episode title | 1 |
| Change every speaker / Content selector | All speakers or No speakers | 1 edit, then Save defaults or Review selection |
| Import available managed release / selected source context | Review latest release → Apply update | 2 |
| Prepare managed release / selected context | Prepare update → Apply update after preparation/review | 2 launches/approval actions, plus wait; source recovery may add steps |
| Run source/database validation / selected Maintenance tab | Validate → report | 1 launch; scope defaults remembered |
| Preview redundancy / preselected release | Preview coverage | 1 launch |
| Restore / archived list | Restore | 1 |
| Rare policy/judge/settings changes | Expand relevant settings → edit → explicit scoped save | Longer path justified by lower frequency and greater consequence |

Retain stable locations; remember source, context, release and draft values. Avoid
automatic menu reordering. Disabled actions explain requirements adjacent to the
control. One review page is sufficient for routine updates. All slow operations
leave durable status in Activity; navigation preserves work. Keyboard focus returns
to the launching row after closing details; status text never relies on color alone.

## 6. Delivery order and backend prerequisites

1. Small React extensions: actual More menu, Open folder, Archived/Restore, Copy,
   Guide, Custom in Create, speaker bulk controls, episode fields/import labels and
   Overview metadata. Reuse existing client methods; add missing data/restore methods.
2. Source browser: typed source/context/release inventory, link/discover/status,
   archive/restore, active-export mapping and context-scoped saved profile. Build on
   managed catalog/adapter methods only after checking their GUI contract.
3. Standalone reports and deduplication: structured validation, full dry-run,
   aggregate ledger review/prospective plan, saved policy revisions and stale-review
   invalidation. Add deep evidence browsing where available.
4. Redundancy: bridge preview/assessment/pilot/resume/cancel/bundle validation,
   policy/judge persistence, label export/evaluation and durable partial outcomes.
   CLI dispatch alone does not supply a usable React report contract.
5. Scoped settings transfer and installer workflow. Keep producer Resume, arbitrary
   models/custom identity/contextualization changes as explicitly separate decisions.

Do not mark an inventory row complete until its control, data, validation, outcome
and recovery path work together. Reuse React Create/Update/Activity/Maintenance and
one report component. Preserve no-overwrite creation, staged rebuilds, exact removal
review and active-pointer/identity validation. Promotion/rollback/prune were not a
complete Qt workflow and remain outside this parity proposal.

## 7. Review renders

The accompanying interactive renders show three entry states in the same retained
React shell: **Source connections**, **Databases → Content**, and **Redundancy
analysis**. Each also allows navigation to Create, Overview, Maintenance, History,
Settings & help, source defaults and deduplication. Data are illustrative and all
actions are local simulations. They are design previews, not an implemented app.

Browser rendering validation: all three entry views rendered at 1,024px content
width; the source, database and redundancy subsection navigation was exercised
with no JavaScript errors. A narrow Settings view in each copy had no root
horizontal overflow. Desktop renders were visually inspected. This is a limited
prototype check, not full accessibility or application acceptance testing.

Review priorities: whether managed-context fields are all reachable; whether
deduplication and semantic redundancy are clearly distinct; whether the Content
view is less busy while preserving speaker control; and whether the primary actions
match actual daily frequency. The inventory above is the completeness checklist
for the next revision, independent of what fits in a single rendered screen.
