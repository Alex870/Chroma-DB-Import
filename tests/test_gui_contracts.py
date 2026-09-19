import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from chroma_db_import.contract import content_fingerprint
from chroma_db_import.deduplication import load_managed_dedup_inputs
from chroma_db_import.redundancy_artifacts import write_bundle
from chroma_db_import.redundancy_models import Scope
from chroma_db_import.workflow.environment_repair import build_review, run_review
from chroma_db_import.workflow.models import SelectionPolicy
from chroma_db_import.workflow.redundancy_adapter import evaluate_export, label_export
from chroma_db_import.workflow.settings_transfer import apply_import, build_transfer, preview_import, read_transfer


class GuiContractTests(unittest.TestCase):
    def test_modern_managed_selection_is_applied_before_dedup_planning(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "episode.processed_documents.json"
            path.write_text(json.dumps({"documents": [
                {"page_content": "host text", "metadata": {"node_id": "host", "episode_id": "ep1", "episode_uid": "part:ep1", "partition_id": "part", "corpus_id": "corpus", "speaker": "Host"}},
                {"page_content": "guest text", "metadata": {"node_id": "guest", "episode_id": "ep1", "episode_uid": "part:ep1", "partition_id": "part", "corpus_id": "corpus", "speaker": "Guest"}},
            ]}), encoding="utf-8")
            identity = SimpleNamespace(partition_id="part", corpus_id="corpus")
            upstream = {"episode_uids": ["part:ep1"], "processed_cache_fingerprints": [content_fingerprint(path)]}
            policy = SelectionPolicy(speaker_mode="allowlist", allowlist_speakers=["Host"])
            selected = load_managed_dedup_inputs([path], identity, upstream, selection_policy=policy)
            self.assertEqual([item.effective_id for item in selected.inputs], ["host"])
            empty = load_managed_dedup_inputs([path], identity, upstream, selection_policy=SelectionPolicy(speaker_mode="allowlist"))
            self.assertEqual(empty.inputs, [])
            self.assertEqual(empty.excluded_reasons["selection_filtered"], 2)

    def test_scoped_transfer_preserves_omitted_fields_and_rejects_stale_apply(self):
        transfer = build_transfer("database", {"database_id": "db-1"}, {"selection_policy": {"speaker_mode": "allowlist", "allowlist_speakers": []}})
        current = {"selection_policy": {"speaker_mode": "all"}, "execution_options": {"embedding_device": "auto"}}
        scope = {"scope_kind": "database", "identity": {"database_id": "db-1"}, "revision": 4}
        proposal = preview_import(transfer, scope, current, base_revision=4)
        result = apply_import(proposal, current, ["selection_policy"], current_revision=4, transfer=transfer)
        self.assertEqual(result["value"]["selection_policy"]["allowlist_speakers"], [])
        self.assertEqual(result["value"]["execution_options"], current["execution_options"])
        with self.assertRaises(Exception):
            apply_import(proposal, {**current, "execution_options": {"embedding_device": "cpu"}}, ["selection_policy"], current_revision=4, transfer=transfer)

    def test_environment_repair_review_uses_backend_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / "scripts" / "Run-ChromaDbImportUi.ps1"
            launcher.parent.mkdir()
            launcher.write_text("Write-Output ready", encoding="utf-8")
            review = build_review(root)
            captured = {}

            def runner(args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            result = run_review(review, root=root, runner=runner)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(captured["args"][0], "powershell.exe")
            self.assertIn("-InstallDependencies", captured["args"])
            self.assertFalse(captured["kwargs"]["shell"])

    def test_legacy_settings_are_normalized_without_guessing_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ui_state.json"
            path.write_text(json.dumps({
                "database_id": "db-legacy",
                "asset_filter": "reviewed",
                "included_speakers_by_episode": {"episode-1": ["Host"]},
                "embedding_device": "cpu",
                "speaker_fingerprints": {"episode-1": ["ambiguous"]},
            }), encoding="utf-8")
            transfer = read_transfer(path, scope_kind="database", identity={"database_id": "db-target"})
            self.assertEqual(transfer["identity"]["database_id"], "db-legacy")
            self.assertEqual(transfer["settings"]["selection_policy"]["episode_overrides"], {"episode-1": ["Host"]})
            self.assertIn("speaker_fingerprints: no unique verified episode mapping", transfer["excluded_fields"])
            scope = {"scope_kind": "database", "identity": {"database_id": "db-target"}, "revision": 0}
            proposal = preview_import(transfer, scope, {"selection_policy": {}, "execution_options": {"embedding_device": "auto"}})
            self.assertEqual(proposal["compatibility"], "blocked")

    def test_redundancy_bundle_review_exports_labels_and_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            rows = [
                {"document_id": "a", "text": "same text", "metadata": {"episode_uid": "part:e1", "node_type": "leaf", "source_group_ids": ["shared"]}, "episode_uid": "part:e1", "node_type": "leaf"},
                {"document_id": "b", "text": "same text", "metadata": {"episode_uid": "part:e2", "node_type": "leaf", "source_group_ids": ["shared"]}, "episode_uid": "part:e2", "node_type": "leaf"},
            ]
            write_bundle(bundle, scope=Scope("part", "corpus", "release", "representation"), occurrences=rows, candidate_pairs=[{"candidate_id": "a::b", "left_id": "a", "right_id": "b", "channels": ["exact_text"], "scores": {}, "channel_ranks": {}, "guards": []}], storage_mode="full", coverage={"judge_status": "disabled"})
            labels_path = root / "labels.json"
            label_report = label_export(bundle, str(labels_path))
            self.assertEqual(label_report["kind"], "redundancy_label_export")
            labels = json.loads(labels_path.read_text(encoding="utf-8"))
            labels["pairs"][0].update({"relation": "equivalent", "material_difference": False, "reviewer": "test"})
            labels_path.write_text(json.dumps(labels), encoding="utf-8")
            evaluation_path = root / "evaluation-result.json"
            evaluation_report = evaluate_export(bundle, str(labels_path), output_path=str(evaluation_path))
            self.assertEqual(evaluation_report["kind"], "redundancy_evaluation")
            self.assertTrue(evaluation_path.is_file())

    def test_modern_window_injects_native_file_and_save_pickers(self):
        class Signal:
            def __iadd__(self, callback):
                self.callback = callback
                return self

        class Window:
            def __init__(self):
                self.events = SimpleNamespace(closing=Signal())
                self.dialogs = []

            def create_file_dialog(self, dialog_type, **kwargs):
                self.dialogs.append((dialog_type, kwargs))
                return ["C:/picked.json"]

        class Webview:
            FOLDER_DIALOG = "folder"
            OPEN_DIALOG = "open"
            SAVE_DIALOG = "save"

            def __init__(self):
                self.window = Window()
                self.kwargs = None

            def create_window(self, *args, **kwargs):
                self.kwargs = kwargs
                return self.window

        fake_webview = Webview()
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {"webview": fake_webview}):
            from chroma_db_import.desktop.window import create_window

            window, service = create_window(state_dir=Path(directory) / "state")
            try:
                bridge = fake_webview.kwargs["js_api"]
                self.assertEqual(bridge.pick_file({"purpose": "settings_import"})["data"], "C:/picked.json")
                self.assertEqual(bridge.pick_file({"purpose": "redundancy_bundle"})["data"], "C:/picked.json")
                self.assertEqual(bridge.pick_save_file({"purpose": "evaluation"})["data"], "C:/picked.json")
                self.assertEqual([item[0] for item in window.dialogs], ["open", "folder", "save"])
                self.assertEqual(window.dialogs[-1][1]["save_filename"], "evaluation.json")
            finally:
                service.shutdown()


if __name__ == "__main__":
    unittest.main()
