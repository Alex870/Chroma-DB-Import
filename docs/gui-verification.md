# Modern GUI verification

## Environment

- Canonical repository: `C:\temp\codex\Chroma DB Import`
- Python: Conda environment `chroma-db-import`, Python 3.13.12
- Node: v23.9.0
- npm: 10.9.2
- Frontend: React 19.1.1, Vite 7.1.5, TypeScript 5.9.2, Vitest 3.2.4
- Desktop dependency pin: `pywebview==6.2.1` in `desktop_requirements.txt`

## Automated evidence

Latest incremental frontend verification: `npm run typecheck`, Vitest (14 tests), `npm run test:e2e` (3 Playwright browser tests), and `npm run build` pass. The regressions cover structured source readiness counts, inspection-gated Prepare with automatic update-review handoff, a distinct CLI-only Redundancy area, field-level migration proposals, actual Activity totals/active-database state, maintenance/history detail for exact record IDs, affected episodes, reasons, recovery scope, and separate source/database releases, plus prescribed Update recovery actions for unavailable sources and profile mismatches.

From `frontend/`: `npm ci`, `npm run typecheck`, `npm run test -- --run` (14 tests), `npm run test:e2e` (3 Playwright browser tests), and `npm run build` passed. `npm ci --dry-run` also verified the locked dependency set. The build uses relative asset URLs and was copied to `src/chroma_db_import/desktop/assets`; the desktop build script clears generated Python packaging output first so repeated wheel builds cannot retain deleted frontend bundles. Settings exposes an explicit copyable repair command using the supported launcher; it does not install dependencies on page load.

From the repository: the current full suite runs 217 tests and passes 217/217. The focused modern backend suite passes 41/41, including immutable source-copy execution, under-lock stale-review rejection, target-content stale-review rejection, same-process and separate-process target-lock exclusion, legacy-export lock cooperation, real fake-provider folder promotion through temporary Chroma, retained-record totals, metadata-only updates with zero embedding-provider calls, compact episode inventory, queued cancellation, fresh-review retry after failed import, restart recovery evidence, activation transition evidence, managed registration inspection, managed creation through the real bridge/service queue, one-run selection baseline validation, packaged desktop-host asset/picker forwarding for paths with spaces and non-ASCII characters, close-guard/missing-assets coverage, app-ID/downstream-identity separation, managed profile-change stale-review rejection, queue-boundary validation of malformed bridge payloads, managed release dry-run inventory and exact-release execution pinning, managed retention blocking, stale-review rejection after database settings change, real-Chroma importer fingerprints, read-only migration proposals, malformed bridge/diagnostic payload handling, and the workflow package import-cycle regression. Frontend `npm run typecheck`, `npm run test -- --run` (14 tests), `npm run test:e2e` (3 Playwright browser tests), and `npm run build` pass. PowerShell parsing passes; `Run-ChromaDbImportUi.ps1 -Ui Modern -Workspace Default -NoLaunch`, `-Ui Modern -Workspace Contexts -NoLaunch`, and `-Ui Legacy -NoLaunch` pass dependency/CUDA checks in the existing Conda environment. A clean no-build-isolation wheel build passed and included exactly three current compiled `desktop/assets` files (`index.html`, one CSS bundle, and one JavaScript bundle).

## Desktop evidence

Latest backend rerun after managed bridge/service, desktop-host integration, one-run selection, exact deletion scope, profile compatibility, and workflow import-cycle coverage: focused GUI backend 41/41 and full repository 217/217. The managed-ready creation path is covered through the real bridge and service queue with draft persistence and reviewed Apply; the packaged host is covered for asset URL routing, picker forwarding, missing-assets setup messaging, and active-import close blocking. The source-connections UI now presents structured source readiness counts and warnings from inspection jobs while keeping active database health separate; the frontend regression suite covers this distinction.

The pywebview package is installed in the existing Conda environment at 6.2.1, and the modern module reaches its event loop. The freshly built wheel was installed into a disposable target without dependencies and launched with the source tree out of the import path; it resolved the three packaged assets and remained alive for five seconds as a native top-level window titled `Chroma DB Import` with a nonzero Windows window handle. No Node process was used. The available computer-use surface still exposed no targetable application window. Therefore the following are not claimed: local packaged asset startup as seen by a user, native folder picker, paths containing spaces and non-ASCII characters, 100%/150% scaling, responsive long jobs, and close behavior during an active import. The wheel contents and installed-copy launch prove packaging and process-level startup; runtime interaction without Node still needs a targetable desktop session.

## Launcher

- Modern UI: `scripts\Run-ChromaDbImportUi.ps1 -Ui Modern`
- Modern managed-source landing area: `scripts\Run-ChromaDbImportUi.ps1 -Ui Modern -Workspace Contexts`
- Legacy Qt UI: `scripts\Run-ChromaDbImportUi.ps1 -Ui Legacy`
- Root menu implementation selector: `Run Chroma DB Import.ps1 -Ui Modern` or `-Ui Legacy`

## Runtime propagation verification — 2026-09-12

The exact changed-file set was copied from the canonical repository to `D:\Pod Cast RAG\Chroma DB Import`. The copy explicitly excluded `state`, runtime configuration, dependencies, and generated directories. Hash checks confirmed that D: `state\context_catalog.sqlite3` and `state\ui_state.json` were unchanged.

On D:, the focused GUI backend suite passed 41/41, frontend Vitest passed 14/14, Playwright passed 3/3, and the packaged Modern build passed after installing the locked frontend dependencies. The Modern module created a real native `Chroma DB Import` window with a nonzero Windows handle when pointed at a disposable GUI state directory. The available computer-use surface still exposed no targetable native window, so interactive folder picking, scaling, responsive long jobs, and close behavior remain unverified.

The copied source now includes the default state-directory fallback: explicit state paths remain supported, and an unwritable project state path falls back to `%LOCALAPPDATA%\Chroma DB Import\gui`. The canonical suite passes 218/218; the D: focused suite passes 42/42, including the fallback test. The D: protected `state\context_catalog.sqlite3` and `state\ui_state.json` hashes remained unchanged. A fresh wheel verification passed with `desktop/window.py` and exactly three packaged desktop assets. Native window creation is verified at process/handle level; click-through behavior remains pending a targetable Windows computer-use surface.

Bootstrap verification: the copied root launcher now defaults to Modern. Running its option-2 equivalent (`-Action RunUi` without an explicit `-Ui`) created a native `Chroma DB Import` window whose process command line was `python -m chroma_db_import.desktop`; explicit `-Ui Legacy` remains the compatibility path. The old Qt screenshot matches the prior Legacy default and is no longer the default bootstrap route.

Edge file-URI fix verification: the copied D: host served the Modern entrypoint through pywebview’s loopback server. WebView2 reported `http://127.0.0.1:<port>/index.html?workspace=contexts`; the HTML, JavaScript, and CSS all returned HTTP 200. This replaces the broken `file:///.../index.html%3Fworkspace=...` navigation. The canonical suite passes 219/219 and the D: focused GUI suite passes 43/43.

Desktop bridge and text-copy verification: pywebview 6.2.1’s default `text_select=False` was overridden with `text_select=True` for both the normal Modern window and the setup-error window. The frontend client now waits for the host’s `pywebviewready` event and rejects only after a five-second bounded timeout when the bridge truly is absent; it also handles an API object that has been injected but not populated yet. C: and D: focused GUI backend suites pass 43/43, frontend Vitest passes 14/14, Playwright passes 3/3, and both frontend typechecks pass. The rebuilt JavaScript bundle and source changes are hash-identical between C: and D:; protected D: state hashes remain unchanged.
