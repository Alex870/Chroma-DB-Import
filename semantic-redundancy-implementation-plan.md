# Semantic Redundancy Control: Step-by-Step Implementation Plan

**Status:** Instructions only; no implementation performed in this documentation task.
**Date:** 2026-09-06.
**Normative specification:** [semantic-redundancy-design.md](semantic-redundancy-design.md).
**Purpose:** Implement all five recommendations R1–R5 with explicit tasks and completion gates.

## 0. Worker execution rules

1. Read the design first. Follow steps in order. Check a box only after its checks pass. Do not simplify matching, invent models, weaken filters, or skip a failing gate.
2. Exact deduplication already exists. Do not blindly reimplement the old plan. Preserve all current tracked/untracked work; never reset, clean, or revert user changes. Do not commit/deploy unless asked.
3. Importer root: `C:/temp/codex/Chroma DB Import`. Consumer root: `C:/temp/codex/PodCast Chat`. Read applicable AGENTS instructions in both. Actual consumer edits belong to the implementation run, not this documentation task. If the worker lacks consumer access, mark steps 15–16 pending and continue importer work; do not bypass permissions.
4. Follow `C:/temp/codex/AGENTS.md`: all execution artifacts stay below the relevant repository, using `.test_tmp/semantic_redundancy/` for tests. No producer/historical-export writes.
5. Tests use fake embeddings and a fake local HTTP server. No test downloads a model or calls a real LLM. Live pilot requires an explicitly selected installed model.
6. A measured no-go result completes the experiment. Missing real measurements/human labels means evaluation pending, not success.
7. Maintain `docs/semantic-redundancy-verification.md`: step, files, exact command/result, unresolved issues. Do not put secrets or full transcripts in routine logs.

## 1. Baseline and prerequisites

**Depends on:** none. **Covers:** prerequisites for R1–R5.

- [ ] Record Git status, pre-existing changes, interpreter, and installed dependency versions. Read both dedup designs, current source, requirements, verification document, and handoff contract.
- [ ] Check the configured environment for `chromadb`, `sentence_transformers`, `numpy`, and UI dependencies. Prior verification reported missing Chroma; recheck instead of copying that report.
- [ ] Run importer baseline tests. Before step 15, run the consumer baseline from its own root/environment.
- [ ] Create the verification document with baseline failures/skips. Use the configured interpreter, not an arbitrary installation. Do not conceal dependency failures with fabricated results.

From importer root in PowerShell:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
conda run -n chroma-db-import python -m unittest discover -s tests -v
```

If `python` is not the configured executable, replace it consistently in all commands. All later module paths are below `src/chroma_db_import/` unless another root is specified.

**Pass:** reproducible baseline recorded. Unrelated baseline errors remain disclosed; required real-Chroma/UI checks cannot pass by skipping.

## 2. Preserve and verify the exact layer

**Files:** existing `deduplication.py`, `dedup_artifacts.py`, `importer.py`, `managed.py`, `retrieval_dedup.py`; existing dedup/managed tests. **Covers:** R1.

- [ ] Add/verify regression fixtures for exact grouping, Audit retention, unchanged ID reimport, metadata-only vector reuse, full embedding-input keys, canonical change, scope isolation, and missing/conflicting provenance retention.
- [ ] Validate actual temporary Chroma IDs against the plan, not just manifest files.
- [ ] In `run_managed_import`, remove the enabled-dedup compatibility escape that catches a `TypeError` and retries `run_import` without `dedup_plan`. Enabled dedup must fail before publication when integration is unsupported. Update test fakes to the real signature.
- [ ] Require v2 reuse validation when policy expects v2 even if a damaged release omits `dedup` and `required_capabilities`. Preserve valid Off/v1 behavior.
- [ ] Check group membership and suppressed-row exclusion even with retrieval collapse disabled. A toggle must not bypass validation.
- [ ] Verify cancellation preserves pointer and source/history bytes remain unchanged. Fix only relevant invariant failures; document unrelated problems separately.

**Check:** existing dedup/managed suites, including actual temporary Chroma. **Pass:** exact key/group identities and old versioned near algorithm remain unchanged; required invariants pass.

## 3. Pure policy and record contracts

**New files:** `redundancy_policy.py`, `redundancy_models.py`; `tests/test_redundancy_policy.py`.

- [ ] Implement `resolve_redundancy_policy(mapping=None) -> dict` using every field/default/range in design §4. Reject unknown keys/versions, bool-as-number, NaN/infinity, and changed fixed constants.
- [ ] Implement canonical JSON/SHA-256 policy fingerprinting. Do not include endpoint/auth/timestamps in portable policy.
- [ ] Define frozen records:
  - `Scope(partition_id, corpus_id, base_release_id, representation_id)`.
  - `AnalysisUnit(document_id, occurrence_ids, text, metadata, node_type, episode_uid, cache_fingerprint, span_ids, embedding_fingerprint)`.
  - `CandidatePair(left_id, right_id, channels, scores, channel_ranks, guards)`; IDs sorted.
  - `Coverage(unit_count, channel_available, posting_truncations, capped_neighbors, skipped_reasons)`.
  - `Judgment(candidate_id, relation, matched_ids, evidence, novel_quotes, conflict_quotes, change_flags, status, reason)`.
  - `SelectionResult(selected_ids, omitted, related_occurrence_ids, mode, fallback_reason)`.
- [ ] Keep score meanings separate: Jaccard, directional text/span containment, cosine. Do not create an uncalibrated duplicate probability.

**Check:** defaults, round trips, shuffled object keys, validation errors. **Pass:** pure tests load no Chroma/model/UI dependencies.

Use the explicit shortlist priority/gating and benchmark split rules in design sections 5.3 and 9; do not invent score normalization or connect all candidate edges into split groups.

## 4. Catalog migration and frozen jobs

**Files:** `managed.py`; new `redundancy_jobs.py`; `tests/test_redundancy_catalog.py`.

- [ ] Migrate schema 2 -> 3 transactionally using design §4 tables, compound revision keys and foreign keys. Seed existing contexts with redundancy defaults without changing exact profile/release identity.
- [ ] Implement `get_redundancy_policy`, `save_redundancy_policy`, `create_redundancy_job`, `update_redundancy_job`, `get_redundancy_job`. Revisions append-only; scope every lookup.
- [ ] Store private judge configuration with partition-qualified catalog setting keys; auth by reference only.
- [ ] Freeze job spec: base scope/hashes, policy/fingerprint, explicitly selected channels, model artifact identity or session nonce, prompt/schema versions.
- [ ] States: planned/running/cancelled/failed/completed/completed_partial. Partial lists unavailable/truncated/unfinished portions. Empty edges alone never imply complete coverage.
- [ ] Established-catalog preview is read-only. Mid-job policy edits affect the next job. Reject newer schema versions.

**Check:** migrate twice; preserve rows/fingerprints; isolate partitions; frozen settings; preview no mutation. **Pass:** existing exact import behavior remains intact.

## 5. Evidence and vector inventory

**New file:** `redundancy_inventory.py`; `tests/test_redundancy_inventory.py`.

Implement `load_inventory(base_export, *, inspect_vectors=True)` returning scope, units, occurrences, vectors, and hashes.

- [ ] Require complete validated managed v2, ledger hash/capability, and actual stored IDs. V1 returns `requires_v2_evidence`, never synthesized provenance.
- [ ] One analysis unit per exact group or unique retained ID; Audit also uses one unit/group. Retain all original member occurrences.
- [ ] Validate scopes/representations/direct aliases before vector lookup. Fetch existing embeddings in pages of <=1,000, without a provider call.
- [ ] Reject invalid/nonfinite/dimension-mismatched vectors; zero norm is explicitly unavailable for cosine.
- [ ] Hash base files before/after. If the installed Chroma client mutates a source on open, analyze a verified private snapshot copy instead. Never permit a read workflow to modify historical files.

**Check:** Safe/Audit inventories, alias mapping, missing/foreign rows, zero norm, no embeddings, byte preservation. **Pass:** all eligible original occurrences retained.

## 6. Indexed lexical and source candidates

**New file:** `redundancy_candidates.py`; `tests/test_redundancy_candidates.py`.

Implement `build_lexical_index(units, sqlite_path, policy)` and `lexical_and_source_candidates(unit, index, policy)`.

- [ ] Create indexed private SQLite postings for normalized text, source spans, and MinHash bands. Tokenize and create ordered three-token shingle sets as designed.
- [ ] Implement exactly the 64-seed SHA-256/16-band recipe in §5.3. No process-random hash, unpinned random MinHash, or full-corpus all-pairs pass.
- [ ] Verify underlying strings after exact-text hashes. Represent repeated-text buckets with smallest-ID star/member inventory, not quadratic edges.
- [ ] Scope source postings by episode UID/cache fingerprint/span. Calculate both directional source/text containment plus Jaccard. Missing spans disable only that channel.
- [ ] Cap each posting lookup, not the entire node block. Query every unit and record true posting sizes/truncation. Apply per-channel gates, ordering, and neighbor caps.
- [ ] Material guards: differing numeric-token sets; differing tokens from `{no, not, never, without, may, might, could, must, only, unless, if}`; changed episode/speaker/date. They veto suppression suggestions, not candidate discovery.
- [ ] Under-three-token content retains exact/source channels; do not interpret empty shingles as equivalence. Input-order changes must not change lexical/source output.

**Check:** fixed known signatures; shuffled input; overlap/containment; short negation/numbers; 2,001+ units still queried; 513-entry posting cap reported; scope isolation; repeated-text edges O(n).

**Pass:** new channel removes the old whole-block coverage gap without altering `lexical-near-v1` semantics.

## 7. Dense neighbors and saved candidate snapshot

**Files:** `redundancy_candidates.py`; `tests/test_redundancy_dense.py`.

Implement `build_dense_candidate_index` and `generate_candidate_snapshot`.

- [ ] Build a private cosine Chroma index using supplied vectors, scoped node metadata, no embedding function. Never mix representations/scopes.
- [ ] Query `dense_neighbors+1`, remove self, compute explicit cosine from vectors, gate/cap. Do not reinterpret native L2/IP distances.
- [ ] Merge undirected channel pairs, preserving ranks/scores/guards. Pair ID hashes scope plus sorted IDs. Algorithm/model settings remain in run identity.
- [ ] Write sorted candidate JSONL and coverage in private job workspace. Record runtime/ANN configuration and resulting candidate snapshot hash; ANN rebuilds need not be bit-identical.
- [ ] Requested dense mode with missing dependencies fails clearly. Lexical-only must be explicitly selected and marked partial; never silently claim all channels ran.

**Check:** low-lexical/high-semantic synthetic neighbors, metadata filters, zero norm, count bounds, no embedding calls, snapshot reuse. **Pass:** bounded merged candidate snapshot with honest coverage.

## 8. Judge prompt, shortlist, and output validation

**New files:** `redundancy_judge.py`, package resource `prompts/redundancy_judge_v1.txt`; `tests/test_redundancy_judge.py`.

- [ ] Implement pure `select_judge_units`, `build_judge_request`, `validate_judgment`. Use design §6 budgets, ranking, and whole-text limits.
- [ ] Persist a fixed system prompt containing all §6.2 requirements: all assertions covered with the same actor/time/polarity/conditions; literal evidence; uncertainty; no majority-vote truth; ignore document instructions. Include the prompt as package data.
- [ ] Put source passages in a JSON data message. No tools, action execution, history, or source-derived system instructions.
- [ ] Build strict schema: relation enum, candidate ID, known matched IDs, evidence objects, novel/conflict quote arrays of literal candidate-text strings, three boolean change flags, short reason; reject unknown properties and out-of-bounds arrays/strings.
- [ ] Verify every quote is a nonempty literal substring of the specified original text. Equivalent requires evidence, no novel/conflict quotes, and false change flags. Material guards block equivalent-suppression suggestions regardless of the model response.
- [ ] Every final action is `retain_evidence`. Semantic relations never become exact aliases. Cross-episode/source/speaker relations carry `distinct_occurrence`.
- [ ] Remove lowest-ranked whole neighbor objects to fit the request budget. Never shorten source text. Candidate plus one neighbor cannot fit -> uncertain/context_limit. No neighbors is not proof of novelty.

**Check:** unknown IDs/keys, fabricated/wrong-record quotes, empty evidence, material conditions, injection, oversize requests, zero budget, stable shortlist, no index changes. **Pass:** invalid output becomes explicit uncertain/retain.

## 9. LM Studio client, cache, and bounded execution

**New file:** `local_judge_client.py`; extend `redundancy_jobs.py`; `tests/test_local_judge_client.py`.

- [ ] Use standard-library HTTP or an already pinned dependency. Implement LM Studio's compatible endpoint only; no unused cloud/Ollama adapter.
- [ ] Default base URL `http://localhost:1234/v1`; GET `/models` lists choices, never auto-selects one. Require explicit configured model ID. Inspect Chat's existing `lmstudio.py` for conventions, without sibling sys.path imports.
- [ ] POST `/chat/completions` with strict response schema, stream false, temperature zero, output limit 768, no tools. Parse `choices[0].message.content`; reject absent/unfinished output or served-model mismatch. Unsupported structured output is uncertain, not free-form authority.
- [ ] No redirects/ambient proxies; loopback default and explicit host allowlist otherwise. Private auth stays private. One concurrent request, no retry, per-call timeout, job wall-clock/call budgets, cancellation checks between calls.
- [ ] Implement cache keys from design §6.3. No immutable model fingerprint -> per-job nonce and no cross-job judgment reuse. Resume same job may reuse schema-validated successes. Do not cache server/context errors as successful decisions.
- [ ] Record actual elapsed/token usage; absent token counts are null. Checkpoint each response. Model unavailable -> candidate report survives, pilot completed_partial with reason.
- [ ] Tests use a fake loopback server. Do not contact an installed real model in automated tests.

**Check:** success, malformed/foreign/timeout/truncated response, redirect rejection, cache invalidation on every input/model/prompt change, error noncaching, budgets, cancel/resume. **Pass:** fake end-to-end pilot bounded and source/export bytes unchanged.

## 10. Portable evidence and exact-input sharing

**New file:** `redundancy_evidence.py`; `tests/test_redundancy_evidence.py`.

Implement `write_evidence_inventory`, `build_occurrence_index`, `build_representative_map`, `build_compact_vectors`.

- [ ] Copy every original validated occurrence to sorted portable evidence JSONL, preserving original text/metadata and exact lineage. No model summaries replacing evidence.
- [ ] Build occurrence/speaker/date/representative SQLite tables/indexes from design §7. Parse speaker/date using existing rules. Ledger is authority; SQLite is derived.
- [ ] Full mode uses base vectors/exact alias mapping. Shared_input groups only identical verified actual embedding-input strings within one representation/scope. Compare strings after hashes; smallest original ID is representative.
- [ ] Reconstruct actual input using pinned contextualization and verify against base fingerprints. If impossible after portability redaction, retain an unshared singleton with its existing vector and `unverifiable_embedding_input` reason.
- [ ] One map row per occurrence including exact aliases. Resolve existing aliases before obtaining vectors. Different contextual headers must not share; do not loosen this rule for higher savings.
- [ ] New compact Chroma stores supplied vectors and representative/scope metadata only, not a misleading single episode/speaker for all members. Judge relationships never affect vector mapping.
- [ ] Validate representative counts/targets/dimensions and close/checkpoint handles before file hashes. On Windows use the project's process/handle patterns; do not kill unrelated applications to release files.

**Check:** equal-input/different provenance shares; changed header does not; missing reconstruction singleton; whitespace-original text retained; Audit/exact alias coverage; no semantic omissions. **Pass:** occurrence count and original text hashes preserved; compact rows equal representative count.

## 11. Bundle schema, validation, and publication

**New file:** `redundancy_artifacts.py`; `tests/test_redundancy_artifacts.py`; fixtures under `tests/fixtures/contracts/chroma-redundancy-bundle-v1/`.

Implement `write_bundle`, `validate_bundle`, `publish_bundle`.

- [ ] Define manifest with contract/capabilities, base scope/hashes, policy/fingerprint, input-spec fingerprint, coverage, judgment/model/prompt status, storage mode, artifact references/hashes/row counts, logical vector digest.
- [ ] Include evidence JSONL, occurrence SQLite, candidates, judgments, representative map; judgments may be empty only with explicit disabled/zero-budget status. Compact mode includes its closed vector collection. Coverage remains in manifest after private workspace cleanup.
- [ ] Compute file hashes first, then bundle ID from base identity/policy/artifact hashes and actual judgment snapshot. Exclude bundle ID/timestamps/timings/local paths from identity. Runtime costs have an explicitly excluded manifest section, implemented in one shared helper.
- [ ] Validate versions/capabilities, relative paths, scope, schemas/hashes/counts, map/vector references, ledger/SQLite agreement. Full mode validates base vectors; shared_input validates compact vectors. Consumer never needs private catalog/job state.
- [ ] Recheck base hashes and atomically publish under partition lock. Lock publication only. Existing valid identical target may be reused; otherwise fail without overwrite.
- [ ] Cancellation/failure leaves only private resumable work; never changes active-release pointer, source release, or historical bundle.

**Check:** corruption, missing base, foreign map, vector mismatch, path/symlink escapes, mid-run base changes, interrupted publication, valid reuse, changed judgments/new ID, portability. **Pass:** bundle validated using only its files plus resolved base.

## 12. Filter-first compact retrieval adapter

**New file:** `redundancy_retrieval.py`; `tests/test_redundancy_retrieval.py`.

Implement importer-side reference interfaces:

```text
eligible_occurrences(bundle, validated_filter) -> IDs
representatives_for(eligible_ids) -> unique representative IDs
query_representatives(query_vector, allowed_ids, depth) -> ranked hits
hydrate_occurrences(hits, eligible_ids, mode) -> original hits/provenance
```

- [ ] Filters carry episodes, speakers, node types, date bounds, and optional externally computed allowed-occurrence IDs for access policy. Unknown filters fail. Supplied empty access set stays empty, never unrestricted.
- [ ] Filter occurrences first; map to representatives. In compact mode query `representative_id` metadata using allowed IDs in sorted batches <=1,000. Merge bounded per-batch results in metric-correct order. Test pinned Chroma API rather than assuming support.
- [ ] Full mode maps eligible occurrence IDs through exact aliases and uses a supported ID-restricted query mechanism. If the pinned base client cannot query explicit IDs, fetch allowed embeddings in bounded pages and perform exact metric scoring privately; do not rewrite the base collection or filter after taking an unrestricted top_k.
- [ ] Hydrate only eligible original occurrence metadata/text. Factual mode picks one member; preserve-occurrences expands within top_k and offers paginated enumeration plus total counts.
- [ ] Empty allowed representatives returns empty without query. Scope/representation failure is unavailable, not another database fallback.
- [ ] Cache validated read-only bundle handles per bound session; invalidate on base/bundle refresh. Avoid rereading complete JSONL every query.

**Check:** excluded representative A with eligible member B, speaker/access/date filters, empty set, >1,000 reps, alias hierarchy provenance, recurrence expansion, controlled-vector full/compact evidence equivalence. **Pass:** excluded metadata neither leaks nor hides eligible noncanonical members.

## 13. One semantic MMR selector and conformance fixtures

**New file:** `semantic_selection.py`; `tests/test_semantic_selection.py`; shared portable JSON fixtures.

Implement `select_evidence(hits, vectors, *, top_k, mode, mmr_lambda) -> SelectionResult`.

- [ ] Inputs are scope-validated, filtered, relevance-ranked hits. Validate IDs/vector space/shapes before selection.
- [ ] Use design §8 exact formula: rank relevance `1/(1+rank0)`, nonnegative cosine penalty, lambda 0.75, first highest relevance, deterministic rank/ID ties. Do not discard all negative-scoring candidates; fill top_k where possible.
- [ ] Ranked mode retains relevance order after proven-copy handling. Preserve-occurrences bypasses semantic penalties and cross-episode bare-text collapse.
- [ ] Missing/invalid required vector falls back for the entire request to ranked with reason. No model/embedding calls.
- [ ] Emit selected/omitted IDs and similarities/reasons/underfill. Token-budget omissions belong to the packer.
- [ ] Save expected JSON conformance outputs: same/orthogonal/opposite vectors, rank ties, negative MMR scores, absent vector, top_k bounds, repeated cross-episode text, recurrence.

**Check:** best hit survives; diverse fixture reorders; negative scores still fill; missing vector full fallback; correct mode behavior. **Pass:** deterministic provider-independent reference.

## 14. Catalog-backed CLI and importer UI

**Files:** new `redundancy_cli.py`; existing `cli.py` dispatch, `managed.py`, `ui_window.py`, `ui_workers.py`; CLI/UI tests.

- [ ] Add `chroma-db-import redundancy` dispatch without breaking current commands.
- [ ] Implement commands below. All accept `--catalog PATH`; context-bound commands require `--partition ID`.

| Command | Arguments | Result |
|---|---|---|
| `policy` | optional `--storage full|shared_input`, `--retrieval ranked|semantic_mmr`, `--judge-enabled true|false`, `--judge-fraction FLOAT`, `--judge-max-calls INT` | Show if no edits; otherwise validate/save revision. |
| `configure-judge` | `--base-url URL --model ID`, optional `--model-fingerprint HASH` | Save private configuration, no auto-selection/download. |
| `models` | optional `--base-url URL` | List model choices. |
| `preview` | `--release ID` | Model-free prospective lexical/source coverage, no job/index/profile writes. |
| `assess` | `--release ID`, optional `--channels lexical,structural,dense`, `--judge`, `--resume JOB_ID` | Scoped assessment/new immutable bundle. |
| `review` | `--bundle ID` | Validate/show reports/provenance/coverage. |
| `label-export` | `--bundle ID --output PATH` | Export unlabeled examples. |
| `evaluate` | `--bundle ID --labels PATH --queries PATH --output PATH` | Run frozen metrics/gates. |

- [ ] Exit 0 complete/valid, 1 failed, 2 explicit partial/pending prerequisite. Always show status/reason. Judge disabled is not candidate-job failure.
- [ ] Judge calls require both saved enablement and explicit `--judge`/UI pilot action. Omission never invokes a model. Resume requires matching frozen job spec; a changed policy starts a new job.
- [ ] UI under selected Context: policy/model selection, Preview, Assess, Cancel, Resume, Review, Export labels, Evaluate. Use existing QObject worker pattern and thread-local SQLite; no UI-thread HTTP/vector work.
- [ ] Review shows original/representative IDs, reasons/channels/material guards, literal judge evidence, advisory labels, coverage, time. No delete action.
- [ ] Saving mode/bundle in importer does not activate it in Chat. Display base/bundle identity and explicit consumer selection instructions.

**Check:** parser/help, default no calls, required model selection, frozen resume, worker recovery, preview no mutations, no Strict/delete UI. **Pass:** complete importer workflow accessible without editing JSON or private function calls.

## 15. Integrate actual PodCast Chat

**Depends on:** steps 11–13. **Root:** `C:/temp/codex/PodCast Chat`. **Covers:** live R3/R5.

- [ ] Read consumer instructions and record baseline/tests/changes. If access is missing, mark pending; do not claim live completion.
- [ ] Add `src/podcast_chat/redundancy_bundle.py` for validation/session loading and the step-12 adapter. Use portable conformance fixtures, not a runtime sibling-path import.
- [ ] Add `src/podcast_chat/semantic_selection.py` with the exact step-13 algorithm and the same fixtures. Do not create a different formula.
- [ ] Extend managed context preferences with optional bundle ID/mode keyed by partition/corpus/base release. Selection is explicit, separate from active base pointer. Base refresh invalidates mismatched bundle selection.
- [ ] In `managed_contexts.py`, validate base/bundle identity, capabilities and files, then bind a read-only session. Invalid bundle is unavailable; user can explicitly choose valid full base.
- [ ] In `retrieval.py:ChromaVectorService.retrieve`, apply existing filter/access policy to occurrences, use chosen adapter, hydrate original `RetrievedChunk` text/IDs/metadata. Vectors are request-local side data, not routine debug arrays.
- [ ] Keep hybrid/graph channels correct: map and filter original IDs on hydration/expansion. Unsupported compact channel must be explicitly unavailable with reason, not silently emit stale references. Required dense/full and dense/compact paths must work.
- [ ] In `services.py:ChatService.answer`, run selector after existing relevance reranking and before `pack_evidence`. Reranker retains bounded candidate depth rather than trimming to final top_k first. Cap 200 and 5x oversampling; expansion/fusion remains scoped.
- [ ] Disable old token-overlap MMR when new selector runs. In `context.py:pack_evidence`, keep token budgeting but replace bare-text cross-episode suppression with ID/provenance-aware rules. Check all research callers.
- [ ] Add preserve-occurrences override/explicit recurrence mode. Automatically preserve occurrences for timeline, belief-evolution, speaker-comparison. Do not silently alter user filters with broad keyword heuristics.
- [ ] Extend UI/settings with ranked/semantic MMR/preserve occurrences and full/compact bundle choice. Defaults ranked/full. Related-occurrence expansion obeys filters and shows counts/pagination.
- [ ] Grounding/citations use original occurrence IDs and text. Never cite representative metadata as if it were the selected episode. Distinguish presented passages from occurrence counts.

**Checks:** new `tests/test_redundancy_bundle.py`, `tests/test_semantic_selection.py`, `tests/test_redundancy_services.py`; existing managed, services, provenance, hybrid, research, and UI suites.

**Pass:** actual service call on temporary valid bundle uses new selection/retrieval and returns correct citations. A reference-helper test is insufficient.

## 16. Cross-repository integration fixture

- [ ] Importer generates a valid small base and full/shared-input bundles under `.test_tmp/semantic_redundancy/integration/` with fake/counting embeddings.
- [ ] Include equal-input/different-episode occurrences, a new qualification, contradiction, source overlap, Audit copies, excluded speaker, and hierarchy link to exact alias.
- [ ] Consumer loads those generated files and runs ranked, MMR, preserve-occurrences service paths with a fake answer model.
- [ ] Assert filtered member discoverability when its representative is excluded; no scope leak; related provenance filtered; original citations; unchanged source bytes; refresh mismatch; corrupt-bundle rejection; explicit full-base recovery.
- [ ] Compact fixture has fewer vector rows, unchanged evidence count, and every original text recoverable. Do not require unrelated real corpora to meet a synthetic savings target.

**Pass:** actual producer-consumer contract demonstrated. Record commands/results in both verification reports. Never run integration tests against a live export.

## 17. Labeling and benchmark evaluator

**Importer files:** new `redundancy_evaluation.py`; `tests/test_redundancy_evaluation.py`; review/evaluation UI.

- [ ] Export labelable pairs with scope/ID/content hashes, source excerpts, relation enum, material-change flags, reviewer, and split. Default label null. Human-reviewed labels are required for real quality claims.
- [ ] Select 600 distinct real comparisons in stable SHA-256 order, stratified as design §9; aim for 300 development/300 held-out. Split by connected episode/source groups. When exact quotas conflict with leakage protection, preserve grouping and report actual counts, never duplicate examples.
- [ ] Export 100 real queries with mode, required occurrence IDs/graded relevance; aim for 50/50 with same grouping. Unlabeled queries cannot count as measured recall/nDCG.
- [ ] Add independent candidate-recall audit: <=200-unit scoped subsets for exhaustive comparison/review, plus sampled nongenerated pairs. Recall denominator cannot include only retrieved positives.
- [ ] Implement arms A–D with frozen data/settings. Record candidate, judge, retrieval, latency, and storage metrics separately. Use source-group bootstrap with seed 0 and 1,000 replicates for query metrics.
- [ ] Freeze lexical/source baseline on development data: grid Jaccard threshold {0.8,0.9,0.95,1.0}, containment threshold {0.9,0.95,1.0}, material guards always veto. Predict equivalent only when BOTH selected thresholds pass. Choose highest development precision with nonzero positives, tie highest recall then stricter thresholds. No positives -> insufficient baseline, not perfect precision.
- [ ] Compare judge on the same selected subset, reporting overall shortlist selection rate and recall. Existing relevance reranker on/off is a separate retrieval ablation, never described as NLI. Optional installed/configured NLI arm does not block required A–D.
- [ ] Implement design's numeric rollout gates. N/A/missing data -> insufficient_evidence. Include denominators/intervals; small samples with zero observed errors do not certify safe deletion.
- [ ] Write `evaluation.json` and `evaluation.md` to explicit private report output. Report generation cannot enable a mode, mutate policy, or delete vectors.

**Check:** known metrics, empty positives, missing labels, leakage detection, held-out tuning prevention, baseline zero redundancy, deterministic bootstrap, no-go outcome.

**Pass:** mechanical evaluator correct and users can review real labels through UI/export without hand-editing policy.

## 18. Real pilot and rollout decision

**Requires:** real v2 corpus, explicitly selected local model, reviewed labels/queries. These are operational gates, not reasons to abandon independent implementation.

- [ ] Choose a real context/release through UI and verify scope/hashes. Do not pick unrelated data to improve reported results.
- [ ] Explicitly select the installed LM Studio model and record artifact identity if available. If none configured, request model selection, not a guessed ID. Run one small schema smoke request.
- [ ] Record hardware/model/quantization/context settings without credentials. Run frozen candidate assessment and bounded default judge pilot. Measure real duration/tokens/coverage; never substitute illustrative latency estimates.
- [ ] Obtain reviewed labels. Worker can prepare forms/synthetic tests but cannot pretend its model-generated labels are human ground truth. Missing labels -> evaluation pending.
- [ ] Tune development only, freeze, evaluate held-out. Retuning after held-out failure consumes that test set; obtain a new held-out group before a new go claim.
- [ ] Run A–D on the same query set; report gates/intervals/coverage/skips/compact and total storage. A no-go result retains ranked/full defaults and states the measured reason.
- [ ] Passing permits opt-in recommendations/advisory judge only. No semantic physical suppression. Mode activation occurs through explicit user selection, never edits to history.

**Pass:** reproducible real labeled measured report, whether go or no-go. Unavailable prerequisites remain specifically named pending items.

## 19. Final verification and documentation

Importer root:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
conda run -n chroma-db-import python -m unittest discover -s tests -p 'test_redundancy*.py' -v
conda run -n chroma-db-import python -m unittest tests.test_local_judge_client tests.test_semantic_selection -v
conda run -n chroma-db-import python -m unittest discover -s tests -v
git diff --check
git status --short
```

Consumer root, with its interpreter/environment:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
conda run -n chroma-db-import python -m unittest tests.test_redundancy_bundle tests.test_semantic_selection tests.test_redundancy_services -v
conda run -n chroma-db-import python -m unittest discover -s tests -v
git diff --check
git status --short
```

- [ ] Update importer README/handoff with commands, separate bundle contract, local pilot, sharing limits, no automatic activation/deletion.
- [ ] Update consumer help with modes, bundle selection, filter-aware citations, recurrence, explicit recovery.
- [ ] Finish verification reports: baseline, new tests, real integration, measured pilot/labels, changed files, failures/skips, rollout outcome.
- [ ] Confirm source/history unchanged, no ambient downloads/cloud calls, no unsupported semantic deletion, no false vector-versus-total-disk savings claim.
- [ ] Deliver user-executable commands/UI, not only private functions.

## 20. Acceptance and completion states

| Recommendation | Mandatory proof |
|---|---|
| R1 | Exact identities/policies unchanged; actual stored IDs/cache correct; no compatibility bypass. |
| R2 | >2,000-node corpus queried with indexed bounds; lexical/source/dense positives found; coverage and candidate recall reported. |
| R3 | Actual Chat service uses one semantic selector, ranked fallback, research/recurrence bypass, original filtered citations. |
| R4 | Fake-server robustness AND real selected-model timing/labeled held-out go/no-go report; judge never deletes evidence. |
| R5 | Every occurrence retained; only identical verified inputs share vectors; eligible noncanonical member searchable; full-base recovery works. |

Use these final status meanings:

- **Implementation verified:** steps 1–17 and 19 mechanical/code/integration checks pass, including real Chroma/UI/Chat tests. If step 18 is pending, say exactly “implementation verified; operational pilot pending,” not “all five validated.”
- **End-to-end validated:** implementation verified plus real labeled measured step-18 report. A no-go judge outcome completes the experiment; retain defaults and explain why.
- **Partial:** required implementation/access/dependencies/tests/model/labels missing. Name unfinished work. Do not turn skipped tests into passes or fabricate metrics.

Do not stop after generating candidates or a local MMR helper and claim completion. Consumer integration, evidence mapping, and the measured pilot are explicit deliverables.

## Appendix A. Fixed evaluation file shapes and formulas

Use these shapes to avoid inventing incompatible formats. All IDs resolve inside the bound base scope; unknown IDs and mismatched hashes fail validation.

`labels.json` top level: `contract_version: redundancy-labels-v1`, `base_scope`, `base_fingerprint`, and `pairs`. Each pair has `left_id`, `right_id`, `relation` (same enum as judge), `material_difference` (boolean), `distinct_occurrence` (boolean), `reviewer` (nonempty for reviewed rows), `split` (development/held_out), and `source_group_ids`. Null relation means unlabeled and cannot contribute to measured quality. Relation describes claim coverage; distinct_occurrence separately records independent provenance. An equivalent semantic relation never authorizes evidence deletion.

`queries.json` top level: `contract_version: redundancy-queries-v1`, `base_scope`, `base_fingerprint`, and `queries`. Each query has `query_id`, `query`, `mode`, validated `filters`, `relevance` (original occurrence ID to integer grade 0..3), `reviewer`, `split`, and `source_group_ids`. At least one positive grade is required to measure recall/nDCG. Relevance annotations must identify acceptable substitute occurrences explicitly; the evaluator may not infer them from embeddings or judge labels.

Metric definitions:

- Equivalence precision = correctly predicted equivalent / all predicted equivalent; denominator zero -> unknown.
- Equivalence recall = correctly predicted equivalent / all labeled equivalent; denominator zero -> unknown.
- Material-change error = predicted-equivalent rows with material_difference true, reported as both count and rate.
- Candidate recall = labeled positive pairs found / all labeled positive pairs in the independent audit; count a saved exact-text star/member relation as found when it directly establishes the pair's exact-text membership, not by semantic transitivity.
- Query recall@k = positive-grade occurrence IDs retrieved / all positive-grade IDs for that query. Deduplicate IDs, not text.
- nDCG@k uses gain `2^grade-1` and discount `log2(rank+1)` with one-based ranks; ideal zero -> unknown.
- Redundant-passage rate = selected passage pairs labeled equivalent / all selected passage pairs with reviewed labels. Denominator zero -> unknown. Do not assume unlabeled pairs are unrelated. Report label coverage with the rate.
- For proportions report 95% Wilson intervals using z=1.96 and numerator/denominator. For query-level aggregate differences use the specified source-group bootstrap. Never report an interval for unknown metrics.
- Latency excludes warmup from steady-state percentiles but reports cold start separately. Include retrieval/selection cost and LLM pilot cost separately. Cache-hit and cold pilot runs are separate measurements, not one blended speed claim.

Tune only using reviewed development labels. Source split identity and all input-file hashes become part of the evaluation fingerprint. The held-out report records the frozen policy and label hashes so later changes cannot silently reuse an earlier go result.

## Appendix B. Stop/resume behavior by missing prerequisite

| Missing prerequisite | Continue with | Must remain pending |
|---|---|---|
| Installed Chroma/UI runtime | Pure policy, lexical, judge-validator, fake-server, metrics work | Real storage/UI/consumer integration checks |
| Access to Chat repository | Importer bundle, CLI/UI, reference/conformance fixtures | Steps 15–16 and live R3/R5 |
| Selected local model/server | All non-LLM and fake-server work | Real pilot timings/quality |
| Human-reviewed labels/queries | Label export, review UI, synthetic evaluator tests | Held-out quality/go-no-go claims |
| Suitable real v2 corpus | Synthetic integration and interface completion | Operational corpus pilot |

A pending item is never filled with guessed data. Preserve completed work, state the missing prerequisite precisely, and resume from the corresponding numbered step when it becomes available.
