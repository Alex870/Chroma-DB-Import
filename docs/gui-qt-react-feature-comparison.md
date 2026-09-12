# Qt versus React UI feature comparison

Date: 2026-09-12

This comparison is based on the actual controls and handlers in the existing Qt
window (`src/chroma_db_import/ui_window.py`) and the current React application
(`frontend/src/App.tsx`). “Replaced” means the React UI intentionally uses a
different, usually safer workflow; it does not mean the old button exists under
another label. “Partial” means some of the old behavior is available, but an
important part is absent or only available indirectly.

## Executive summary

The React UI currently provides a usable database library, three-step creation,
existing-database registration, remembered database settings, content selection,
reviewed updates, staged folder rebuilds, exact removal review, activity history,
source inspection/preparation, environment diagnosis, and migration inspection.

The largest gaps compared with Qt are:

- Managed Contexts is not a context browser/workspace. It is currently a small
  source-root/partition form with Inspect and Prepare actions.
- Redundancy analysis is a CLI-only placeholder in React; Qt exposes its policy,
  channels, judge pilot, resume, bundle review, label export, and evaluation.
- The Qt global settings surface is not fully represented: output defaults,
  database ID, collection, embedding device/model controls, contextualization,
  direct CUDA installation, complete settings save/load, and the in-app guide
  are absent or redirected.
- Several specialist Qt inspection tools—folder preview, episode detail
  metrics, standalone validation, collection information, and direct Open
  Export—are missing or only partially represented.

## Core workflow comparison

| Capability | Qt UI | React UI | Status |
| --- | --- | --- | --- |
| Open processed folder | Toolbar **Open** opens a folder-preview dialog with suggested locations, a folder tree, sample files, metrics, and an explicit Open action. | Create flow has a folder picker and backend source scan; it shows counts, date range, speakers, and excluded files. | Partial |
| Suggested source locations | Offers likely `processed_data` locations and a folder browser. | Not shown. User must type or choose a folder. | Missing |
| Create a new database | Global settings are filled first, then **Generate** runs the import. Existing output can be deleted after a confirmation. | Three-step Content → Location → Review flow. Creation refuses to overwrite and offers Use existing or Choose another location. | Implemented, redesigned |
| Add/register existing database | No equivalent first-class library registration flow. | **Add existing** performs read-only inspection, then registration without moving or replacing files. | React addition |
| Database identity fields | Editable podcast name, database ID, and collection name. | Editable display name; ID and collection are generated/read-only technical details. | Partial / changed contract |
| Output location | Separate toolbar **Output** chooses a parent output folder and persists it globally. | Storage location is selected inside Create; no standalone application output setting. | Partial |
| Asset filter | Global reviewed/cleaned/raw/custom filter; changing it reloads the asset list. | Available during Create and database settings; custom pattern is available after creation and during update selection, but not as a Create-step option. | Partial |
| Embedding profile | Shows/selects representation profile and exposes model, revision, and device fields. | Shows the fixed Qwen3 Embedding 4B profile and checks readiness; model/profile/device are not editable in the React flow. | Partial / constrained |
| Embedding device | Auto/CPU/CUDA device selector. | Environment diagnosis reports CUDA readiness; no device selector. | Missing |
| Contextualization | Explicit `minimal`, `full`, and `none` setting. | No React control or displayed setting. | Missing |
| Speaker selection | Global speaker list with tri-state checkboxes plus **Select All**/**Select None**; per-episode speaker controls also have Select All/None. | Allowlist/exclusion fields and per-episode checkboxes/overrides; no Select All/Select None controls. | Partial |
| Episode inspection | Shows title, date, source file, document count, leaf chunks, position cards, cluster summaries, thesis count, included documents, and speakers. | Content view shows title/ID, record count, speakers, inclusion, and override; it does not show source file/date/node breakdowns or included-document count. | Partial |
| Save settings | **Save Settings** writes a complete settings JSON chosen through a file dialog. | Automatic scoped catalog/draft persistence; no modern complete settings export. | Partial / redesigned |
| Load settings | **Load Settings** restores a complete settings JSON through a file dialog. | Migration candidates can be inspected, but complete settings import remains directed to the Legacy UI/CLI path. | Missing in React |
| GPU details | **GPU Details** opens a dedicated diagnostic message. | **Run environment diagnosis** displays a JSON report. | Implemented, redesigned |
| CUDA installation | Qt has an **Install CUDA PyTorch** button with progress and completion/failure handling. | Offers a copyable repair command; installation is not performed from React. | Partial / intentionally externalized |
| Workflow guide | **Guide** opens an in-app workflow guide. | Explanatory copy exists across screens; no equivalent guide view. | Missing |
| Copyable diagnostics | Qt uses copyable message dialogs for selected diagnostics and reports. | Normal text is now selectable/copyable; the repair command has an explicit copy action. There are no general Copy buttons for technical fields/reports. | Partial |

## Import, update, and maintenance comparison

| Capability | Qt UI | React UI | Status |
| --- | --- | --- | --- |
| Generate/rebuild | **Generate** is a toolbar action and can delete/rebuild an existing export after confirmation. | Create never overwrites; folder rebuild is under Database → Maintenance → Review rebuild with staging and review. | Implemented, safer replacement |
| Update | Builds a plan from the current global fields, previews added/changed/metadata-only/unchanged/removed records, then uses a confirmation dialog. | Library → Update uses remembered source/target/profile, a persisted preview, effect counts, findings, and **Apply update**. | Implemented, redesigned |
| Dry Run | Standalone **Dry Run** dialog shows eligibility, validation, reconciliation, embedding, staging, dates, errors, and warnings. | Folded into Create/Update/Maintenance review; no standalone Dry Run screen/report with the Qt fields. | Replaced |
| Validate | Standalone **Validate** action creates a processed-cache and metadata validation report. | Validation findings appear in previews; Settings has environment diagnosis. No dedicated source/database validation workflow. | Missing / partial |
| Reconcile/mirror removals | Toolbar **Reconcile** and a global **Mirror records removed from source caches** checkbox can schedule deletions. | Ordinary Update retains missing records; Maintenance → removal review shows exact IDs, episodes, and reasons before Apply. | Implemented, safer replacement |
| Progress and logs | Progress bar and import log remain on the current Qt panel. | Persistent job strip, Activity page, job states, actual totals, warnings, retry/cancel, expandable event logs, and report export. | Implemented, improved |
| Open export | Toolbar **Open Export** opens the selected export folder directly in Explorer. | Activity can open a known database folder after a job; Database detail has no direct equivalent button. | Partial |
| Collection information | **Collection Info** reads `import_manifest.json` and `podcast.json`, showing manifest/importer versions, model, dimension, source-file count, metadata validity, and episode count. | Overview/History show target, profile, database identity, revision, release IDs, active state, and stored episodes; manifest/podcast validation details are not displayed. | Partial |
| Existing output protection | Generate prompts to delete an existing export. | Create refuses overwrite and explicitly routes to register existing or choose another location. | Implemented, changed behavior |
| Archive/restore database | Qt has archive/restore for managed contexts. | Library archive hides a catalog entry while retaining files; no visible restore/list-archived flow is present. | Partial |

## Managed Contexts comparison

| Capability | Qt Contexts workspace | React UI | Status |
| --- | --- | --- | --- |
| Link source root | **Link Source Root** chooses and persists a Podcast-RAG project/release root. | User types a source root into Source connections or Create; no link/persist/discover control. | Missing |
| Discover contexts | **Discover / Refresh** scans the source and populates a context list. | No context list or discovery action. | Missing |
| Select context | Context list shows independent podcast/meeting contexts and details. | No managed-context browser; a database must already be selected elsewhere. | Missing |
| Refresh source status | **Refresh Source Status** reads producer status for the selected context. | **Inspect source** reads a manually supplied root/partition and shows readiness counts. | Partial |
| Resume producer processing | Qt context workflow supports producer status/resume behavior through the managed worker and gives pending-work guidance. | React text mentions resuming, but there is no Resume source-processing control. | Missing |
| Prepare and import latest | Qt **Prepare and Import Latest** prepares a release, previews it, asks for confirmation, then imports/activates it. | React **Prepare source release** prepares the source and returns to database review only when an existing database is selected; no context-level import-latest action. | Partial |
| Import newest release | Qt has **Import Newest Release** as a separate action. | No equivalent control. | Missing |
| Save current profile | Qt saves the current embedding/representation profile for the selected context. | No managed-context profile-save control. | Missing |
| Open active database | Qt opens the selected context’s active release export. | Activity can open a known registered database folder; no context-level active-release action. | Partial |
| Archive/restore context | Qt **Archive / Restore** toggles local context status. | Library archive hides database entries; no context archive/restore workspace. | Partial |
| Deduplication settings | Qt exposes profile, near-match/retrieval toggles, thresholds, and block limits. | No managed-context deduplication controls. | Missing |
| Review deduplication | Qt **Review Deduplication** validates and displays release-ledger groups, reasons, and coverage. | No equivalent React screen or action. | Missing |
| Preview next import | Qt computes a model-free prospective deduplication preview. | No equivalent context action. | Missing |
| Source and database status separation | Qt shows producer status, release, and active database context details together. | React source screen explicitly separates source readiness from active database health. | Implemented, narrower scope |

## Redundancy and specialist tools

| Capability | Qt UI | React UI | Status |
| --- | --- | --- | --- |
| Select frozen release | Qt Redundancy screen selects the managed partition/release. | Redundancy page is labeled CLI-only; no selector. | Missing |
| Configure redundancy policy | Qt exposes vector storage, retrieval mode, judge enablement/fraction/cap, and candidate channels. | No controls. | Missing |
| Configure judge | Qt saves LM Studio URL, model, and optional model fingerprint. | No controls. | Missing |
| Preview coverage | Qt **Preview** launches a model-free operation. | No control. | Missing |
| Assess channels | Qt **Assess** runs selected lexical/structural/dense channels. | No control. | Missing |
| Judge pilot | Qt **Pilot Judge** performs an explicitly enabled bounded pilot. | No control. | Missing |
| Resume assessment | Qt accepts a frozen job ID and **Resume**. | No control. | Missing |
| Review bundle | Qt opens and validates a redundancy bundle. | No control. | Missing |
| Export labels | Qt exports label examples from a bundle. | No control. | Missing |
| Evaluate bundle | Qt evaluates reviewed labels, optional queries/results, and writes a report. | No control. | Missing |
| Standalone reports/validation | Qt exposes validation and collection reports directly from the toolbar. | React relies on preview findings, Activity report export, and CLI instructions. | Partial |
| Release administration | Qt does not expose a complete promotion/rollback/prune GUI contract. | React documents release status/promotion/rollback/prune as CLI-only in History. | Comparable limitation |

## Bottom line

The React UI is not yet a feature-for-feature replacement for the Qt UI. It is
a redesigned core database manager with several deliberate safety changes, but
it is not yet a complete replacement for the specialist/operator surface.

The highest-priority parity work, if React is intended to replace Qt for daily
operations, is:

1. Build a real Managed Contexts browser with link/discover/select, refresh,
   resume, import-newest, profile, archive/restore, dedup settings, dedup
   review, and next-import preview.
2. Implement the Redundancy workflow in React rather than displaying a CLI-only
   placeholder.
3. Restore specialist inspection tools: processed-folder preview, episode
   details, standalone validation, collection information, direct open export,
   and a real Guide view.
4. Decide which global settings remain intentionally fixed by the new catalog
   contract and either expose the rest—device, contextualization, output
   defaults, settings import/export—or document them as explicit CLI/Legacy
   operations.

The existing specialist disposition document records several of these areas as
“CLI-only” or “implemented” at the backend/contract level. This comparison is
more conservative: it reports whether the current React screen actually gives
the operator a usable control for the capability.
