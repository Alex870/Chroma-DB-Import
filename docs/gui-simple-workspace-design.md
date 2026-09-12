# Simple workspace design for React feature parity

Design proposal · 2026-09-12 · Not an implementation

> Superseded for implementation by [React parity audit and design](gui-react-parity-audit-and-design.md).
> The revised proposal validates Qt's actual visible controls and preserves the
> current React shell, navigation and database tabs. This earlier Library redesign
> is retained only as design history.

Based on `gui-qt-react-feature-comparison.md`, the current `frontend/src/App.tsx`,
and its bridge client/types. The comparison is the parity baseline; the specialist
disposition document sometimes describes backend capability more generously than
the visible UI. Existing unrelated working-tree changes are outside this proposal.

## 1. Central decision

Make **Library** the everyday workspace for both managed contexts and folder
databases. Each row answers: what is this, is its source ready, is its database
current, and what can I do next? Put one clearly named next action on the row.
Keep **Activity** and **Settings & help** as the other primary destinations.

Replace the separate Source connections and Redundancy sidebar pages with
contextual Source and Quality tabs. Users should not have to select a database,
navigate elsewhere, and enter its source/partition identifiers again.

Do not merge the underlying context and database identities. A managed context
can exist without an export and can have several database/profile variants.
Show one context row with its explicitly active database; show additional variants
under its detail view. Folder databases have their own rows. Match managed exports
to contexts using recorded identities, never display names or path guesses.
Unmatched registered exports remain visible until explicitly linked.

## 2. Huffman-inspired interaction budget

Use expected frequency to allocate short paths and visual prominence. This is a
design heuristic, not a literal prefix-code tree: UI actions also have reading,
waiting, error, and recovery costs. No usage telemetry was supplied. The ordering
below is a hypothesis to validate with the operator, not measured frequency.

| Expected frequency | Task | Target path from visible Library row |
| --- | --- | --- |
| Very frequent | Bring a database up to date | Review update → Apply update: 2 activations |
| Very frequent | See source/database status | 0; separate labeled columns, with last-check age |
| Frequent | Open the active export folder | Open folder: 1 activation |
| Frequent when interrupted | Continue source processing | Resume processing: 1; review/import remains separate |
| Occasional | Inspect an episode | Row name → Content → episode title: 3 |
| Occasional | Validate | Row name → Quality → Run checks: 3 |
| Occasional | Discover additional contexts | Add source → choose root → Link source; discovery follows automatically |
| Rare | Change processing policy | Row name → Settings → expand relevant options |
| Rare | Run judge/evaluation work | Row name → Quality → Redundancy analysis → relevant operation |

File-picker interactions, entering values, required warning acknowledgments and
waiting are additional costs. Two-click update assumes a linked, configured source
with an available release and no blockers. Never imply every update is two clicks.

Keep action placement stable. State may change the action label, but do not reorder
navigation based on recent use. Remember source, partition, selection and profile;
do not make users re-enter them. Use one review screen, without a second generic
confirmation dialog. Exact deletions and environment installation retain explicit
review. Measure task completion time, navigation backtracking, errors and action
counts before deciding whether further shortcuts help.

## 3. Library

Header: **Library**, search, **Add source**. Add source opens three plain choices:
**Link managed source**, **Create from folder**, **Add existing database**. Keep the
existing create/register implementations behind those entries.

Rows: **Name | Source | Database | Next action | Open folder | More**.
Use names such as “Weekly podcast” with “Managed source” as secondary text;
show technical paths only in details or copyable tooltips/details disclosures.
Source and Database each display their own status and checked timestamp.
“Not checked” is distinct from “Up to date.” Color always has a text label.

Show All / Needs attention / Archived filters. Default to All, preserve the user's
filter, and preserve stable name order. Needs attention is a user-selected filter,
not an automatic reordering mechanism. More is an actual menu, not an archive
button disguised as an ellipsis as in the current implementation.

| Authoritative state | Next action | Result |
| --- | --- | --- |
| Unknown/stale status | Check for updates | Read-only check; proceed to review if possible |
| A newer frozen release exists | Review update | Preview that specific release; Apply update imports/activates it |
| Processed source changes need a release | Prepare update | Prepare release, then show review; never auto-apply |
| Producer has interrupted, resumable work | Resume processing | Resume supported producer job; show progress, then Prepare update |
| Producer is actively running | View progress | Open its job; do not start a duplicate |
| Context has no database | Create database | Prefilled existing creation flow |
| Source and database are current | Check for updates | Explicit fresh check; Open folder remains adjacent |
| Source disconnected/invalid | Fix source | Open Source tab at the relevant problem |
| A review is persisted and still valid | Continue review | Resume the exact review |

For simultaneous conditions, prioritize an active operation, blocking problems,
then a valid pending review, then update/create availability. A newer ready release
may coexist with pending producer work: offer Review update for the ready release
and show pending processing separately in Source. Do not block useful imports
merely because newer source material is still processing.

For archived entries, show Restore as the row action. Explain archive scope:
“Hide this context from the library; keep its releases and files.” Database-entry
archive and context archive are distinct operations; a context archive does not
silently archive every database record. Restore never starts processing/importing.

## 4. Selected workspace

Open a dedicated detail view with a Library breadcrumb instead of placing details
below the entire library table. Header retains name, next action and Open folder.
Use **Overview · Content · Source · Quality · History · Settings**. Put infrequent
maintenance inside Settings under a clearly labeled Maintenance section.

### Overview

Two compact sections: Source readiness and Active database. Include release/date,
episode count and any actionable issue. A **Database details** disclosure contains
database ID, collection, path, importer/manifest versions, model/revision/dimension,
source-file count and metadata validity. Distinguish unknown or last-validated
values from live checks. Give technical fields Copy and reports Copy report / Save
report. Managed variants live here with their explicit active status; ordinary
selection does not promote or activate a version.

### Content

Reuse episode selection. Add labeled **Select all shown** / **Clear shown** controls
and a tri-state header checkbox. State “12 shown of 80” under a search; bulk actions
affect the stated scope, not invisible rows. Provide separate All speakers / No
speakers controls globally and within an episode. Preserve the existing distinction
between shared context and included content.

Click an episode title to open an inline detail panel with title/date, source file,
document and included-document counts, leaf chunks, position cards, cluster
summaries, thesis count and speakers. Missing fields read “Not available.” Closing
the panel preserves scroll, search and selection. View details must not toggle
inclusion.

Selection edits persist as a draft until **Save content defaults** or **Review this
update**; label their scope explicitly. Empty selection explains why import cannot
proceed. Add Custom pattern next to Reviewed/Cleaned/Raw during Create as well as
later editing. Expand the pattern field only when Custom is selected.

### Source

Managed source: linked root, selected context, last refreshed producer status,
latest prepared release and pending work. **Refresh status** is read-only.
**Resume processing** appears only when supported and resumable. **Prepare update**
prepares then reviews; **Review latest release** imports an already prepared release
without re-running preparation. Shared source connection details link to
**Manage linked sources** for discovery/refresh, relinking and other contexts.
Discovering contexts registers visibility only; it does not process or import them.

Folder source: current folder, **Change folder**, **Inspect folder**. Reuse the
Create source browser: recent valid locations and suggested processed_data roots,
Browse, folder tree, selectable sample files, counts/date range/speakers and
excluded-file reasons. Suggestions must be labeled and checked before use.
Inspection is read-only; **Use this folder** explicitly commits the choice to the
draft. Large trees load on expansion. Relinking shows old/new identity before save.

### Quality

Start with **Run checks**, a short latest-result summary and last-run timestamp.
Checks cover processed cache/metadata and database/manifest validation as applicable.
Offer Source / Database / Both; default to Both when both exist. Reports show
eligibility, reconciliation, embedding readiness, staging, date range, errors and
warnings in disclosures, restoring standalone validation and detailed Dry Run
inspection. **Preview next import** is model-free and cannot import or prepare a
release. Actual update/rebuild review remains the authoritative mutation plan.

Two expandable tools follow:

* **Import deduplication**: current policy summary, Review latest deduplication,
  Preview next import and Edit policy. Review shows release-ledger groups, reasons,
  coverage and provenance. Policy exposes profile, near-match/retrieval toggles,
  thresholds and block limits with backend-defined ranges/defaults. Changes apply
  to a future preview/release and invalidate affected pending reviews.
* **Redundancy analysis**: advisory analysis of a frozen release; it never silently
  removes records or changes the import deduplication policy.

### Redundancy workflow

Keep all operations in one workspace, progressively revealed:

1. **Release** defaults to the selected context's latest prepared frozen release,
   visibly showing ID/date and whether it is active. A release picker permits older
   releases. With none available, link to Prepare source release.
2. **Preview coverage** runs without a model. Show selected candidate channels and
   coverage limitations. Expand **Analysis options** for lexical/structural/dense
   channels, vector storage and retrieval mode. Dense prerequisites are checked;
   “no model” must not be implied for Assess.
3. **Run assessment** starts selected channels. Job progress/retry/cancel reuse
   Activity. Interrupted frozen jobs expose **Resume assessment** directly;
   **Find job by ID** remains a secondary recovery action for jobs outside history.
4. Results open **Review results**: groups/pairs, reasons, provenance and a detail
   panel. **Open existing bundle** supports externally produced results and validates
   release/schema before review. Invalid bundles show actionable errors.
5. **Judge pilot** is a separate optional action. Expand Judge setup for LM Studio
   URL, model, optional fingerprint, fraction and cap; judge defaults to disabled.
   Show the bounded workload and endpoint before **Run pilot**. Remember setup,
   but never silently enable judging for later assessments.
6. **Export labels** exports supported label examples. **Evaluate reviewed labels**
   selects reviewed labels and optional queries/results, validates compatibility,
   runs evaluation and displays a copyable/saveable report. Do not assume an in-app
   label editor exists or invent one as a parity prerequisite.

Release/policy identity remains visible through every stage. Switching releases
does not relabel old results or resume an incompatible job. Provide an explicit
choice to view an older bundle on its own release. Missing prerequisites produce
specific repair actions, not a screen full of permanently disabled buttons.

### History and Settings

History keeps jobs, versions and release status. Promotion/rollback/prune stay
explicitly outside this parity proposal until exact-plan backend contracts exist;
Qt parity does not require adding them. Include explanatory help at their location.

Context Settings owns **Defaults for future imports** and **Save current profile**;
database Settings shows effective values and their origin. Representation/profile,
model revision and contextualization affect output identity: changing them offers
reviewed creation of a separate compatible database/variant, not mutation of an
existing collection. Device can be Auto/CPU/CUDA when the backend supports it;
CUDA unavailability has an actionable error, never a silent device substitution.

Advanced identity during Create permits an explicit database ID and collection
only if catalog/backend validation supports that contract. Existing identities are
read-only. Until that contract is added, show the generated values with the reason
and the supported Legacy/CLI route. Likewise, keep the fixed Qwen profile visibly
fixed until alternate model/profile contracts exist; UI fields alone are not parity.

Maintenance contains the existing Review rebuild and exact removal review. Reuse
their staged execution and stale-preview checks. Do not restore overwrite-in-place
Generate or a global mirror-removals checkbox.

## 5. Settings & help

* **New database defaults**: output parent, asset filter/custom pattern, supported
  device and contextualization. Display that these affect future creations only.
  Effective precedence: explicit draft choice → saved context/database setting →
  application creation default. Defaults never rewrite existing database identity.
* **Environment**: readable CPU/GPU/CUDA readiness, Run diagnosis, Copy diagnostics,
  and expandable raw details. To close installation parity, add **Repair GPU
  support** with exact environment/command, install impact and **Install** review,
  then persistent progress and verification. Requires a supported launcher job
  contract. Until available, retain the explicit copy-command workflow.
* **Settings transfer**: Export settings with explicit Application defaults and/or
  Selected context/database scope. Import opens a file, validates schema and shows
  field-level old/new values and selected changes before Apply selected settings.
  Preserve unrelated operational values, reject incompatible identity changes and
  never replace runtime configuration wholesale. File browsing and merge/apply
  contracts are required. Legacy migration uses this same review where compatible.
* **Guide**: searchable task articles for create, update, source processing,
  selection, quality, recovery and settings. Contextual Help links open the relevant
  article, with a route back to the current draft. No first-run tutorial gate.

## 6. Shared behavior and accessibility

Use a single durable job presentation and single reusable report presentation.
Navigation never cancels jobs. Restore drafts/reviews after returning; stale reviews
must be refreshed before Apply. Disable duplicate submissions while requests start.
Job failure explains both recovery and whether the active database changed, using
reported backend state. Cancellation must state whether it is still available.

Use labeled native buttons/inputs; preserve keyboard focus after closing details;
announce status changes without moving focus. Errors appear beside their fields
and in a linked summary. Long paths wrap or have Copy; essential actions never
depend on hover. On narrow screens, rows become labeled stacked entries and detail
panels flow below content. Do not use icon-only primary actions or color-only status.

## 7. Implementation sequence and contracts

1. **Low-cost visibility and inspection:** real More menu, direct Open folder using
   the existing client method, copy controls, bulk selection, custom Create pattern,
   episode details, collection details, Guide. Extend report data where absent.
   Archive restore needs listing/filtering and a restore endpoint; archive alone
   does not provide it.
2. **Managed workspace:** add typed context/source/release records and bridge
   contracts for link/list/discover/status, archive/restore and active export mapping.
   Reuse startSourceAction only for operations its adapter actually supports.
   Add resumable producer jobs, persisted profile and context-to-create/review
   handoffs. Build Library and Source on those contracts.
3. **Quality:** typed standalone validation, prospective preview and deduplication
   policy/ledger report contracts, then the frozen-release redundancy workflow.
   Add preview/assess/pilot/resume/bundle/export/evaluate bridge methods and native
   file selection. Reuse job infrastructure; do not equate CLI existence with GUI
   readiness.
4. **Configuration parity:** scoped defaults, compatible device/contextualization
   changes, settings export/field-merge import and reviewed GPU installation.
   Identity/model freedom remains a deliberate contract decision requiring backend
   work. Document any deferred controls explicitly rather than marking them done.

Prefer extracting LibraryWorkspace, SourcePanel, QualityPanel, ReportView and
SettingsTransfer from the current large App.tsx. Reuse Create, Update, Content,
History, Maintenance and Activity rather than making parallel workflows. Ship
working slices; the mockup's sample state is not evidence of backend capability.

## 8. Acceptance checks

* A configured context with a newer release reaches an exact review in one action
  and starts applying in a second, excluding required specific acknowledgments.
* A linked context without a database is discoverable and can enter prefilled Create.
* Producer pending/running state and active database health are independently clear.
* Prepare, preview, assessment and validation never imply database activation.
* Open folder resolves the selected active export, not a stale last-job path.
* Filtered bulk selection states its scope; inspecting an episode leaves selection
  unchanged. All Qt episode metrics are present or explicitly unavailable.
* Archived contexts and database entries can be found and restored independently.
* Every redundancy operation listed in the comparison has a usable route and a
  durable result; release mismatch and interrupted-job recovery are tested.
* Settings import previews individual changes and preserves unrelated runtime
  values; representation changes cannot silently alter existing identity.
* Keyboard-only use completes create, update, source recovery and report export.
* Observe representative daily tasks with the operator before treating the assumed
  frequency ranking as settled. Keep rare workflows discoverable through labels
  and Guide, even when their controls are progressively disclosed.
