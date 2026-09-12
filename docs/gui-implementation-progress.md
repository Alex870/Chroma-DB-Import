# GUI implementation progress

This log records actual checks from the canonical repository. It is intentionally candid about gates that require a Windows webview or GPU/runtime data.

## Task 01

Status: PASS

Files changed: `tests/fixtures/gui/`, `tests/test_gui_*.py`, this log.

Checks run and actual results: The package was installed editable into the existing `chroma-db-import` Conda environment because the initial baseline could not import `chroma_db_import`. The corrected baseline ran 176 tests: 175 passed and 1 failed. The current full suite runs 214 tests and passes 214/214. The reconciliation integration fixture now uses the sole pinned Qwen3 representation, read-only Chroma inspections now close only systems they own, and folder promotion closes its owned staging Chroma system before the Windows move.

Manual checks and actual observations: Synthetic fixtures cover two episodes, two speakers, shared context, changed source bytes, and existing-target refusal.

Unresolved issue, if any: The baseline reconciliation failure is not introduced by the modern UI files and remains to be reviewed separately.

Next task: Task 02 and Task 03.

## Task 02

Status: PASS for the frontend build and dependency installation; BLOCKED for actual pywebview Windows execution until a real window is exercised.

Files changed: `frontend/`, `desktop_requirements.txt`, `scripts/Build-ChromaDbImportDesktop.ps1`, `src/chroma_db_import/desktop/`.

Checks run and actual results: `npm ci`, `npm run typecheck`, `npm run test -- --run`, `npm run test:e2e`, and `npm run build` all passed. The frontend unit/journey suite contains 5 Vitest tests, and the Playwright browser suite contains 3 tests covering the three-step creation path, managed-workspace landing, and missing-bridge behavior. The allowlisted bridge also has JSON echo and bounded synthetic-progress diagnostics, exposed under Settings & help. Vite emitted a relative-path production bundle. The Python bridge has a visible missing-assets/missing-pywebview startup message.

Manual checks and actual observations: No real Windows webview, folder picker, scaling, or close-interception run has been claimed in this environment.

Unresolved issue, if any: The package is installed in the existing Conda environment, but the available computer-use surface did not expose a targetable webview window. Run the packaged window on Windows before switching the launcher default for users.

Next task: Task 03.

## Task 03–09

Status: IN PROGRESS

Files changed: `src/chroma_db_import/workflow/`, `src/chroma_db_import/ui_models.py`, `src/chroma_db_import/ui_export.py`, `src/chroma_db_import/desktop/`.

Checks run and actual results: The workflow modules compile and import. The focused GUI backend suite passes 38/38. It covers catalog restart/duplicate-target/schema handling, selection semantics, immutable source snapshots/stale previews, under-lock stale database settings rejection, real-Chroma importer fingerprints, retained-record totals, metadata-only reconciliation with zero embedding-provider calls, read-only migration proposals, duplicate Apply reuse, same-process and separate-process target locks, interrupted jobs, activation-journal transition evidence, queued-job cancellation, fresh-review retry after failed import, compact episode inventory without document text, managed registration inspection, app-ID/downstream-identity separation, managed profile-change stale-review rejection, malformed bridge payload validation at queue time, bridge echo/diagnostic validation, bridge error envelopes, real fake-provider folder promotion, managed release dry-run/execution pinning plus retention blocking against the existing `ManagedCatalog` and `run_managed_import` contracts, managed creation through the real bridge/service queue with draft persistence and reviewed Apply, one-run selection baseline validation, and packaged desktop-host asset/picker/close-guard/missing-assets behavior.

Manual checks and actual observations: The modern UI calls only the explicit bridge methods. The mock client is selected only by `VITE_USE_MOCK_BRIDGE=true`; production without the bridge shows an error instead of fixture data.

Unresolved issue, if any: Folder execution still delegates model loading and Chroma writes to the existing importer; a targetable Windows webview acceptance run remains before the final launcher cutover. Managed normal updates that would remove active records remain explicitly blocked for an unsupported retention contract.

Next task: Run focused backend tests, then complete maintenance/analysis parity and packaging evidence.

## Task 10–16

Status: IN PROGRESS

Files changed: `frontend/`, launcher scripts, package/build metadata, `docs/gui-specialist-parity.md`.

Checks run and actual results: The library, activity, create, add-existing, update-review, content selection, maintenance, source-connections, settings, and CLI-only advisory-analysis disposition are present in the React application. `npm run typecheck`, Vitest (6 tests), Playwright (3 browser tests), and Vite build pass. Creation draft persistence is covered by a React Testing Library journey, including reuse of the saved draft ID, native destination-folder selection, existing-target routing, the managed workspace landing area, the explicit supported-launcher repair command, and the non-persistent one-run update selection editor. Queued activity cancellation is exposed only while a job is still cancellable. Modern and Legacy launcher `-NoLaunch` checks pass, including CUDA diagnosis in the existing environment. A clean no-dependency wheel build passed with `--no-build-isolation`, and the wheel contains exactly the three current compiled desktop assets (`index.html`, one CSS bundle, and one JavaScript bundle); the desktop build script clears generated Python packaging output before rebuilding. Full automated acceptance passes; the targetable real desktop gate remains pending.

Manual checks and actual observations: Legacy Qt remains available through `-Ui Legacy`; the modern path is selected by `-Ui Modern`. Modern `-Workspace Contexts` now opens Source connections, while the default workspace opens the database library.

Unresolved issue, if any: The Windows computer-use surface exposed no targetable pywebview window after the local event loop started, so folder picker/scaling/close behavior is not claimed as verified. Runtime propagation to `D:\Pod Cast RAG\Chroma DB Import` has not been performed.

Next task: Complete the targetable Windows webview acceptance gate, then ask for the required runtime-copy decision.

## Final automated audit — 2026-09-07

Status: PASS for the available automated and packaged-build checks; BLOCKED only for the targetable Windows pywebview acceptance gate.

Checks run and actual results: `conda run --no-capture-output -n chroma-db-import python -m unittest discover -s tests -p "test_gui_*.py" -v` passed 38/38. The full repository suite passed 214/214. From `frontend/`, `npm run typecheck`, `npm run test -- --run` (12 tests), `npm run test:e2e` (3 Playwright browser tests), and `npm run build` passed. A clean `npm ci --dry-run` verified the locked frontend dependencies. A clean `--no-build-isolation` wheel build passed and the wheel contained exactly 3 desktop assets. Modern Default, Modern Contexts, and Legacy `-NoLaunch` smoke checks all passed dependency and CUDA diagnosis in the existing Conda environment: RTX 5070 Ti, torch 2.11.0+cu128, CUDA 12.8, one available device. The audit also added real managed-registration inspection coverage, managed creation through the real bridge/service queue, packaged desktop-host routing, close-guard, and missing-assets coverage, the explicit supported-launcher repair action, one-run selection baseline validation, app-ID/downstream-identity separation, managed profile-change stale-review coverage, source/target stale-review checks, episode/date/excluded-file scan details, persisted create-flow content selection, managed Add-existing support, representation-aware maintenance deletion, OS-backed cross-process and legacy-export writer-lock coverage, queue-boundary validation for malformed bridge payloads, and fresh-review retry after failed import.

Manual checks and actual observations: The computer-use surface still exposed no targetable pywebview window, so actual packaged-window interaction, native folder picking, scaling, close interception, and launch-without-Node remain unverified. The canonical repository is unchanged by any runtime-copy operation.

Unresolved issue, if any: Legacy remains the default launcher because the plan explicitly requires a successful real webview gate before cutover. Runtime propagation to `D:\Pod Cast RAG\Chroma DB Import` also remains intentionally untouched pending the user's decision.

## Per-task completion audit

Latest incremental frontend verification on 2026-09-08: TypeScript, Vitest (14 tests), Playwright (3 browser tests), and Vite build pass. The managed source-connections UI now renders structured readiness counts, active release, and warnings from source jobs while keeping active database health separate; React regressions cover the distinction, inspection gating, automatic handoff to update review, the distinct CLI-only Redundancy area, field-level migration proposals, actual Activity totals/active-database state, maintenance/history detail for exact record IDs, affected episodes, reasons, recovery scope, and separate source/database releases, plus prescribed Update recovery actions for unavailable sources and profile mismatches with source/target/profile compatibility details.

Final automated rerun on 2026-09-08: the full Conda suite passed 217/217, including the focused GUI backend suite at 41/41; the real-Chroma removal-preview and representation-profile mismatch tests passed; the workflow package import-cycle regression passed in a fresh process; Modern Default, Modern Contexts, and Legacy `-NoLaunch` launcher checks passed dependency validation; CUDA diagnosis reported the RTX 5070 Ti available; the production bundle was rebuilt, copied into desktop assets, and included in a wheel with exactly 3 packaged assets. The native desktop acceptance gate was checked again, but the available Windows computer-use surface exposed no targetable native application, so folder-picker, scaling, close-interception, and packaged-window interaction remain unverified.

Latest rerun on 2026-09-08 after the desktop build cleanup, Unicode picker fixture, one-run update selection, exact deletion scope, profile compatibility, and import-cycle fix: focused GUI backend 41/41. The broader evidence includes a managed-ready draft saved through the real bridge, a queued service preview, reviewed Apply, separate application/downstream identities, packaged asset URL routing, native picker forwarding for spaces and non-ASCII paths, missing-assets setup messaging, close blocking during active imports, and a five-second native-window launch from the installed wheel with no Node process.

This matrix gives each numbered task an explicit status and points to the evidence above or to the focused checks that support it.

| Task | Status | Evidence / limitation |
| --- | --- | --- |
| 01 | PASS | Baseline, synthetic fixtures, fixture variants, and expected-effect coverage are recorded above; the latest full suite passes 216/216. |
| 02 | BLOCKED | React/Vite, pinned pywebview, local assets, bridge shell, Playwright browser journeys, and process-level native-window creation pass. The targetable native webview surface is unavailable for folder-picker, scaling, close, and packaged-window interaction. |
| 03 | PASS | Versioned SQLite catalog, DTO validation, restart/rename/archive behavior, target normalization, and newer-schema rejection are covered by `tests/test_gui_catalog.py`. |
| 04 | PASS | Read-only folder/managed inspection, migration proposals, identity preservation, and selection-policy behavior are covered by bridge, planning, and managed workflow tests. |
| 05 | PASS | Explicit final/staging destinations, existing-target refusal, stable sibling locks, and staged promotion are exercised by folder execution and job tests. |
| 06 | PASS | Frozen source snapshots, exact effect categories, retained records, metadata-only/no-op behavior, real-Chroma fake-provider execution, and stale target/source rejection pass in the focused suite. |
| 07 | PASS with documented limitation | Exact managed release/profile pinning, partition isolation, readiness separation, dedup evidence, and full-version removal blocking pass. Unsupported managed retention remains visibly blocked rather than silently applied. |
| 08 | PASS | Durable queue/recovery, duplicate Apply reuse, OS-backed same/separate-process locks, activation journals, queued cancellation, and fresh-review retry pass in `tests/test_gui_jobs.py` and `tests/test_gui_recovery.py`. Legacy export lock cooperation is also covered. |
| 09 | PASS | Allowlisted bridge methods, strict queue-boundary validation, version handshake, safe error envelopes, pagination, and missing-bridge behavior are covered by Python and Playwright tests. |
| 10 | PASS | Navigation, library, activity, status/error/empty states, archive wording, and browser-tested first-run behavior are present in `frontend/src/App.tsx`. |
| 11 | PASS | Folder creation, managed workspace landing, draft persistence, scan revisions, existing-target routing, and frontend preview/Apply journeys are covered by Vitest and Playwright; managed-ready creation also runs through the real bridge/service queue and reviewed Apply in `tests/test_gui_managed_workflow.py`. |
| 12 | PASS | Remembered update review, no-op handling, exact effects, stale review checks, Apply gating, and a non-persistent one-run content selection review are implemented in the update flow and backend planning tests. |
| 13 | PASS | Content selection, episode overrides, preview-scoped selection, settings revisions, structured source readiness counts distinct from active database health, source actions, environment diagnosis, and read-only migration presentation are implemented and tested at the service/UI boundary. |
| 14 | PASS with documented CLI-only operations | Maintenance review, exact deletion acknowledgment, retained-missing semantics, managed replacement blocking, history, and explicit CLI-only release administration are recorded in the parity inventory. |
| 15 | PASS with documented CLI-only operations | Advisory redundancy analysis remains version-bound and CLI-only; the specialist parity inventory gives every existing operation an implemented location or explicit disposition. |
| 16 | BLOCKED | Packaging, wheel asset verification, locked dependency installation, launcher preservation, and smoke checks pass. Final cutover remains intentionally blocked until Task 02's targetable Windows webview acceptance gate is completed. |

## Runtime propagation verification — 2026-09-12

The exact changed-file set from the canonical repository was propagated to `D:\Pod Cast RAG\Chroma DB Import` for validation. The copy excluded `state`, runtime configuration, dependencies, and generated build/test directories; D: `state\context_catalog.sqlite3` and `state\ui_state.json` were hash-checked before and after propagation and remained unchanged.

Checks from D: after propagation: the focused GUI backend suite passed 41/41; the frontend typecheck passed; Vitest passed 14/14; Playwright passed 3/3; the Modern production build passed and refreshed the packaged desktop assets. A broad D: repository run was started but was not used as acceptance evidence because it encountered pre-existing environment failures before the UI sections and then stopped producing progress; the canonical C: run remains clean at 217/217.

The copied Modern package reached a real native window titled `Chroma DB Import` with a nonzero Windows handle when launched against a disposable GUI state directory. The available computer-use surface exposed no targetable app, so native click-through, folder-picker, scaling, and close-interception behavior remain unverified. No runtime configuration was changed.

The follow-up state-directory fix is also present in both trees: an explicit `--state-dir` remains honored, while the default path falls back to `%LOCALAPPDATA%\Chroma DB Import\gui` if the runtime checkout cannot accept writes. The canonical full suite now passes 218/218 and the copied D: focused GUI suite passes 42/42, including this fallback regression. A fresh disposable-output wheel build also contains `desktop/window.py` and exactly the three current packaged assets. The D: native smoke path created separate GUI state without changing the protected catalog or UI-state files; direct interactive acceptance remains blocked only by the unavailable targetable computer-use surface.

Bootstrap cutover verification: the root `Run Chroma DB Import.ps1` now defaults to `-Ui Modern`, so menu option 2 launches `python -m chroma_db_import.desktop` on D:. The explicit `-Ui Legacy` path remains available. A D: smoke run produced a native `Chroma DB Import` window with a nonzero handle; the attached old Qt appearance was explained by the previous Legacy default, not by stale frontend assets.

Edge file-URI fix verification: the Modern host now passes a plain local asset path to pywebview, allowing its loopback server to serve `index.html` and its bundles. Workspace selection is applied after initialization, avoiding WebView2’s conversion of `?workspace=...` into a `%3F` filename. D: WebView2 inspection returned `http://127.0.0.1:<port>/index.html?workspace=contexts` with HTTP 200 for HTML, JavaScript, and CSS. The canonical suite passes 219/219 and the copied D: GUI suite passes 43/43.

Desktop bridge and text-copy fix verification: pywebview 6.2.1 defaults to disabling selection and injects its API asynchronously. The Modern host now passes `text_select=True`, so ordinary UI text can be selected and copied with the native window’s standard copy command. The frontend waits for pywebview’s `pywebviewready` event before calling the bridge, detects partial injection, and retries after a bounded timeout instead of showing a transient connection banner. C: and D: focused GUI suites pass 43/43; frontend typecheck, Vitest 14/14, and Playwright 3/3 pass after the fix. The rebuilt bundle was propagated to D: without changing protected runtime state.
