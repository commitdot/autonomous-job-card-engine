"""
tests/test_daemon_harness2.py
=============================
Unit tests for Harness² task-level recursion (Propose–Probe–Compose) in
AutonomousJobCardEngine.execute_single_child().

All tests are offline: SimAdapter is deterministic and probe worktrees fall
back to filesystem copies inside a temp directory.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.adapters.sim_adapter import SimAdapter
from src.daemon import AutonomousJobCardEngine
from src.models import ChildCard, GovernancePolicies, MotherCard
from src.privacy_guard import PrivacyGuard
from src.sandbox import SandboxRunner


def _make_mother(enable_harness2: bool, max_depth: int = 3) -> MotherCard:
    return MotherCard(
        id="m1",
        name="Mother",
        core_philosophy="test",
        governance=GovernancePolicies(enable_harness2=enable_harness2, max_recursion_depth=max_depth),
    )


def _make_child(validation_commands=None, deliverables=None, max_iterations: int = 2) -> ChildCard:
    return ChildCard(
        id="c1",
        parent_mother_id="m1",
        name="Child",
        tactical_objective="Implement the core module logic",
        deliverables=deliverables or [{"path": "src_out/module.py", "description": "core module"}],
        validation_commands=validation_commands if validation_commands is not None else [],
        max_iterations=max_iterations,
    )


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.engine = AutonomousJobCardEngine(workspace_root=self.root, adapter=SimAdapter(self.root))
        self.engine.privacy_guard = PrivacyGuard(restricted_paths=[])
        self.engine.sandbox = SandboxRunner()
        self.child_path = os.path.join(self.engine.jobs_dir, "child_c1.yaml")

    def tearDown(self):
        self._tmp.cleanup()

    def ledger_events(self):
        path = os.path.join(self.engine.jobs_dir, "audit_ledger.jsonl")
        with open(path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def ledger_types(self):
        return [b["event_type"] for b in self.ledger_events()]


class TestHarness2Gating(_Base):
    def test_disabled_governance_is_inactive(self):
        self.assertFalse(self.engine._is_harness2_active(_make_mother(False)))

    def test_enabled_governance_with_supporting_adapter_is_active(self):
        self.assertTrue(self.engine._is_harness2_active(_make_mother(True)))

    def test_adapter_without_support_is_inactive(self):
        self.engine.llm.supports_harness2 = lambda: False
        self.assertFalse(self.engine._is_harness2_active(_make_mother(True)))

    def test_disabled_runs_legacy_path_without_harness2_events(self):
        child = _make_child()
        result = self.engine.execute_single_child(child, _make_mother(False), self.child_path, self.root)
        self.assertEqual(result["phase"], "Completed")
        self.assertFalse([t for t in self.ledger_types() if t.startswith("HARNESS2")])
        self.assertEqual(self.engine.recursion_state.list_probes("c1"), [])

    def test_system1_fast_path_untouched_when_enabled(self):
        child = _make_child(deliverables=[{"path": "README.md", "description": "docs"}])
        result = self.engine.execute_single_child(child, _make_mother(True), self.child_path, self.root)
        self.assertEqual(result["phase"], "Completed")
        self.assertFalse([t for t in self.ledger_types() if t.startswith("HARNESS2")])
        self.assertEqual(self.engine.recursion_state.list_probes("c1"), [])


class TestHarness2ProbeCompose(_Base):
    def test_enabled_probes_and_composes_in_isolated_worktrees(self):
        child = _make_child()
        result = self.engine.execute_single_child(child, _make_mother(True), self.child_path, self.root)
        self.assertEqual(result["phase"], "Completed")

        probes = self.engine.recursion_state.load_candidate_probes("c1")
        self.assertEqual(len(probes), 3)
        statuses = {p.status for p in probes}
        self.assertIn("success", statuses)
        self.assertIn("failed", statuses)  # minimal candidate 0 has no guardrails

        # Composed deliverable committed into execution root
        committed = os.path.join(self.root, "src_out", "module.py")
        self.assertTrue(os.path.exists(committed))

        # Probe worktrees were cleaned up
        wt_dir = os.path.join(self.root, ".worktrees")
        self.assertEqual(os.listdir(wt_dir) if os.path.isdir(wt_dir) else [], [])

    def test_contrast_matrix_persisted_with_winner(self):
        child = _make_child()
        self.engine.execute_single_child(child, _make_mother(True), self.child_path, self.root)
        contrast = self.engine.recursion_state.load_contrast_matrix("c1", iteration=1)
        self.assertIsNotNone(contrast)
        self.assertEqual(len(contrast.candidate_ids), 3)
        self.assertIsNotNone(contrast.winner_probe_id)
        self.assertTrue(contrast.differences)
        self.assertTrue(contrast.fault_frames)

    def test_recursion_state_attached_to_child(self):
        child = _make_child()
        self.engine.execute_single_child(child, _make_mother(True), self.child_path, self.root)
        state = child._recursion_state
        self.assertEqual(state["recursion_depth"], 1)
        self.assertEqual(state["probes_count"], 3)
        self.assertTrue(state["enable_harness2"])
        self.assertEqual(state["last_contrast_id"], "contrast-c1-1")

    def test_failing_validation_everywhere_uses_contrast_in_reflection(self):
        captured = []
        original = self.engine.llm.execute_child_card

        def spy(**kwargs):
            captured.append(kwargs.get("previous_errors", ""))
            return original(**kwargs)

        self.engine.llm.execute_child_card = spy
        child = _make_child(
            validation_commands=["python -c exit(1)"],
            max_iterations=2,
        )
        result = self.engine.execute_single_child(child, _make_mother(True, max_depth=5), self.child_path, self.root)

        self.assertEqual(result["phase"], "Failed")
        # Every probe failed -> fallback single-path call receives behavioral contrast, not a blind retry
        self.assertTrue(captured)
        self.assertTrue(all("[BEHAVIORAL CONTRAST]" in e for e in captured))
        self.assertTrue(any("[BEHAVIORAL CONTRAST]" in line for line in child.logs))
        self.assertIsNone(self.engine.recursion_state.load_contrast_matrix("c1", iteration=1).winner_probe_id)

    def test_depth_limit_falls_back_to_single_path(self):
        child = _make_child()
        child._recursion_state["recursion_depth"] = 3
        result = self.engine.execute_single_child(child, _make_mother(True, max_depth=3), self.child_path, self.root)
        self.assertEqual(result["phase"], "Completed")
        types = self.ledger_types()
        self.assertIn("HARNESS2_DEPTH_LIMIT", types)
        self.assertNotIn("HARNESS2_PROPOSE", types)
        self.assertEqual(self.engine.recursion_state.list_probes("c1"), [])

    def test_composed_deliverable_outside_root_is_blocked(self):
        original = self.engine.llm.compose_harness

        def evil_compose(**kwargs):
            composed = original(**kwargs)
            composed.deliverables = {"../escaped.py": "print('x')"}
            return composed

        self.engine.llm.compose_harness = evil_compose
        exec_root = os.path.join(self.root, "jail")
        os.makedirs(exec_root)
        child = _make_child()
        self.engine.execute_single_child(child, _make_mother(True), self.child_path, exec_root)
        self.assertFalse(os.path.exists(os.path.join(self.root, "escaped.py")))
        self.assertIn("HARNESS2_CONTAINMENT_VIOLATION", self.ledger_types())


class TestHarness2AuditLedger(_Base):
    def test_probe_comparisons_and_composition_logged_to_ledger(self):
        child = _make_child()
        self.engine.execute_single_child(child, _make_mother(True), self.child_path, self.root)
        types = self.ledger_types()
        for expected in (
            "HARNESS2_ENABLED",
            "HARNESS2_PROPOSE",
            "HARNESS2_PROBE_COMPARISON",
            "HARNESS2_COMPOSITION_DECISION",
        ):
            self.assertIn(expected, types)

        comparison = next(b for b in self.ledger_events() if b["event_type"] == "HARNESS2_PROBE_COMPARISON")
        probes = comparison["payload"]["meta"]["probes"]
        self.assertEqual(len(probes), 3)
        self.assertTrue(all({"candidate_id", "status", "score"} <= set(p) for p in probes))

        decision = next(b for b in self.ledger_events() if b["event_type"] == "HARNESS2_COMPOSITION_DECISION")
        meta = decision["payload"]["meta"]
        self.assertIsNotNone(meta["winner"])
        self.assertEqual(meta["candidates_evaluated"], 3)

    def test_ledger_chain_integrity_preserved(self):
        child = _make_child()
        self.engine.execute_single_child(child, _make_mother(True), self.child_path, self.root)
        ok, err = self.engine.audit_chain.verify_integrity()
        self.assertTrue(ok, err)


if __name__ == "__main__":
    unittest.main()
