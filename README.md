# Chroma DB Import

Imports pre-processed podcast RAG documents into a persistent Chroma collection.

This project intentionally does not call LM Studio and does not perform transcript preprocessing. It expects processed cache files produced by `Podcast-RAG-pipeline` under `processed_data`.

The original CLI remains available. The UI workflow adds folder picking, episode stats, speaker inclusion controls, rebuild/update import modes, and `podcast.json` metadata generation for `PodCast Chat`.

## UI Workflow

```powershell
.\Run Chroma DB Import UI.ps1
```

Use the toolbar from left to right:

- `Open`: choose a folder containing `*.processed_documents.json` files, such as `Podcast-RAG-pipeline\processed_data`.
- `Output`: choose a folder where self-contained exports will be written.
- `Generate`: rebuilds the selected podcast export from scratch. If the export folder already exists, the UI prompts before deleting it.
- `Update`: imports only episodes not already listed in the generated `podcast.json`.
- `Settings`: edit podcast/output/settings/speaker selections. Use `Save Settings` / `Load Settings` inside this page to persist and restore them.
- `Guide`: view the expected new-export and update workflows.

The UI startup script prints a CUDA/PyTorch diagnosis before launching. To install a CUDA-enabled PyTorch build into the UI virtual environment, run:

```powershell
.\Run Chroma DB Import UI.ps1 -InstallCudaTorch
```

By default this uses the PyTorch CUDA 12.8 wheel index:

```text
https://download.pytorch.org/whl/cu128
```

This is separate from the NVIDIA driver-reported CUDA version. A newer NVIDIA driver can normally run applications built against an older CUDA runtime such as the PyTorch CUDA 12.8 wheel.

The export layout is:

```text
Selected Output Folder/
  Podcast Name/
    chroma.sqlite3
    podcast.json
    ...Chroma internal files...
```

The tree has a single `Global Settings` root node. Episode nodes are ordered by ascending episode date. Speaker checkboxes under `Global Settings` are tri-state: checked means included for every episode where that speaker appears, unchecked means excluded everywhere, and partially checked means included for some episodes but not all.

Excluded speakers are omitted from the generated metadata and their speaker-specific documents are not inserted. Episode-level thesis documents and multi-speaker summary nodes are preserved to keep retrieval context useful.

## Setup

```powershell
.\Run Chroma DB Import.ps1 -CreateCondaEnv
Copy-Item .\chroma_db_import_config.example.json .\chroma_db_import_config.json
```

Edit `chroma_db_import_config.json` so `processed_data_dir` points at the preprocessed cache directory and `persist_dir` points at the Chroma database directory you want to populate.

To create or update the Conda CLI environment with CUDA-enabled PyTorch:

```powershell
.\Run Chroma DB Import.ps1 -CreateCondaEnv -InstallCudaTorch
```

or, for an existing environment:

```powershell
.\Run Chroma DB Import.ps1 -InstallCudaTorch -SkipDependencyCheck
```

## Test

```powershell
.\Test Chroma DB Import Environment.ps1
```

The test script checks `nvidia-smi`, PyTorch version, PyTorch CUDA runtime, `torch.cuda.is_available()`, and CUDA device names when available. If `nvidia-smi` sees the GPU but PyTorch reports CUDA unavailable, reinstall PyTorch with:

```powershell
conda run -n chroma-db-import python -m pip install --upgrade --force-reinstall torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```

## Import

```powershell
.\Run Chroma DB Import.ps1
```

Useful options:

```powershell
.\Run Chroma DB Import.ps1 -OneFile
.\Run Chroma DB Import.ps1 -ProcessedDataDir "D:\Pod Cast RAG\Podcast-RAG-pipeline\processed_data"
.\Run Chroma DB Import.ps1 -PersistDir "D:\Pod Cast RAG\Podcast-RAG-pipeline\chroma_db_raptor_v2"
.\Run Chroma DB Import.ps1 -CollectionName "whisper_rag_v2"
```

Progress is tracked in `state/chroma_import_state.json`. If `skip_existing_ids` is enabled, documents whose `node_id` already exists in Chroma are skipped.
