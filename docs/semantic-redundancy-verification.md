# Semantic redundancy verification

This file records implementation checks for the semantic-redundancy plan. It
contains no model transcripts or credentials.

## Baseline — 2026-09-06

- Repository: `C:\temp\codex\Chroma DB Import`
- Interpreter: `C:\Users\Alex\miniconda3\envs\chroma-db-import\python.exe` (Python 3.13.12)
- Optional dependency probe: `chromadb`, `sentence_transformers`, `numpy`,
  `PySide6`, and `langchain_chroma` are not installed in the configured
  interpreter.
- Command: `conda run -n chroma-db-import python -m unittest discover -s tests -v`
- Result before implementation: 89 tests ran; 83 passed, 2 skipped, and 2 failed because the existing
  Chroma integration tests import the unavailable `chromadb` package. The UI
  smoke tests and one Chroma integration test were skipped for the same missing
  optional dependencies.
- Pre-existing worktree changes were preserved; no reset, clean, or revert was
  performed.

## Implementation log

| Step | Files/check | Result | Unresolved issue |
|---|---|---|---|
| 1 | Baseline and dependency probe | Recorded above; default interpreter lacks optional runtime packages, and the installed project environment was rechecked separately | Default-environment Chroma/UI checks remain unavailable; project-environment checks pass |
| 3–4 | Policy/model contracts and catalog schema | Dependency-free imports and catalog/job smoke check passed; new catalogs report schema 3 and revisions are append-only | Legacy hand-built schema-v1 compatibility marker remains version 2 for old tooling |
| 5–8 | Inventory, candidate channels, judge validation | Dedicated inventory/candidate/dense/judge suites plus synthetic v2 inventory, private-snapshot Chroma inspection, 2,001-unit repeated-text star/cap coverage, scope-before-cap posting queries, per-channel posting-size statistics, representation isolation, dense unavailable-vector coverage, literal-evidence checks, and context-limit guards passed | Actual Chroma vector inventory pending `chromadb` in the configured interpreter |
| 9–13 | LM Studio/vLLM client, evidence, bundle, retrieval, MMR | Dedicated evidence/artifact suites plus synthetic shared-input bundle round trip, real compact-Chroma materialization/validation/retrieval, bounded fake-server pilot, filter-first retrieval, MMR regression/conformance fixture, per-call judge timing/token capture, schema-validated cache reuse, request-bound match IDs, verified-input collision separation, SQLite/evidence integrity checks, base-vector/hash revalidation at publication, and producer-side full/shared integration fixture passed. The judge sends the RAG pipeline's request-scoped `chat_template_kwargs.enable_thinking=false` control. | Real labeled pilot still pending |
| 14 | Redundancy CLI and importer UI | `redundancy` dispatch, working module entrypoint, model-free preview, assessment/publish workflow, bundle review, label export, bundle-backed A–D evaluation, saved-judge gating, worker-thread Redundancy panel, real Chroma paths, and UI smoke paths implemented | Real v2 corpus assessment and operational runtime selection remain pending |
| 17 | Mechanical evaluator | Deterministic source/lineage-only split (candidate edges cannot create leakage), stable query sampling, filter/relevance validation, bound label excerpt/hash validation, lexical development tuning, explicit unavailable A–D arm states, independent candidate-recall denominator, grouped query bootstrap with missing-query coverage, fake LM Studio server, and unknown query metrics covered by focused tests | Human-reviewed labels, real queries, and measured rollout gates remain pending |
| 15–16 | PodCast Chat integration | Actual consumer checkout now contains bundle validation/session loading, provider-independent selection, managed preference binding, retrieval routing, pre-packing service selection, UI controls, generated full/shared fixtures, and the service-level regression. Focused redundancy tests passed 7/7; the full consumer suite passed 87/87 with 2 expected PySide6 skips. | Real labeled pilot and broader operational corpus remain pending |
| 17–18 | Human labels and real pilot | Selected vLLM model `Inferact/Qwen3.8-27B-NVFP4` at the user-supplied endpoint passed one bounded no-thinking JSON schema smoke: served model matched, `finish_reason=stop`, 28 prompt tokens, 13 completion tokens, and 0 reasoning tokens. No real corpus or reviewed labels are available for the measured pilot. | Human labels, real v2 corpus assessment, and rollout gates remain pending; no quality or rollout claim made |

## Final local run — 2026-09-06

- Command: `conda run -n chroma-db-import python -m unittest discover -s tests -v`
- Result: 148 tests ran; 142 passed, 4 skipped, and 2 existing Chroma integration
  tests errored at their direct `import chromadb` calls because the configured
  interpreter does not have Chroma installed. The new semantic-redundancy
  regression, evaluator, and fake-server tests all passed. PySide6-dependent
  tests remain skipped.
- Project-environment recheck: `C:\Users\Alex\miniconda3\envs\chroma-db-import\python.exe`
  has Chroma 1.5.9, NumPy, sentence-transformers, LangChain Chroma, and PySide6
  installed. `conda run -n chroma-db-import python -m unittest discover -s tests -q` ran 152 tests with 0
  failures and 0 skips, exercising real private Chroma inventory,
  compact-vector materialization/validation/retrieval, and UI smoke paths.
- That real-runtime run exposed and fixed float32 storage-rounding validation,
  deterministic client shutdown for Windows temporary stores, and placeholder
  fake-export handling in managed metadata stamping. The focused
  artifact/integration/inventory recheck and the full redundancy suite both
  pass in that environment.
- Focused plan-aligned redundancy run: `conda run -n chroma-db-import python -m unittest discover -s tests -p 'test_redundancy*.py' -q` — 44 tests passed.
- Focused semantic selection/local-judge run: `conda run -n chroma-db-import python -m unittest tests.test_local_judge_client tests.test_semantic_selection -q` — 9 tests passed.
- `compileall` passed after adding the worker-thread importer UI workflow. The default configured interpreter still lacks PySide6, while the project environment exercised the UI smoke paths.
- UI assessment cancellation uses a private cooperative marker; the CLI checks it between candidate/judge/publication stages and records the frozen job as `cancelled`, leaving private work resumable.
- Chroma vector inspection, when available, is performed against a repository-local private snapshot; tracked release/ledger/import-manifest hashes are captured before and after inspection.
- Assessment now inventories validated vectors whenever the frozen job selects Dense or a full/shared-input bundle mode, so a missing vector backend fails before candidate publication rather than after bundle construction; model-free lexical/source preview remains vector-free.
- Shared-input bundles record a deterministic vector sidecar plus a separate cosine Chroma backend manifest; live Chroma collection IDs, vectors, metadata scope, and retrieval batches are validated when the client is available.
- The consumer root `C:\temp\codex\PodCast Chat` received the approved staged
  integration update. Existing dirty files were backed up under
  `C:\temp\codex\Chroma DB Import\.test_tmp\podcast-chat-integration-backup`
  before application. The focused consumer redundancy test passed 7/7 after
  the compact-Chroma fixture was copied to a writable temporary directory.
- A writable staging mirror at
  `C:\temp\codex\Chroma DB Import\.test_tmp\podcast-chat-integration` was built
  from the current consumer checkout to exercise the planned handoff. It adds
  bundle validation/query/hydration, managed-context binding and preference
  migration, retrieval routing, pre-packing semantic selection, UI controls,
  full/shared-input fixtures, and service-level regression coverage. The actual
  consumer environment ran 87 tests with 0 failures and 2 expected PySide6 skips. This staging mirror
  remains available as the reproducible staging source; the actual consumer
  checkout is now the authoritative integration target.
- Operational prerequisite probe: the consumer checkout has no configured
  `context_preferences.sqlite3` managed-root database and no real managed v2
  release was found. The user supplied vLLM endpoint
  `http://192.168.1.230:8000/v1` served the explicitly selected model
  `Inferact/Qwen3.8-27B-NVFP4`; the bounded judge smoke used the same
  request-scoped no-thinking control as the RAG pipeline and returned valid
  JSON with zero reasoning tokens. No real assessment, human-label export, or
  rollout claim was made.
- The managed fake importer was updated to accept the required `dedup_plan`
  argument; no compatibility retry without a plan remains.
- `git diff --check` passed. Git emitted only the existing line-ending
  normalization warnings for modified files.
