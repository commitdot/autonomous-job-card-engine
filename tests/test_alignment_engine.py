import os
import tempfile
import unittest
from src.alignment_engine import AlignmentEngine
from src.rag.indexer import LocalRAGIndexer
from src.rag.retriever import LocalRAGRetriever


class TestAlignmentEngine(unittest.TestCase):
    def test_blast_radius_and_qa_cascade(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a mock repo structure
            src_dir = os.path.join(tmpdir, "src")
            tests_dir = os.path.join(tmpdir, "tests")
            docs_dir = os.path.join(tmpdir, "docs")
            os.makedirs(src_dir, exist_ok=True)
            os.makedirs(tests_dir, exist_ok=True)
            os.makedirs(docs_dir, exist_ok=True)

            auth_file = os.path.join(src_dir, "auth.py")
            with open(auth_file, "w", encoding="utf-8") as f:
                f.write("def login(user, password):\n    return True\n")

            test_file = os.path.join(tests_dir, "test_auth.py")
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("from src.auth import login\ndef test_login(): assert login('a', 'b')\n")

            engine = AlignmentEngine(workspace_root=tmpdir, max_wave_depth=3)
            blast = engine.calculate_blast_radius(["src/auth.py"])

            self.assertIn("tests/test_auth.py", blast["impacted_files"])
            self.assertTrue(blast["requires_qa_sync"])

            # Generate alignment cards
            cards = engine.generate_alignment_cards(
                completed_child_id="child-101",
                completed_child_name="Auth Service",
                parent_squad_id="squad-backend",
                current_wave_depth=1,
                modified_files=["src/auth.py"],
            )

            self.assertTrue(len(cards) > 0)
            qa_cards = [c for c in cards if c["parent_squad_id"] == "squad-qa"]
            self.assertEqual(len(qa_cards), 1)
            self.assertEqual(qa_cards[0]["wave_depth"], 2)
            self.assertIn("child-101", qa_cards[0]["depends_on"])

    def test_cryptographic_deduplication_and_horizon_ceiling(self):
        engine = AlignmentEngine(workspace_root=".", max_wave_depth=2)
        fp1 = engine.calculate_task_fingerprint("squad-qa", "Run tests", ["tests/test_a.py"])
        fp2 = engine.calculate_task_fingerprint("squad-qa", "Run tests", ["tests/test_a.py"])
        self.assertEqual(fp1, fp2)

        engine.register_task_fingerprint(fp1)
        self.assertTrue(engine.is_task_seen(fp1))

        # Test horizon ceiling blocks wave >= max_wave_depth
        cards = engine.generate_alignment_cards(
            completed_child_id="child-202",
            completed_child_name="Deep Task",
            parent_squad_id="squad-backend",
            current_wave_depth=2,
            modified_files=["src/core.py"],
        )
        self.assertEqual(len(cards), 0)

    def test_incremental_rag_reindexing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_dir = os.path.join(tmpdir, "docs")
            rag_dir = os.path.join(tmpdir, ".jobs", "rag")
            os.makedirs(docs_dir, exist_ok=True)

            doc_path = os.path.join(docs_dir, "architecture.md")
            with open(doc_path, "w", encoding="utf-8") as f:
                f.write("# Architecture Overview\n\nThe payment engine uses tokenized vaulted credit cards.\n\nAll transactions are immutable.")

            indexer = LocalRAGIndexer(workspace_root=tmpdir, storage_dir=rag_dir)
            count = indexer.index_paths(["docs/"])
            self.assertGreater(count, 0)

            # Modify file and reindex incrementally
            with open(doc_path, "w", encoding="utf-8") as f:
                f.write("# Architecture Overview\n\nThe payment engine now supports cryptocurrency settlement.\n\nAll transactions are cryptographic.")

            new_count = indexer.reindex_file("docs/architecture.md")
            self.assertGreater(new_count, 0)

            retriever = LocalRAGRetriever(workspace_root=tmpdir, storage_dir=rag_dir)
            results = retriever.query("cryptocurrency settlement", top_k=1)
            self.assertTrue(len(results) > 0)
            self.assertIn("cryptocurrency", results[0]["content"].lower())


if __name__ == "__main__":
    unittest.main()
