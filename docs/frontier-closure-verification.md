# Frontier Closure Verification

Verified: 2026-07-13

- Baseline embedding: `BAAI/bge-large-en-v1.5@d4aa6901d3a41ba39fb536a557fa166f842b0e09`.
- Experimental embedding: `BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181`.
- Temporary Chroma integration covers added, changed, metadata-only, unchanged, and source-bounded removed records.
- The desktop UI is exercised offscreen and presents reconciliation before deletion consent.
- Local model probe: CPU and CUDA produced normalized 1024-dimensional vectors; maximum absolute device delta was `1.6391277313232422e-07`.
- Hardware probe: NVIDIA GeForce RTX 5070 Ti, CUDA-enabled PyTorch `2.7.1+cu128`.
- GitHub Actions targets Windows and Ubuntu with Python 3.12. Hosted CI remains CPU-only; CUDA is an explicit local verification path.
