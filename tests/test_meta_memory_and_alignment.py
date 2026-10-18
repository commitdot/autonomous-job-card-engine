"""
Unit Tests for Harnessing-Level Meta-Memory, Lesson Extraction, RAG Indexing, and Alignment Cascades
"""

import os
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src.models import ChildCard, MotherCard, GovernancePolicies
from src.recursion.state_manager import (
    RecursionStateManager,
    CandidateProbe,
    ContrastMatrix,
    LessonLearned,
)
from src.rag.indexer import LocalRAGIndexer
from src.rag.retriever import LocalRAGRetriever
from src.alignment_engine import AlignmentEngine
from src.daemon import AutonomousJobCardEngine
from src.adapters.sim_adapter import SimAdapter
from src.privacy_guard import PrivacyGuard
from src.sandbox import SandboxRunner


class TestMetaMemoryAndAlignment(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.jobs_dir = os.path.join(self.test_dir, ".jobs")
        self.recursion_dir = os.path.join(self.jobs_dir, "recursion")
        self.rag_dir = os.path.join(self.jobs_dir, "rag")
        os.makedirs(self.recursion_dir, exist_ok=True)
        os.makedirs(self.rag_dir, exist_ok=True)

        self.state_mgr = RecursionStateManager(workspace_root=self.test_dir)
        self.alignment_engine = AlignmentEngine(workspace_root=self.test_dir, max_wave_depth=3)
        self.rag_indexer = LocalRAGIndexer(workspace_root=self.test_dir, storage_dir=self.rag_dir)
        self.rag_retriever = LocalRAGRetriever(workspace_root=self.test_dir, storage_dir=self.rag_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_lesson_extraction_from_contrast_and_probes(self):
        """Test synthesizing lessons from contrast matrices and probe fault frames."""
        child = ChildCard(
            id="child-jwt-auth",
            parent_mother_id="mother-test",
            name="JWT Auth Service",
            parent_squad_id="squad-backend",
            tactical_objective="Implement JWT RS256 token verification and error handling",
            deliverables=[{"path": "src/auth.py", "description": "JWT verifier"}],
            wave_depth=1,
        )

        contrast = ContrastMatrix(
            contrast_id="contrast-jwt-1",
            card_id="child-jwt-auth",
            iteration=1,
            candidate_ids=["probe-1", "probe-2"],
            differences=[{"component": "guardrails", "delta": "Enforced RS256 algorithm validation"}],
            fault_frames=[{"file": "src/auth.py", "reason": "Missing public key decode param"}],
            winner_probe_id="probe-2",
            synthesis_rationale="Candidate probe 2 successfully handled public key decoding with algorithm RS256.",
        )

        probe_fail = CandidateProbe(
            probe_id="probe-1",
            card_id="child-jwt-auth",
            iteration=1,
            status="failed",
            fault_frames=[{"file": "src/auth.py", "reason": "jwt.exceptions.InvalidAlgorithmError: The specified alg value is not allowed"}],
        )
        probe_pass = CandidateProbe(
            probe_id="probe-2",
            card_id="child-jwt-auth",
            iteration=1,
            status="success",
            score=1.0,
        )

        lessons = self.state_mgr.extract_lessons_from_task(
            child=child,
            contrast=contrast,
            probes=[probe_fail, probe_pass],
            task_fingerprint="fp-12345",
        )

        self.assertGreaterEqual(len(lessons), 2)
        # Should have contrast lesson, probe fault frame lesson, and task completion lesson
        summaries = [l.summary for l in lessons]
        self.assertTrue(any("Candidate probe 2" in s or "contrast synthesis" in s.lower() for s in summaries))
        self.assertTrue(any("InvalidAlgorithmError" in s or "Resolved fault" in s for s in summaries))
        self.assertTrue(any("Verified implementation pattern" in s for s in summaries))

        # Test recording and deduplication
        recorded = self.state_mgr.extract_and_record_lessons(
            child=child,
            contrast=contrast,
            probes=[probe_fail, probe_pass],
            task_fingerprint="fp-12345",
        )
        self.assertEqual(len(recorded), len(lessons))

        # Re-running extraction does not duplicate
        recorded_again = self.state_mgr.extract_and_record_lessons(
            child=child,
            contrast=contrast,
            probes=[probe_fail, probe_pass],
            task_fingerprint="fp-12345",
        )
        stored_lessons = self.state_mgr.load_lessons()
        self.assertEqual(len(stored_lessons), len(recorded))

    def test_rag_indexing_lessons_learned_json(self):
        """Test that .jobs/recursion/lessons_learned.json is parsed and indexed into RAG chunks."""
        lesson1 = LessonLearned(
            lesson_id="lesson-pydantic-v2",
            category="library_quirk",
            summary="Pydantic v2 requires model_validate instead of parse_obj",
            details="Calling parse_obj raises DeprecationWarning in Pydantic v2. Use Model.model_validate(data).",
            source_card_id="child-schema-01",
            tags=["pydantic", "python", "schema"],
            confidence=0.95,
        )
        lesson2 = LessonLearned(
            lesson_id="lesson-pytest-async",
            category="test_strategy",
            summary="Async pytest requires pytest-asyncio with asyncio_mode=auto",
            details="Configure asyncio_mode=auto in pytest.ini to avoid coroutine not awaited warnings.",
            source_card_id="child-async-02",
            tags=["pytest", "async", "python"],
            confidence=0.90,
        )

        self.state_mgr.save_lessons([lesson1, lesson2])

        # Index lessons using RAG indexer
        count = self.rag_indexer.index_lessons()
        self.assertEqual(count, 2)

        # Retrieve lesson via RAG
        results = self.rag_retriever.query("pydantic model validate", top_k=2)
        self.assertGreater(len(results), 0)
        self.assertIn("pydantic", results[0]["content"].lower())
        self.assertIn("model_validate", results[0]["content"])

        # Retrieve second lesson
        results_async = self.rag_retriever.query("asyncio pytest coroutine", top_k=1)
        self.assertGreater(len(results_async), 0)
        self.assertIn("pytest-asyncio", results_async[0]["content"])

    def test_alignment_engine_depth_aware_fingerprints(self):
        """Test AlignmentEngine uses recursion/wave depth in task deduplication fingerprints."""
        engine = AlignmentEngine(workspace_root=self.test_dir, max_wave_depth=3)

        fp_depth1 = engine.calculate_task_fingerprint(
            parent_squad_id="squad-qa",
            tactical_objective="Synchronize unit tests for auth",
            deliverable_paths=["tests/test_auth.py"],
            recursion_depth=1,
        )
        fp_depth2 = engine.calculate_task_fingerprint(
            parent_squad_id="squad-qa",
            tactical_objective="Synchronize unit tests for auth",
            deliverable_paths=["tests/test_auth.py"],
            recursion_depth=2,
        )
        fp_depth1_repeat = engine.calculate_task_fingerprint(
            parent_squad_id="squad-qa",
            tactical_objective="Synchronize unit tests for auth",
            deliverable_paths=["tests/test_auth.py"],
            recursion_depth=1,
        )

        # Same depth yields same fingerprint
        self.assertEqual(fp_depth1, fp_depth1_repeat)
        # Different depths yield different fingerprints
        self.assertNotEqual(fp_depth1, fp_depth2)

        # Generate alignment cards checks wave_depth
        src_file = os.path.join(self.test_dir, "src", "token.py")
        os.makedirs(os.path.dirname(src_file), exist_ok=True)
        with open(src_file, "w", encoding="utf-8") as f:
            f.write("def issue_token(): pass\n")

        cards_wave1 = engine.generate_alignment_cards(
            completed_child_id="child-101",
            completed_child_name="Token Issuer",
            parent_squad_id="squad-backend",
            current_wave_depth=1,
            modified_files=["src/token.py"],
        )
        self.assertGreater(len(cards_wave1), 0)
        self.assertEqual(cards_wave1[0]["wave_depth"], 2)

        # Repeating for same depth should be deduplicated
        cards_wave1_dup = engine.generate_alignment_cards(
            completed_child_id="child-101",
            completed_child_name="Token Issuer",
            parent_squad_id="squad-backend",
            current_wave_depth=1,
            modified_files=["src/token.py"],
        )
        self.assertEqual(len(cards_wave1_dup), 0)

    def test_daemon_post_task_lesson_extraction_and_cross_task_transfer(self):
        """Test daemon extracts lessons upon task completion and transfers memory to subsequent tasks."""
        llm = SimAdapter(workspace_root=self.test_dir)
        daemon = AutonomousJobCardEngine(workspace_root=self.test_dir, adapter=llm)
        daemon.privacy_guard = PrivacyGuard(restricted_paths=[])
        daemon.sandbox = SandboxRunner()

        mother = MotherCard(
            id="mother-test",
            name="Institutional Memory Test",
            core_philosophy="Autonomous Harnessing",
            governance=GovernancePolicies(enable_harness2=True, max_recursion_depth=3),
        )
        mother_path = os.path.join(self.jobs_dir, "mother_card.yaml")
        mother.to_yaml(mother_path)

        # Task 1 (System 2 with deliverables)
        child1 = ChildCard(
            id="child-db-pool",
            parent_mother_id=mother.id,
            name="Database Connection Pool",
            parent_squad_id="squad-database",
            tactical_objective="Configure asyncpg connection pool with max_queries and timeout",
            deliverables=[{"path": "src/db.py", "description": "Database connection pool"}],
            validation_commands=["python -c \"print('db pool tests passed')\""],
            max_iterations=2,
            phase="Pending",
        )
        child1_path = os.path.join(self.jobs_dir, f"child_{child1.id}.yaml")
        child1.to_yaml(child1_path)

        # Execute Task 1
        res1 = daemon.execute_single_child(child=child1, mother=mother, child_path=child1_path)
        self.assertEqual(res1["phase"], "Completed")

        # Verify lessons were persisted
        lessons = daemon.recursion_state.load_lessons()
        self.assertGreater(len(lessons), 0)
        self.assertTrue(any("Database Connection Pool" in l.summary or "child-db-pool" == l.source_card_id for l in lessons))

        # Verify RAG index was updated with lessons
        rag_results = daemon.rag_retriever.query("asyncpg connection pool database", top_k=1)
        self.assertGreater(len(rag_results), 0)

        # Task 2 queries database pool pattern and retrieves Task 1 lesson
        relevant_lessons = daemon.recursion_state.get_relevant_lessons("asyncpg database connection pool")
        self.assertGreater(len(relevant_lessons), 0)
        self.assertEqual(relevant_lessons[0].source_card_id, "child-db-pool")


if __name__ == "__main__":
    unittest.main()
