# React UI implementation progress

Implementation guide: `docs/gui-react-ui-implementation.md`  
Authority: `docs/gui-react-ui-design.md`  
Repository: `C:\temp\codex\Chroma DB Import`  
Runtime copy: intentionally untouched.

## P00 — Establish baseline and progress log

Task: P00
Status: PASS
Files changed: `docs/gui-react-ui-progress.md`
Backend/bridge implementation: None; baseline inspection only.
Frontend implementation: Existing React/Vite application inspected.
Checks run and exact result:
- `.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_gui_*.py'`: 43 tests, OK.
- `frontend/npm run typecheck`: passed.
- `frontend/npm run test -- --run`: 2 files, 14 tests, passed.
- `frontend/npm run test:e2e`: build passed; 3 Playwright tests passed.
- `.venv/Scripts/python.exe` exists; `frontend/node_modules` and `frontend/package-lock.json` are present.
Mocked boundaries: Existing Playwright fixture bridge only; no production mock mode enabled.
Manual/native checks actually performed: None.
Audit IDs covered: Initial disposition recorded below; implementation evidence is added per task.
Open issue or next unfinished step: P01.

## Long-operation feedback and semantic judge workflow — 2026-09-13

Status: PASS for the visible workflow and packaged React bundle.

The Redundancy analysis area now exposes a clearly named `Redundancy analysis · semantic judge` navigation entry. Its Semantic judge tab provides per-context enablement, loopback OpenAI-compatible endpoint and exact served-model configuration, request timeout, sampling/call/neighbor/job limits, endpoint probing against `/v1/models`, and explicit save → frozen review → confirmed pilot execution. Judge execution remains optional; ordinary database creation and model-free analysis do not require a generative LLM.

Long-running bridge calls now publish a shared operation status in the UI immediately. Queued jobs remain visible through completion, show the current stage/message, show elapsed time after five seconds, and display a percentage when backend events provide current/total work. Job progress is persisted in the GUI catalog and survives restart. Redundancy judge calls report bounded call progress through the same status channel.

Checks: Conda-backed full Python suite 232/232; frontend typecheck passed; Vitest 17/17; Playwright E2E 3/3; Vite production build passed; packaged assets refreshed; `git diff --check` passed. Verified source and packaged UI changes were synchronized to the D: runtime copy with runtime state/configuration excluded.

Open issue: native interactive verification of the Windows window remains outside the automated test environment.

## P01–P23 — Task register

| Task | Status | Audit IDs | Evidence / next step |
| --- | --- | --- | --- |
| P01 | PASS | G08, E08, G21, R17–R18 | Report model/view, capability handshake, typed bridge additions; full suite green. |
| P02 | PASS | G07–G08, G29 | Archive/restore, More menu, strict active-export resolver; GUI suite green. |
| P03 | PASS | G11, G13, G28 | Database details report with identity/representation findings. |
| P04 | PASS | G14–G15, E01–E05 | Paginated source/stored inventory and source-unavailable reporting. |
| P05 | PASS | E06–E07 | Speaker/episode editor, explicit empty allowlist, nullable metrics. |
| P06 | PASS | G01–G05, G09–G10 | Folder suggestions, bounded browser, field-level folder report. |
| P07 | PASS | G06, G11–G13, E07 | App/database/context execution options with optimistic revisions. |
| P08 | PASS | M01–M09 | Owned source connections and managed context/release inventory. |
| P09 | PASS | M14–M16 | Two-pane source browser with independent context archive state. |
| P10 | PASS | M10–M12 | Linked context inspect/prepare actions use explicit context identity; producer Resume remains intentionally excluded per the guide. |
| P11 | PASS | M13, E07 | Modern policy is applied before managed dedup/planning and included in release identity; focused contract test. |
| P12 | PASS | M17–M20 | Scoped policy save/read, prospective no-model preview, active strict-export review, and report controls are wired. |
| P13 | PASS | G22–G28 | Standalone validation and durable report paths are wired; full suite green. |
| P14 | PASS | R01–R06, R17–R18 | Allowlisted redundancy actions, read-only settings, scope-bound reports. |
| P15 | PASS | R07–R09 | Context/release selectors, model-free coverage preview, report view. |
| P16 | PARTIAL | R11–R13 | Durable advisory jobs and retry classification exist; frozen assessment resume/cancel parity remains. |
| P17 | PARTIAL | R04, R10 | Judge config save, immutable export/policy/channel review, backend revalidation, and explicit pilot UI/run are wired; live LM Studio/fake-judge acceptance remains not run. |
| P18 | PASS | R14–R16 | Bundle picker/validation, bounded label export, scope-bound evaluation, optional query inputs, and durable reports are wired; fixture integration covers successful label/evaluation execution. |
| P19 | PASS | G16, E08 | Native pywebview open/save pickers are injected with purpose-specific filters; report copy and scoped transfer contracts are implemented. |
| P20 | PARTIAL | G16 | Field-scoped proposal/apply with stale re-read, context transaction, and conservative legacy normalization are implemented; unique legacy speaker-fingerprint mapping remains intentionally unresolved. |
| P21 | PASS | G17–G18 | Backend allowlisted launcher review/apply job with fake-runner coverage; native installer gate not run. |
| P22 | PASS | G19–G20 | Guide/search, accessible labels, mixed checkbox behavior and responsive styles added; manual 200% check pending. |
| P23 | PARTIAL | all | Full suite, focused suite, typecheck, package asset build, and browser E2E pass; native pywebview interaction and 200% visual checks remain not run. |

## Initial audit disposition

All 75 audit IDs are assigned exactly once according to section 7 of the guide.
Existing prototype coverage is treated as unverified until backed by the Python
bridge/service and targeted tests; no placeholder is marked complete by inheritance.

Runtime follow-up on 2026-09-12: Option 2 was reproduced against the D: runtime.
The host failed before bridge initialization with
`sqlite3.OperationalError: attempt to write a readonly database` while opening
`state/gui/gui_catalog.sqlite3`; the React error was the downstream symptom.
The Modern host now performs a non-mutating SQLite write/lock probe and falls
back to per-user GUI state when the runtime checkout cannot host its catalog.
The C: codebase, packaged assets, scripts, docs, tests, and frontend sources
were synchronized to D: with `.git`, environments, caches, `state`, and runtime
configuration excluded. A code-directory hash audit reports zero missing files
and zero mismatches; D: handshake smoke returned `api_version=gui-api-v1`.

Bridge follow-up on 2026-09-12: pywebview invokes the JavaScript API with one
payload argument even for no-argument methods. `ApplicationBridge.handshake`
now accepts that optional payload, eliminating the observed
`TypeError: ... handshake() takes 1 positional argument but 2 were given`.
The exact `handshake({})` call is covered by the bridge regression test and was
rechecked against the synchronized D: runtime.

Baseline verification before producer-aware tracking: full Python suite 226/226, focused GUI suite 50/50,
focused contract suite 6/6, frontend typecheck passed, Vitest 14/14, and
Playwright E2E 3/3. No commit or push was performed.

## Producer-aware database tracking delta — 2026-09-12

Task: Producer-aware database tracking
Status: PASS
Files changed: `workflow/catalog.py`, `workflow/reconciliation.py`, `workflow/sources.py`, `workflow/service.py`, `workflow/managed_adapter.py`, `workflow/jobs.py`, `desktop/bridge.py`, `frontend/src/App.tsx`, `frontend/src/pages/SourceConnections.tsx`, API types/clients, tests, and generated desktop assets.
Backend/bridge implementation: Additive SQLite persistence now stores source observations, explicit database links, and downstream release history. Reconciliation is lifecycle-driven and read-only against producer roots, with strict root/partition/corpus/target/profile matching, legacy adoption proposals, actionable readiness states, pinned upstream/downstream preview identity, stale-review rejection, and retryable failure history.
Frontend implementation: Library and Source connections show available partitions, readiness counts, active/latest/last-applied releases, change summaries, create/supplement/separate-database actions, adoption review, ambiguous matching, and source/database history. Managed creation is prefilled from reconciled context defaults and all writes continue through review and the durable job pipeline.
Checks run and exact result: Conda-backed full Python suite 230/230; frontend typecheck passed; Vitest 16/16; Vite production build passed; packaged desktop assets refreshed; `git diff --check` passed.
Mocked boundaries: Producer source inspection and UI bridge are fixture-backed in targeted tests; no live producer or runtime copy was modified.
Manual/native checks actually performed: None in this pass.
Audit IDs covered: M08–M16, G21, and the producer-aware tracking delta.
Open issue or next unfinished step: Ask whether the verified C: changes should be propagated to the separate D: runtime copy.
