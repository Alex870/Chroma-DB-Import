import json, tempfile, unittest
from pathlib import Path
from chroma_db_import.advanced_sidecars import (
    AdvancedSidecarError,
    build_package,
    maxsim_search,
    validate_package,
)


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class AdvancedSidecarTests(unittest.TestCase):
    def values(self):
        return (
            {
                "contract_version": "evidence-graph-1.0",
                "disposition": "prototype",
                "parent_corpus_release_id": "r1",
                "entry_gate_id": "g",
                "graph_id": "graph",
                "nodes": [],
                "edges": [],
            },
            {
                "contract_version": "late-chunk-alignment-1.0",
                "disposition": "prototype",
                "parent_corpus_release_id": "r1",
                "alignment_id": "align",
                "encoder": {
                    "model_id": "m",
                    "model_revision": "rev",
                    "tokenizer_id": "t",
                },
                "documents": [{"document_id": "d1"}, {"document_id": "d2"}],
            },
            {
                "dimension": 2,
                "documents": [
                    {"document_id": "d1", "vectors": [[1, 0], [0, 1]]},
                    {"document_id": "d2", "vectors": [[-1, 0]]},
                ],
            },
        )

    def test_build_validate_and_bounded_maxsim(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as raw:
            root = Path(raw)
            g, a, v = self.values()
            package = build_package(
                write(root / "g.json", g),
                write(root / "a.json", a),
                write(root / "v.json", v),
                root / "out",
                release_id="r1",
            )
            self.assertEqual(
                "prototype", validate_package(package, release_id="r1")["disposition"]
            )
            index = json.loads((package / "multi-vector-index.json").read_text())
            self.assertEqual(
                [{"document_id": "d1", "score": 1.0}],
                maxsim_search(index, [[1, 0]], candidate_ids={"d1"}),
            )
            second = build_package(
                root / "g.json",
                root / "a.json",
                root / "v.json",
                root / "second-output",
                release_id="r1",
            )
            self.assertEqual(package.name, second.name)

    def test_rejects_alignment_drift(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as raw:
            root = Path(raw)
            g, a, v = self.values()
            v["documents"] = [{"document_id": "wrong", "vectors": [[1, 0]]}]
            with self.assertRaisesRegex(AdvancedSidecarError, "exactly align"):
                build_package(
                    write(root / "g", g),
                    write(root / "a", a),
                    write(root / "v", v),
                    root / "out",
                    release_id="r1",
                )


if __name__ == "__main__":
    unittest.main()
