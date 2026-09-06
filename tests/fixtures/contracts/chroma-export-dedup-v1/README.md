# `chroma-export-dedup-v1` fixture

The executable Safe/Audit round-trip fixtures are created in the repository
scratch directory `.test_tmp/dedup/` by `test_dedup_artifacts.py`. They are
generated from the same deterministic writer used by managed exports so their
ledger and manifest hashes cannot drift from the implementation. This directory
is reserved for portable contract examples; it intentionally contains no
machine paths, model caches, secrets, or live Chroma database files.
