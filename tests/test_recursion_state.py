"""
Unit Tests for Harness² Persistent Recursion State Infrastructure & Storage
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
import yaml

from src.models import GovernancePolicies, MotherCard, ChildCard
from src.recursion.state_manager import (
    RecursionStateManager,
    CandidateProbe,
    ContrastMatrix,
    LessonLearned,
)


class TestRecursionState(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.state_mgr = RecursionStateManager(workspace_root=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_directory_initialization(self):
        """Verify storage paths and initial lessons file exist."""
        recursion_dir = Path(self.test_dir) / ".jobs" / "recursion"
        self.assertTrue((recursion_dir / "probes").exists())
        self.assertTrue((recursion_dir / "contrasts").exists())
        self.assertTrue((recursion_dir / "lessons_learned.json").exists())

    def test_candidate_probe_save_and_load(self):
        """Test saving and loading individual and batch candidate probes."""
        probe1 = CandidateProbe(
            probe_id="probe-001",
            card_id="child-101",
            iteration=1,
            harness_config={"system_prompt": "You are a backend engineer"},
            status="success",
            stdout="Tests passed (5/5)",
            score=1.0,
            test_results={"passed": 5, "failed": 0},
        )
        probe2 = CandidateProbe(
            probe_id="probe-002",
            card_id="child-101",
            iteration=1,
            harness_config={"system_prompt": "Alternative harness"},
            status="failed",
            stderr="AssertionError: 401 != 200",
            score=0.5,
            fault_frames=[{"file": "auth.py", "line": 42}],
            test_results={"passed": 3, "failed": 2},
        )

        saved_ids = self.state_mgr.save_candidate_probes("child-101", [probe1, probe2], iteration=1)
        self.assertEqual(saved_ids, ["probe-001", "probe-002"])

        loaded = self.state_mgr.load_candidate_probes("child-101", iteration=1)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0].probe_id, "probe-001")
        self.assertEqual(loaded[0].status, "success")
        self.assertEqual(loaded[1].probe_id, "probe-002")
        self.assertEqual(loaded[1].score, 0.5)

        # Test single get
        single = self.state_mgr.get_probe("child-101", "probe-001")
        self.assertIsNotNone(single)
        self.assertEqual(single.probe_id, "probe-001")

        # Test listing across all probes
        all_probes = self.state_mgr.list_probes()
        self.assertEqual(len(all_probes), 2)

    def test_probe_update_in_place(self):
        """Test that re-saving probe with same probe_id updates it."""
        probe = CandidateProbe(
            probe_id="probe-update-1",
            card_id="child-201",
            iteration=1,
            status="pending"
        )
        self.state_mgr.save_candidate_probe(probe)

        probes_before = self.state_mgr.load_candidate_probes("child-201")
        self.assertEqual(len(probes_before), 1)
        self.assertEqual(probes_before[0].status, "pending")

        # Update status
        probe.status = "success"
        probe.score = 0.95
        self.state_mgr.save_candidate_probe(probe)

        probes_after = self.state_mgr.load_candidate_probes("child-201")
        self.assertEqual(len(probes_after), 1)
        self.assertEqual(probes_after[0].status, "success")
        self.assertEqual(probes_after[0].score, 0.95)

    def test_contrast_matrix_save_and_load(self):
        """Test saving and loading behavioral contrast matrices."""
        contrast = ContrastMatrix(
            contrast_id="contrast-001",
            card_id="child-101",
            iteration=1,
            candidate_ids=["probe-001", "probe-002"],
            differences=[{"component": "guardrails", "delta": "Added token expiration check"}],
            fault_frames=[{"file": "auth.py", "reason": "Missing bearer prefix"}],
            winner_probe_id="probe-001",
            composed_deliverables={"auth.py": "def verify_token(): pass"},
            synthesis_rationale="Candidate probe 1 successfully validated bearer token lifecycle.",
        )

        contrast_id = self.state_mgr.save_contrast_matrix(contrast)
        self.assertEqual(contrast_id, "contrast-001")

        loaded = self.state_mgr.load_contrast_matrix("child-101", iteration=1)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.contrast_id, "contrast-001")
        self.assertEqual(loaded.winner_probe_id, "probe-001")
        self.assertEqual(loaded.composed_deliverables["auth.py"], "def verify_token(): pass")

        # Test listing
        all_contrasts = self.state_mgr.list_contrasts("child-101")
        self.assertEqual(len(all_contrasts), 1)

    def test_institutional_lessons_persistence(self):
        """Test recording, querying, filtering, and deduplicating lessons learned."""
        lesson1 = LessonLearned(
            lesson_id="lesson-jwt-01",
            category="library_quirk",
            summary="PyJWT requires algorithms=['HS256'] explicitly in decode",
            details="Calling jwt.decode without algorithms raises InvalidAlgorithmError in PyJWT >= 2.0",
            source_card_id="child-jwt",
            tags=["jwt", "auth", "security"],
            confidence=0.98,
        )
        lesson2 = LessonLearned(
            lesson_id="",  # Auto-generated ID
            category="compiler_flag",
            summary="TypeScript strictNullChecks prevents undefined jwt payload access",
            details="Enable strictNullChecks in tsconfig to catch null claims before runtime",
            source_card_id="child-ts",
            tags=["typescript", "jwt"],
            confidence=0.90,
        )

        rec1 = self.state_mgr.record_lesson(lesson1)
        rec2 = self.state_mgr.record_lesson(lesson2)

        self.assertEqual(rec1.lesson_id, "lesson-jwt-01")
        self.assertTrue(rec2.lesson_id.startswith("lesson-"))

        # Category filter
        quirk_lessons = self.state_mgr.load_lessons(category="library_quirk")
        self.assertEqual(len(quirk_lessons), 1)
        self.assertEqual(quirk_lessons[0].summary, lesson1.summary)

        # Tag filter
        jwt_lessons = self.state_mgr.load_lessons(tag="jwt")
        self.assertEqual(len(jwt_lessons), 2)

        # Relevance search
        relevant = self.state_mgr.get_relevant_lessons("PyJWT decode token error")
        self.assertTrue(len(relevant) > 0)
        self.assertEqual(relevant[0].lesson_id, "lesson-jwt-01")

        # Deduplication test
        duplicate_lesson = LessonLearned(
            lesson_id="lesson-jwt-dup",
            category="library_quirk",
            summary="PyJWT requires algorithms=['HS256'] explicitly in decode",
            details="Duplicated lesson text",
            source_card_id="child-jwt-2",
        )
        # Manually save duplicate to list
        self.state_mgr.save_lessons([lesson1, lesson2, duplicate_lesson])
        self.assertEqual(len(self.state_mgr.load_lessons()), 3)

        removed_count = self.state_mgr.deduplicate_lessons()
        self.assertEqual(removed_count, 1)
        self.assertEqual(len(self.state_mgr.load_lessons()), 2)

    def test_governance_policies_extension(self):
        """Test GovernancePolicies extension with enable_harness2 and max_recursion_depth."""
        gov = GovernancePolicies()
        self.assertFalse(gov.enable_harness2)
        self.assertEqual(gov.max_recursion_depth, 3)

        gov_custom = GovernancePolicies(enable_harness2=True, max_recursion_depth=5)
        self.assertTrue(gov_custom.enable_harness2)
        self.assertEqual(gov_custom.max_recursion_depth, 5)

        # Verify MotherCard serialization & deserialization preserves flags
        mother_yaml_path = os.path.join(self.test_dir, ".jobs", "mother_card.yaml")
        mother = MotherCard(
            id="mother-test",
            name="Harness2 Mother Card",
            core_philosophy="Autonomous recursion",
            governance=gov_custom,
        )
        mother.to_yaml(mother_yaml_path)

        loaded_mother = MotherCard.from_yaml(mother_yaml_path)
        self.assertTrue(loaded_mother.governance.enable_harness2)
        self.assertEqual(loaded_mother.governance.max_recursion_depth, 5)

    def test_child_card_runtime_recursion_state_and_yaml_compatibility(self):
        """Test ChildCard non-serialized _recursion_state backward compatibility."""
        child = ChildCard(
            id="child-test-01",
            parent_mother_id="mother-test",
            name="Test Task",
            tactical_objective="Validate recursion state attachment",
        )

        # Check default state
        self.assertEqual(child._recursion_state, {})

        # Attach recursion state via state manager
        self.state_mgr.attach_recursion_state(
            child,
            recursion_depth=2,
            enable_harness2=True,
            probes=[CandidateProbe(probe_id="p1", card_id="child-test-01")],
        )
        self.assertEqual(child._recursion_state["recursion_depth"], 2)
        self.assertTrue(child._recursion_state["enable_harness2"])
        self.assertEqual(child._recursion_state["probes_count"], 1)

        # Update recursion state
        self.state_mgr.update_recursion_state(child, best_score=0.98, status="composed")
        self.assertEqual(child._recursion_state["best_score"], 0.98)
        self.assertEqual(child._recursion_state["status"], "composed")

        # Save to YAML
        child_yaml_path = os.path.join(self.test_dir, ".jobs", "child_test_01.yaml")
        child.to_yaml(child_yaml_path)

        # Read raw YAML file to ensure _recursion_state is NOT persisted into YAML
        with open(child_yaml_path, "r", encoding="utf-8") as f:
            raw_yaml = yaml.safe_load(f)

        self.assertNotIn("_recursion_state", raw_yaml)
        self.assertNotIn("_recursion_state", raw_yaml.get("spec", {}))
        self.assertNotIn("_recursion_state", raw_yaml.get("status", {}))
        self.assertNotIn("_recursion_state", raw_yaml.get("metadata", {}))

        # Re-load ChildCard from YAML and ensure valid clean instance
        reloaded = ChildCard.from_yaml(child_yaml_path)
        self.assertEqual(reloaded.id, "child-test-01")
        self.assertEqual(reloaded._recursion_state, {})


if __name__ == "__main__":
    unittest.main()
