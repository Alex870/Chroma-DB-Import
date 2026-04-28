# Chroma DB Import

Imports pre-processed podcast RAG documents into a persistent Chroma collection.

This project intentionally does not call LM Studio and does not perform transcript preprocessing. It expects processed cache files produced by `Podcast-RAG-pipeline` under `processed_data`.

## Setup

```powershell
.\Run Chroma DB Import.ps1 -CreateCondaEnv
Copy-Item .\chroma_db_import_config.example.json .\chroma_db_import_config.json
```

Edit `chroma_db_import_config.json` so `processed_data_dir` points at the preprocessed cache directory and `persist_dir` points at the Chroma database directory you want to populate.

## Test

```powershell
.\Test Chroma DB Import Environment.ps1
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
