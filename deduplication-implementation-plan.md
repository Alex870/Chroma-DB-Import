# Deduplication Implementation Plan

**Status:** Ready-to-execute plan; implementation has not been performed.
**Normative design:** [deduplication-design-proposal.md](deduplication-design-proposal.md).
**Repository:** `C:/temp/codex/Chroma DB Import`.

## Worker instructions

Implement this plan in order. Check a box only after its stated checks pass. Do not substitute a simpler matching rule, silently skip a required test, or implement deferred features. If design and plan disagree, follow the design and correct the plan before proceeding. All new function names below are proposed interfaces, not claims that the functions already exist.

Work only in this repository for steps 0–11. Step 12 identifies the separate Chat dependency. Do not edit producer files, live exports, private production databases, or another repository to make tests pass. Use temporary fixture directories. Do not reset/revert the working tree: it already contains extensive user changes, including managed-context code and this proposal. Do not commit, publish, or deploy unless separately requested.

The applicable `C:/temp/codex/AGENTS.md` requires execution artifacts inside this repository. Put test scratch directories under `.test_tmp/dedup/` and reports under `docs/` or a repository-local test-output folder. Pass an explicit repository-local `dir=` to temporary-directory helpers; keep test/build/cache files out of the shared parent directory.

Use existing Python/runtime dependencies. The core planner must use only the standard library and the local tokenizer; no new model, network service, GPU dependency, or approximate-nearest-neighbor library. Do not download models during tests. Run each targeted suite when its step is complete, then the full suite once at the end.

### Fixed implementation choices

- New modules: `deduplication.py` (pure planning), `dedup_artifacts.py` (portable serialization/validation), `retrieval_dedup.py` (reference result collapse), and `managed_lock.py` (partition locking), all under `src/chroma_db_import/`.
- Add `dedup_policy: dict[str, Any] | None = None` to `ImportConfig` (import `Any`), as an internal effective configuration value. It is not a hand-edited JSON workflow. Managed catalog/UI/CLI resolve it. Reject non-Off use without managed identity in the import boundary.
- Pass the run plan explicitly using optional keyword arguments to `run_import` and `ChromaImporter.import_cache`, rather than a module global or serializing document objects into `ImportConfig`.
- Keep existing return keys for legacy callers. Add managed result/status data and dedup statistics without forcing all callers onto an unrelated rewrite.
- No Strict profile, vector near detection, transitive near groups, canonical-edit UI, automatic historical cleanup, or cross-context matching.

## 0. Establish baseline and preserve work

- [ ] Read any applicable `AGENTS.md`, both dedup documents, `podcast-chat-handoff-contract.md`, and current working-tree versions of the named modules.
- [ ] Record `git status --short` and identify pre-existing changes. Do not treat untracked `managed.py` or its tests as disposable.
- [ ] Inspect the configured Python environment and project test conventions. Run baseline tests and record failures/skips before editing implementation.

PowerShell baseline (use an existing environment with project dependencies):

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
python -m unittest discover -s tests -v
```

If `python` is not the configured runtime, use its actual executable for all later commands. Record that executable. Do not install arbitrary dependencies to conceal an environment problem. If baseline failures are unrelated, document them separately; new/changed tests must still pass. Required real-Chroma/UI checks cannot be declared passed merely because they skipped.

**Done when:** baseline result, interpreter, and existing modifications are recorded in the implementation handoff.

## 1. Policy model, defaults, and deterministic helpers

**Files:** new `deduplication.py`; `config.py`; new `tests/test_deduplication.py`.

- [ ] Implement `resolve_dedup_policy(value, *, default_profile)` returning a validated, fully populated plain dict. Implement canonical JSON and SHA-256 helpers, `normalize_text_v1(text)`, and `normalize_span_ids(metadata)` returning spans plus a reason when unusable.
- [ ] Use exactly the policy fields/defaults/normalization in design §§4 and 8. `None` uses the caller's explicit default profile. Reject unknown fields, Strict, unsupported versions, bool-as-number thresholds, NaN/infinity, thresholds outside [0,1], and block limits outside 1–2,000.
- [ ] Off forces near/retrieval disabled. Safe and Audit differ only in storage policy by default. Version fields, min tokens, oversample factor, and cap cannot be arbitrarily changed by callers.
- [ ] Add the internal config field. Reject producer metadata collisions with the new reserved output keys in enabled mode before enrichment.

Tests must assert actual normalized strings: NFC composed/decomposed accent equality; repeated whitespace; preserved case, punctuation, `not`, `10` versus `100`, markup, and diacritics. Test singular/list/JSON-list spans, duplicate span entries, disagreement, empty entries, malformed JSON, numeric values, and absent spans.

**Check:** `python -m unittest tests.test_deduplication -v`.

## 2. Complete release inventory and exact plan

**Files:** `deduplication.py`; `managed.py`; `diagnostics.py`/`contract.py` only where reusable validation belongs; planner tests.

- [ ] Introduce immutable dataclasses (or equivalent validated records): `DedupInput`, `DedupDecision`, `ExactGroup`, and `DedupPlan`.
- [ ] `DedupInput` carries effective ID, original node ID/text/metadata, validated scope/episode, verified cache fingerprint, and a private cache locator. Private locators never enter portable output or hashes.
- [ ] `DedupPlan` carries decisions keyed by ID, sorted stored IDs, exact groups, near edges/coverage, counts, effective policy, and logical fingerprint. Include a private per-cache mapping so each retained ID imports exactly once.
- [ ] Add `load_managed_dedup_inputs(cache_paths, identity, upstream)` at the managed boundary: validate raw rows and global IDs before the existing loader can skip malformed items. Use existing eligibility rules, record exclusions, validate every record's scope and release membership. Reuse verified enclosing identity only for missing values, never conflicts.
- [ ] Implement `build_exact_plan(inputs, policy, spec)` as pure code. Construct keys from the design's full equality predicate. Compare equality fields inside hash buckets, protecting against collisions. Use sorted IDs to elect canonicals.
- [ ] Retain ineligible exact candidates. Record a deterministic primary reason using this order: invalid/missing span, missing node type, different source revision, different embedding input, metadata conflict, otherwise unique. Keep span-conflict findings separately. Do not generate every pair of equal-text conflicts; use buckets and per-record reasons/counts to avoid an unbounded quadratic report.
- [ ] Audit assigns preferred canonicals but stores every ID; Safe emits direct aliases. Unique rows prefer their own ID and have no group/alias. Off produces no dedup artifacts and does not route legacy imports through suppression.
- [ ] Recompute from the full snapshot; do not use existing Chroma IDs as the candidate set. Handle filtered canonical removal and changed content by reelection/splitting groups.

**Check:** exact fixtures in the table below, global ID validation, shuffled cache/record input, simulated hash collision, and zero mutation of input objects. No provider construction/call in tests of inventory/planning.

## 3. Bounded lexical near report

**Files:** `deduplication.py`; tests.

- [ ] Implement `detect_near_edges(representatives, policy)`. Use the existing tokenizer behavior, ordered three-token shingles, length ratio and Jaccard thresholds exactly as designed.
- [ ] Exact grouping must finish first. Compare one representative per group, even in Audit. Skip short records before checking block size.
- [ ] Iterate sorted blocks and ID pairs. Candidate pair count is the number of pairs evaluated in non-skipped blocks before the length/Jaccard gates. Short/block skip counts count representatives, not aliases. Flagged count is unique edge endpoint representatives; document that unit in the report.
- [ ] Disabled returns status disabled, zero edges/pairs, and zero skip counts. Any oversized block makes status incomplete; all processed blocks still contribute edges. No vector calls.
- [ ] Store direct edges only. Sort by `(left_document_id, right_document_id)`. Compare full precision and round displayed numbers only in UI, not before threshold decisions.

**Check:** threshold inclusivity, short text, same/different node type, cross-episode retention, no cross-scope comparison, oversized block, disabled mode, and A≈B≈C fixture with A not matching C. Stored IDs must be identical with near detection on/off.

## 4. Portable ledger and strict artifact validation

**Files:** new `dedup_artifacts.py`; new `tests/test_dedup_artifacts.py`; new contract fixtures under `tests/fixtures/contracts/chroma-export-dedup-v1/`.

- [ ] Implement `portable_occurrence(decision)`, `write_dedup_artifacts(export_root, plan, release_identity, runtime_counts)`, and `validate_dedup_artifacts(export_root, release, *, stored_records)`.
- [ ] Serialize original content/portable producer metadata and every required ledger field. Write sorted JSONL rows with final newline and deterministic JSON. Ledger preferred canonical is always nonempty; absent group/span/alias fields are JSON null. Keep runtime counters/timestamps out of logical fingerprints.
- [ ] Use existing portable-artifact rules; write private path diagnostics only to private state. Do not log transcript bodies in routine progress messages. Test known absolute-path/secret fields as well as nested metadata.
- [ ] Finalize ledger, hash its bytes, finalize dedup manifest, hash its bytes, then write release/import-manifest references. Use atomic temporary-file replacement inside staging.
- [ ] Validate exact schema/types/versions, hashes, safe relative paths, scopes, row uniqueness/counts, sorted deterministic IDs, group memberships, direct aliases, and actual stored inventory. Safe requires only canonical IDs for grouped records; Audit requires all. Reject suppressed IDs in Chroma and missing retained IDs.
- [ ] Validate near endpoints are stored representatives and in one scope/node type. Validate finite scores in [0,1]. Validate each group has >=2 members and the correct smallest-ID canonical.
- [ ] Compute plan fingerprint from policy, portable logical decisions/groups/edges/coverage without circular release identity. Recompute during validation.

**Check:** round-trip Safe/Audit/empty plans plus corruption fixtures: altered text/bytes/hash, unknown version, duplicate row, missing canonical, chain/cycle, foreign member, wrong count, absent file, path traversal/symlink escape where supported. Readers fail closed.

## 5. Catalog migration and authoritative profile loading

**Files:** `managed.py`; `config.py`; `tests/test_managed_contexts.py`.

- [ ] Increment catalog schema version to 2. Add design §8 tables in a transaction, using foreign keys to contexts and declared compound primary keys. Preserve existing rows. Detect newer unsupported schema versions instead of overwriting their version marker.
- [ ] Backfill existing profiles with explicit Off dedup policy, preserve old profile payload/fingerprint in revision history, then save the new complete current profile. Retain old export references; do not relabel their identity. Re-running migration must not duplicate history.
- [ ] New context profile initialization explicitly selects Safe. Existing contexts without a profile must remain Off after migration, not be mistaken for new contexts.
- [ ] Add `resolve_managed_config(base_config, saved_profile)` returning a copy. Apply saved behavior fields; retain machine-local paths/runtime settings from base config. Fill missing legacy fields, then validate the complete effective policy.
- [ ] Extend `import_profile_payload` to include dedup and all behavior-changing representation fields, notably output dimension and Matryoshka compatibility. Preserve stable sorted speaker lists. Exclude display-only and local path settings.
- [ ] `save_profile` updates current pointer and appends an immutable revision. Import reads it; it must no longer implicitly save global defaults. Record downstream variants without overwriting prior variants in the new table.
- [ ] Make dry-run/validation-only paths avoid save/update/catalog-status operations. Catalog migration/setup happens before preview, not as a preview side effect. CLI `--output-root` during preview is an ephemeral override, not persisted setting.

**Check:** schema-v1 fixture migration twice, preserved history, multiple downstream exports for one upstream release, same-profile stable fingerprint, changed threshold/new release ID/same representation ID, saved settings winning over globals, unknown future schema error, preview leaving established catalog contents unchanged.

## 6. Correct embedding reuse

**Files:** `importer.py`; `representation.py` only if a shared helper is needed; new `tests/test_dedup_embeddings.py`; existing reconciliation tests.

- [ ] Change cache key to existing `embedding_fingerprint` of actual model input and resolved representation. Store cache format version 2, key, representation, dimension, and vector. Old cache files are misses and stay untouched.
- [ ] Keep original producer embedding inputs accessible before adding import/dedup metadata. Ensure generated metadata cannot change contextual input accidentally.
- [ ] Validate vector payloads: numeric finite values (reject booleans), expected dimension, matching key/representation/version. Validate returned provider vector count exactly equals distinct requested inputs before writing any cache/stored row.
- [ ] Coalesce identical uncached inputs in a batch and fan out results. Maintain separate provider-input, disk-hit-record, and shared-record counters without counting one record in both savings categories.
- [ ] Preserve metadata-only reconciliation and prevent generated fingerprint recursion or repeat annotation churn. Do not alter stable IDs or derive vector keys from normalized content.

**Check:** counting fake provider; two retained records with equal embedding input use one provider input in the same batch; different contextual headers or representations use separate inputs; annotations only reuse; invalid/old vectors miss; wrong provider count fails; repeated unchanged ID makes zero document embedding calls. Count compatibility probes separately.

## 7. Integrate the plan into staging imports

**Files:** `managed.py:run_managed_import`; `cli.py:run_import`; `importer.py:import_cache`; `diagnostics.py:write_manifest`; new `tests/test_dedup_managed.py`.

- [ ] Resolve effective saved configuration and validate whole-release input before creating `ChromaImporter`. Build full plan for Safe/Audit. Preview returns complete dedup counts/reasons/fingerprint and embedding status not-executed without provider initialization.
- [ ] Pass the plan explicitly through `run_import(..., dedup_plan=None)` to `import_cache(..., dedup_plan=None)`. Reject a plan whose scope/config/cache hash does not match the run. Import only IDs assigned to that cache in the plan, using original text and required annotations.
- [ ] Keep per-cache reconciliation available for retained IDs, while snapshot dedup counts come from the release plan. Do not classify suppressed aliases as legacy source removals.
- [ ] Handle managed cancellation/stop as an explicit incomplete result; do not interpret an early loop break as completed. Check the run return status before continuing.
- [ ] Recheck source cache fingerprints against the inventory immediately before use and before promotion. Reuse the validated input snapshot where possible; abort if the live producer bytes changed.
- [ ] Write dedup ledger/manifest and add release references after all stored records are ready. Fix `_write_managed_metadata` to aggregate actual stored records by episode UID, including multiple caches per episode; retain zero-stored episodes in release coverage. Raw counts belong in input counts, not stored counts.
- [ ] Ensure enabled search sidecars use stored IDs and alias-resolvable hierarchy references. Inspect `lexical_index.py`, `advanced_sidecars.py`, and the caller before changing them; do not build sidecars that managed imports did not request.
- [ ] Propagate snapshot counters to import manifest and downstream catalog detail, plus actual operation embedding counters to run detail/UI. An empty valid eligible inventory produces a valid empty export, no document embedding, no smoke query; determine representation dimension from resolved model configuration/existing supported mechanism without inventing it.

**Check:** real temporary Chroma with fake embeddings: Safe 3 eligible exact aliases stores 1; Audit stores 3; near report never changes IDs; cross-episode identical text stores 2; zero-document release; source changes between preview and import; stored episode counts and ledger equations; no extra suppressed IDs in enabled sidecars.

## 8. Locking, promotion, and compatibility

**Files:** new `managed_lock.py`; `managed.py`; managed tests; handoff contract tests.

- [ ] Implement a context-manager partition lock around real managed runs. On Windows use `msvcrt.locking` over a fixed byte in a persistent lock file held open for the run; initialize the byte safely, seek before locking, unlock/close in `finally`. On POSIX use `fcntl.flock` if this project runs there. Do not unlink a lock file while another process might reference it.
- [ ] Same-partition competing process returns busy; separate partitions are independent. Process termination must release the OS lock. Preview does not acquire a write lock or create a lock file.
- [ ] Before publishing, validate actual collection ID set against plan, all artifacts/aliases/counts, enabled sidecars, representation, source hashes, and smoke query. A manifest file existing is not sufficient.
- [ ] Safe/Audit write downstream v2 and required capabilities. Off continues v1. `_managed_release_is_reusable` checks expected contract for policy, full identity/profile fingerprints and all required dedup integrity checks for v2. Reject a full-fingerprint collision under a truncated release name.
- [ ] Publish complete export directory without replacing history, then atomically replace pointer. Record export status so a crash after directory publication can reuse it. If pointer succeeds and catalog update fails, subsequent status/recovery reconciles from validated pointer.
- [ ] Preserve prior active pointer on cancellation, artifact validation failure, copy/rename failure, and lock contention. A pointer replacement failure may leave a complete inactive new release; do not delete historical targets.
- [ ] Fix managed CLI success classification to include valid reused results; a healthy reuse must not exit as failure.

**Check:** process-level locking test, release reuse with zero document embeddings, corrupt sidecar reuse rejection, injected failures before/after directory publication and pointer replacement, cancellation after first cache, incomplete inventory, immutable producer/history hashes. Perform tests on temporary roots only.

## 9. Reference retrieval helper and v2 handoff extension

**Files:** new `retrieval_dedup.py`; new `tests/test_retrieval_dedup.py`; `podcast-chat-handoff-contract.md`; new portable v2 export fixture.

- [ ] Implement `candidate_limit(top_k, collection_count, policy)` with strict integer validation and the exact cap/formula in design §9. Reject bools and invalid top_k, handle empty collections.
- [ ] With retrieval disabled, request only min(collection_count, top_k), preserve eligible ranking without collapse, and still perform v2 validation and alias resolution. Test this path explicitly.
- [ ] Implement `collapse_ranked_hits(hits, validated_manifest, occurrences, *, top_k, eligible_occurrence_ids)`. Caller supplies already established ranks and eligible IDs from its full filter/access policy. Helper validates every hit's bound identity before collapse, orders by rank then ID, and returns hits plus related eligible occurrences and underfill counts.
- [ ] Validate group membership against artifacts rather than trusting Chroma group strings. Keep distinct ungrouped records and all cross-episode equal-text records. Ignore near edges for default collapse. Do not compare metric-specific distances as cosine scores.
- [ ] Implement `resolve_alias(document_id, validated_occurrences)` returning target plus original ID. Unknown IDs return explicitly unavailable; do not fabricate an occurrence. Reject chains/cycles as artifact errors.
- [ ] Extend the existing Chat handoff with a clearly labeled v2 contract: required files/capabilities, validation, alias hierarchy resolution, filter-first behavior, bounded oversampling/underfill, provenance shape, and explicit legacy v1 support. Update affected normative v1-only statements so the extension is consistent. Do not change upstream versions.

**Check:** Audit group collapse chooses best eligible hit; unique hits with missing group keys all survive; identical normalized text across episodes survives; filtered canonical does not leak forbidden provenance; foreign hit fails before filtering; alias cycle/missing target rejected; empty/underfilled results and top_k limits; v1-only fixture reader rejects v2. These are reference tests, not proof of Chat deployment.

## 10. Catalog-backed CLI and desktop controls

**Files:** `managed.py:managed_main`; `ui_window.py` context methods; `ui_workers.py`; UI helpers/models only as needed; UI/managed tests.

- [ ] Add `contexts set-dedup --partition ID --profile off|safe|audit` with optional `--near-enabled true|false`, `--near-threshold NUMBER`, `--length-ratio NUMBER`, `--near-max-block-records INT`, and `--retrieval-enabled true|false`; include existing `--catalog`. Omitted optional fields retain saved values. Validate through one shared policy resolver and save a profile revision. No JSON editing required.
- [ ] Existing `import --partition ID --dry-run` returns the full preview. Add `contexts review-dedup --partition ID [--downstream-release ID] [--catalog PATH]` to read/validate active or explicitly selected historical artifacts, reporting Off/no-artifact clearly.
- [ ] UI controls load the selected context's saved values. `save_context_profile` preserves dedup/representation fields, and `import_selected_context` uses the saved effective profile. Do not silently replace settings when switching contexts.
- [ ] Add worker-backed preview and review. Create a catalog connection inside the worker; return structured data to the UI thread. Review is read-only with rows for canonical/member IDs, source provenance, decision reason, and near scores/coverage. Never make groups manually editable.
- [ ] Show Safe/Audit consumer compatibility notice, pending profile changes, running profile fingerprint, snapshot counts, operation embedding counts, and incomplete near coverage. Label preview values prospective.

**Check:** settings survive reopen/context switch; CLI and UI resolve identical fingerprints; preview does not embed/save/activate; worker failure leaves controls usable; Off review is understandable; no Strict option; old global defaults do not override saved profile. Use existing UI smoke test conventions and report missing UI dependencies honestly.

## 11. Acceptance matrix, full verification, and handoff

Build fixtures from the existing managed tests, with explicit valid identities/hashes and fake embeddings. Do not weaken validation to admit fixtures.

| Case | Required result |
|---|---|
| A/B/C differ only in IDs; same valid spans/source/text/metadata | Safe stores smallest ID only, 2 aliases; Audit stores 3 and would-suppress=2 |
| Reverse input and batch order | Same groups, aliases, ledger logical content, plan fingerprint |
| Same span, changed text under different ID | Both retained, conflict reported |
| Same normalized text, different exact embedding input | Both retained; distinct vector keys |
| Same text, missing/malformed spans | Retained with reasons |
| Same text, different episode/source revision/speaker/node type | Retained |
| Same stable ID repeated in another cache or excluded row | Validation error before provider call |
| Foreign scope or malformed raw item | Validation error before provider call |
| Old canonical removed/filtered or its text changed | New snapshot reelects/splits correctly; old snapshot unchanged |
| A≈B and B≈C, A not≈C | Two direct edges only; no transitive suppression |
| Near block size 2,001 at default limit | Whole block skipped, incomplete reported, storage unchanged |
| Producer metadata/dedup annotation changes only | Correct reconciliation without unnecessary embedding |
| Vector cache invalid, wrong dimension, nonfinite, wrong version | Miss/recompute; no invalid vector promoted |
| Metadata-only reimport repeated twice | No endless annotation/fingerprint churn |
| Policy change only | New downstream ID, same representation, exact vector reuse where enabled |
| Preview then import, same input/settings | Equal logical plan fingerprints |
| Safe/Audit artifact tampering | Validation/reuse rejected; old active release remains usable |
| Stop/cancel after first of two caches | No incomplete release activation |
| Same-partition competing import | One runs, one busy; no conflicting pointer writes |
| Same upstream imported under two profiles | Both downstream records/history preserved |
| Real Chroma/episode/ledger totals | Equations in design §10 hold |
| Reference retrieval under speaker/episode/access filters | Only eligible provenance returned; no cross-scope leakage |
| Producer and previous export byte hashes before/after | Unchanged |

Run targeted new suites, then existing full suite:

```powershell
python -m unittest tests.test_deduplication tests.test_dedup_artifacts tests.test_dedup_embeddings tests.test_dedup_managed tests.test_retrieval_dedup -v
python -m unittest discover -s tests -v
git diff --check
git status --short
```

- [ ] Document a fixture-based Safe preview/import/reuse/Audit import/rollback demonstration in `docs/deduplication-verification.md`, including policy/release IDs, counts, tests, baseline failures, and skips.
- [ ] Update README with catalog-backed commands, profile semantics, v2 compatibility, review workflow, and rebuild-only migration. Do not claim semantic detection or live Chat integration.
- [ ] Review actual diff for accidental changes to legacy behavior, producer files, user modifications, and unsupported features.
- [ ] Finish with changed-file summary, checks/results, known limitations (conservative suppression, bounded lexical coverage, evidence-ledger storage), and explicit Chat integration status.

**Importer completion gate:** steps 0–11 implemented; all new tests and required real-Chroma/UI checks pass, regression results disclosed, producer/history preservation demonstrated. If a required check is blocked by the environment, report the implementation as awaiting verification and identify that check. Do not mark skipped required integration checks as complete.

## 12. Separate PodCast Chat integration gate

This repository does not contain the Chat retrieval runtime. Do not guess its file paths or report it implemented. The importer can finish independently, but end-to-end repetition control remains pending until the consumer work is done.

When the Chat repository is explicitly made part of the implementation task:

1. Inspect its current startup validator, bound context/query representation, filter/access policy, hierarchy lookup, and retrieval result model.
2. Support both historical downstream v1 and validated v2; reject unsupported required capabilities before querying.
3. Integrate the reference contract from step 9, including oversampling, exact group collapse, alias resolution, eligible provenance, and bounded underfill.
4. Run the portable fixture and filter/isolation/corruption cases against the actual Chat adapter, not only a local imitation.
5. Confirm older v1 exports still work and v2 exports never silently bypass alias validation.
6. Only then report end-to-end Chat repetition control complete. Until then document the v2 consumer requirement prominently in release notes.
