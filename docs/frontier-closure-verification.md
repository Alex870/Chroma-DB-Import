# Frontier Closure Verification (historical)

Verified: 2026-07-13

- The former BGE verification record is historical and is not a supported import representation.
- Current production embedding: `Qwen/Qwen3-Embedding-4B@5cf2132abc99cad020ac570b19d031efec650f2b`, 2,560 dimensions, normalized, cosine distance.
- Temporary Chroma integration covers added, changed, metadata-only, unchanged, and source-bounded removed records.
- The desktop UI is exercised offscreen and presents reconciliation before deletion consent.
- Local model probe: CPU and CUDA produced normalized 2,560-dimensional vectors; rerun the Qwen3 batch-size 1 and 2 smoke tests before promotion.
- Hardware probe: NVIDIA GeForce RTX 5070 Ti, CUDA-enabled PyTorch `2.7.1+cu128`.
- GitHub Actions targets Windows and Ubuntu with Python 3.12. Hosted CI remains CPU-only; CUDA is an explicit local verification path.
