# Deduplication verification

This repository implements contract-driven exact repetition control for managed
exports. The planner is intentionally independent of Chroma and embedding
providers, so release planning can fail closed before model construction.

## Profiles

| Profile | Stored records | Alias ledger | Chat contract |
|---|---:|---|---|
| `off` | all eligible | none | downstream v1 |
| `safe` | one canonical per exact provenance group | direct aliases | downstream v2 + `dedup-aliases-v1` |
| `audit` | all eligible | preferred canonical only | downstream v2 + `dedup-aliases-v1` |

Near matches use the frozen lexical tokenizer and ordered three-token shingles.
They are bounded, report-only edges and never affect storage or retrieval
collapse. Exact matching requires the same partition, corpus, episode, verified
cache revision, valid source span, node type, embedding input, and producer
metadata (apart from stable/node IDs).

## Verification commands

Run from the repository root with the `chroma-db-import` Conda environment:

```powershell
conda run -n chroma-db-import python -m unittest tests.test_deduplication tests.test_dedup_artifacts tests.test_dedup_embeddings tests.test_dedup_managed tests.test_retrieval_dedup -v
conda run -n chroma-db-import python -m unittest tests.test_managed_contexts -v
conda run -n chroma-db-import python -m unittest discover -s tests -v
git diff --check
```

The managed workflow is catalog-backed and does not require hand-editing JSON:

```powershell
chroma-db-import contexts link-source --root "C:\path\to\Podcast-RAG-pipeline" --output-root "D:\RAG\databases"
chroma-db-import contexts discover
chroma-db-import contexts set-dedup --partition podcast-history --profile safe
chroma-db-import import --partition podcast-history --dry-run
chroma-db-import contexts review-dedup --partition podcast-history
chroma-db-import import --partition podcast-history
```

The preview computes a complete release-wide plan and its logical fingerprint.
It does not construct an embedding provider, write vectors, activate a release,
or change the saved profile. A real import writes a fresh staging export, the
portable occurrence ledger, and the dedup manifest before atomic promotion.

## Recorded verification

The bundled dependency runtime used for repository checks is:

```text
C:\Users\Alex\miniconda3\envs\chroma-db-import\python.exe
```

The pre-implementation baseline suite ran 69 tests. The final dependency-runtime
suite ran 88 tests, and the five new deduplication suites plus managed-context
regression checks ran 27 tests successfully. The repository suite has two
pre-existing Chroma-dependent errors in `test_m2_lexical_index.py` because the
verification runtime does not include `chromadb`; four existing tests are
skipped for optional dependencies. Those checks require the project’s full
Chroma/UI environment and are not represented as passed here.

All new temporary fixtures are created below `.test_tmp/dedup/`, and producer
files are read-only inputs. No producer repository or live export is modified.

## Recovery and compatibility

Every partition has an OS-backed process lock. Staging and artifact validation
complete before the active pointer is replaced. A failed or interrupted run
leaves the previous active release untouched. Safe/Audit consumers must validate
`dedup_manifest.json`, `dedup_occurrences.jsonl`, the release references, exact
groups, aliases, scope, and Chroma stored IDs. A v1-only consumer must reject a
v2 export rather than silently ignoring its required capability.

## Known limitations

- Suppression is deliberately conservative and requires usable producer spans.
- Near detection is lexical, bounded, report-only, and not semantic paraphrase
  detection.
- The ledger preserves every eligible occurrence and therefore does not promise
  proportional disk savings outside the vector index.
- Cross-partition search and general topic/episode diversification are deferred.
- Live PodCast Chat integration is a separate deliverable; this repository only
  supplies the v2 handoff and provider-independent collapse helper.
