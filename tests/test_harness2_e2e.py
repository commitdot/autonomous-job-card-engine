"""
tests/test_harness2_e2e.py
==========================
End-to-end integration tests for the Harness² dual-level recursion:

  * Task-level recursion  - multi-depth Propose-Probe-Compose across isolated probe worktrees
  * Behavioral contrast   - a failed round's contrast feeds the next round's proposal (no blind retry)
  * Harnessing-level memory - lessons from one task are persisted, indexed in RAG, and transferred
                              to later tasks across DAG waves (and across engine restarts)
  * Fleet compatibility   - all 20 Mother Card blueprints load and run Harness² without regression

All tests are offline and deterministic (SimAdapter + filesystem-copy worktrees in a temp dir).
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

# Passes only for output produced by a Harness² probe that was not sabotaged by the test.
CHECK_PROBE_SCRIPT = (
    "import sys\n"
    "try:\n"
    "    text = open('src_out/module.py', encoding='utf-8').read()\n"
    "except OSError:\n"
    "    sys.exit(1)\n"
    "sys.exit(0 if ('Sim Probe' in text and 'FORCE_FAIL' not in text) else 1)\n"
)
ALWAYS_FAIL_SCRIPT = "import sys\nsys.exit(1)\n"
ALWAYS_PASS_SCRIPT = "print('ok')\n"


def _mother(max_depth: int = 3, enable_harness2: bool = True) -> MotherCard:
    return MotherCard(
        id="m-e2e",
        name="E2E Mother",
        core_philosophy="test",
        rag_knowledge_paths=[],
        governance=GovernancePolicies(enable_harness2=enable_harness2, max_recursion_depth=max_depth),
    )


def _child(cid="c1", objective="Implement the core module logic", deliverables=None,
           validation_commands=None, max_iterations=3, depends_on=None, squad="squad-backend") -> ChildCard:
    return ChildCard(
        id=cid,
        parent_mother_id="m-e2e",
        parent_squad_id=squad,
        name=f"Child {cid}",
        tactical_objective=objective,
        deliverables=deliverables or [{"path": "src_out/module.py", "description": "core module"}],
        validation_commands=validation_commands if validation_commands is not None else [],
        max_iterations=max_iterations,
        depends_on=depends_on or [],
    )


class _E2EBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.engine = self._new_engine()
        self.child_path = os.path.join(self.engine.jobs_dir, "child_c1.yaml")

    def tearDown(self):
        self._tmp.cleanup()

    def _new_engine(self) -> AutonomousJobCardEngine:
        engine = AutonomousJobCardEngine(workspace_root=self.root, adapter=SimAdapter(self.root))
        engine.privacy_guard = PrivacyGuard(restricted_paths=[])
        engine.sandbox = SandboxRunner()
        return engine

    def write_script(self, name: str, body: str):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as f:
            f.write(body)

    def ledger_types(self):
        path = os.path.join(self.engine.jobs_dir, "audit_ledger.jsonl")
        with open(path, "r", encoding="utf-8") as f:
            return [json.loads(line)["event_type"] for line in f if line.strip()]

    def spy_on_proposals(self, engine=None):
        """Records the (objective, context) of every propose_harness_candidates call."""
        engine = engine or self.engine
        calls = []
        original = engine.llm.propose_harness_candidates

        def spy(**kwargs):
            calls.append({"objective": kwargs.get("tactical_objective", ""),
                          "context": dict(kwargs.get("context") or {})})
            return original(**kwargs)

        engine.llm.propose_harness_candidates = spy
        return calls


class TestMultiDepthProposeProbeCompose(_E2EBase):
    def test_all_probes_failing_recurses_to_max_depth(self):
        self.write_script("fail.py", ALWAYS_FAIL_SCRIPT)
        child = _child(validation_commands=["python fail.py"], max_iterations=3)
        result = self.engine.execute_single_child(child, _mother(max_depth=3), self.child_path, self.root)

        self.assertEqual(result["phase"], "Failed")
        types = self.ledger_types()
        self.assertEqual(types.count("HARNESS2_PROPOSE"), 3)
        self.assertEqual(types.count("HARNESS2_COMPOSITION_DECISION"), 3)
        self.assertNotIn("HARNESS2_DEPTH_LIMIT", types)

        state = self.engine.recursion_state
        for iteration in (1, 2, 3):
            self.assertEqual(len(state.load_candidate_probes("c1", iteration=iteration)), 3)
            contrast = state.load_contrast_matrix("c1", iteration=iteration)
            self.assertIsNotNone(contrast)
            self.assertIsNone(contrast.winner_probe_id)
        self.assertEqual(child._recursion_state["recursion_depth"], 3)
        self.assertEqual(child._recursion_state["last_contrast_id"], "contrast-c1-3")

    def test_depth_limit_stops_recursion_and_falls_back(self):
        self.write_script("fail.py", ALWAYS_FAIL_SCRIPT)
        child = _child(validation_commands=["python fail.py"], max_iterations=3)
        self.engine.execute_single_child(child, _mother(max_depth=2), self.child_path, self.root)

        types = self.ledger_types()
        self.assertEqual(types.count("HARNESS2_PROPOSE"), 2)
        self.assertEqual(types.count("HARNESS2_DEPTH_LIMIT"), 1)
        state = self.engine.recursion_state
        self.assertEqual(len(state.load_candidate_probes("c1", iteration=3)), 0)
        self.assertIsNone(state.load_contrast_matrix("c1", iteration=3))
        self.assertEqual(child._recursion_state["recursion_depth"], 2)

    def test_audit_chain_integrity_across_depths(self):
        self.write_script("fail.py", ALWAYS_FAIL_SCRIPT)
        child = _child(validation_commands=["python fail.py"], max_iterations=3)
        self.engine.execute_single_child(child, _mother(max_depth=3), self.child_path, self.root)
        ok, err = self.engine.audit_chain.verify_integrity()
        self.assertTrue(ok, err)


class TestBehavioralContrastReflection(_E2EBase):
    def _force_first_round_failure(self):
        """Sabotage every iteration-1 probe so the round has no passing candidate."""
        original = self.engine.llm.probe_execution

        def forced(candidate, tactical_objective, deliverables, iteration=1, execution_root=None):
            cand = original(candidate=candidate, tactical_objective=tactical_objective,
                            deliverables=deliverables, iteration=iteration, execution_root=execution_root)
            if iteration == 1:
                for d in deliverables:
                    with open(os.path.join(execution_root, d["path"]), "a", encoding="utf-8") as f:
                        f.write("\n# FORCE_FAIL\n")
            return cand

        self.engine.llm.probe_execution = forced

    def test_failed_round_contrast_informs_next_round_and_heals(self):
        self.write_script("check.py", CHECK_PROBE_SCRIPT)
        self._force_first_round_failure()
        proposals = self.spy_on_proposals()

        child = _child(validation_commands=["python check.py"], max_iterations=3)
        result = self.engine.execute_single_child(child, _mother(max_depth=3), self.child_path, self.root)

        self.assertEqual(result["phase"], "Completed")
        self.assertEqual(result["iterations"], 2)

        # Round 1: nothing passed; round 2: composition found a winner
        state = self.engine.recursion_state
        round1 = state.load_candidate_probes("c1", iteration=1)
        round2 = state.load_candidate_probes("c1", iteration=2)
        self.assertTrue(round1 and all(p.status == "failed" for p in round1))
        self.assertIn("success", {p.status for p in round2})
        self.assertIsNone(state.load_contrast_matrix("c1", iteration=1).winner_probe_id)
        self.assertIsNotNone(state.load_contrast_matrix("c1", iteration=2).winner_probe_id)

        # Round 2's proposal was conditioned on round 1's behavioral contrast (not a blind retry)
        self.assertEqual(len(proposals), 2)
        self.assertEqual(proposals[0]["context"]["previous_errors"], "")
        second_errors = proposals[1]["context"]["previous_errors"]
        self.assertIn("[BEHAVIORAL CONTRAST]", second_errors)
        for probe in round1:
            self.assertIn(probe.probe_id, second_errors)
        self.assertEqual([p["context"]["recursion_depth"] for p in proposals], [0, 1])

        # The composed (winning) deliverable was committed and is probe-derived
        with open(os.path.join(self.root, "src_out", "module.py"), encoding="utf-8") as f:
            committed = f.read()
        self.assertIn("Sim Probe", committed)
        self.assertNotIn("FORCE_FAIL", committed)
        self.assertTrue(any("[BEHAVIORAL CONTRAST]" in line for line in child.logs))

    def test_behavioral_contrast_distinguishes_candidates_with_real_validation(self):
        # Candidate 0 (no guardrails) must fail and candidates 1-2 must pass, judged by the real test command
        self.write_script("check_guardrails.py",
                          "import sys\n"
                          "text = open('src_out/module.py', encoding='utf-8').read()\n"
                          "sys.exit(1 if 'guardrails:    0 rule(s)' in text else 0)\n")
        child = _child(validation_commands=["python check_guardrails.py"], max_iterations=1)
        result = self.engine.execute_single_child(child, _mother(), self.child_path, self.root)
        self.assertEqual(result["phase"], "Completed")

        probes = self.engine.recursion_state.load_candidate_probes("c1", iteration=1)
        by_status = sorted(p.status for p in probes)
        self.assertEqual(by_status, ["failed", "success", "success"])

        contrast = self.engine.recursion_state.load_contrast_matrix("c1", iteration=1)
        self.assertEqual(len(contrast.differences), 2)
        self.assertTrue(contrast.fault_frames)
        failed_id = next(p.probe_id for p in probes if p.status == "failed")
        self.assertNotEqual(contrast.winner_probe_id, failed_id)
        self.assertTrue(any(d["differing_components"] for d in contrast.differences))


class TestCrossTaskMemoryTransfer(_E2EBase):
    OBJ_A = "Configure asyncpg connection pool with max_queries and timeout"
    OBJ_B = "Add retry handling to the asyncpg connection pool for transient timeout errors"

    def _write_cycle_workspace(self):
        self.write_script("check_ok.py", ALWAYS_PASS_SCRIPT)
        jobs = self.engine.jobs_dir
        os.makedirs(jobs, exist_ok=True)
        mother = _mother()
        mother_path = os.path.join(jobs, "mother_card.yaml")
        mother.to_yaml(mother_path)

        card_a = _child("child-a", self.OBJ_A,
                        [{"path": "src_out/pool.py", "description": "pool"}],
                        ["python check_ok.py"], squad="squad-database")
        card_b = _child("child-b", self.OBJ_B,
                        [{"path": "src_out/retry.py", "description": "retry"}],
                        ["python check_ok.py"], depends_on=["child-a"], squad="squad-database")
        card_a.to_yaml(os.path.join(jobs, "child_a.yaml"))
        card_b.to_yaml(os.path.join(jobs, "child_b.yaml"))
        return mother_path

    def test_lessons_flow_from_wave_one_to_wave_two(self):
        mother_path = self._write_cycle_workspace()
        proposals = self.spy_on_proposals()

        self.engine.run_one_cycle(mother_path)

        # Both DAG waves ran with Harness² active
        self.assertEqual(len(proposals), 2)
        ctx_a, ctx_b = proposals[0]["context"], proposals[1]["context"]
        self.assertIn("asyncpg", proposals[0]["objective"])
        self.assertIn("retry", proposals[1]["objective"])

        # Wave 1 starts with empty institutional memory; wave 2 receives wave 1's lessons
        self.assertEqual(ctx_a["lessons"], [])
        self.assertTrue(ctx_b["lessons"])
        self.assertTrue(any("Child child-a" in lesson for lesson in ctx_b["lessons"]))

        # ...and the transferred lessons shaped wave 2's harness candidates (guardrails/memory components)
        probes_b = self.engine.recursion_state.load_candidate_probes("child-b")
        self.assertEqual(len(probes_b), 3)
        guardrails = [g for p in probes_b for g in p.harness_config.get("guardrails", [])]
        self.assertTrue(any(g.startswith("Apply lesson:") for g in guardrails))

        # Both cards completed, and each child completion extracted lessons
        for cid in ("child-a", "child-b"):
            with open(os.path.join(self.engine.jobs_dir, f"child_{cid[-1]}.yaml"), encoding="utf-8") as f:
                self.assertIn("Completed", f.read())
        sources = {l.source_card_id for l in self.engine.recursion_state.load_lessons()}
        self.assertEqual(sources, {"child-a", "child-b"})

    def test_lessons_indexed_in_rag_and_survive_engine_restart(self):
        mother_path = self._write_cycle_workspace()
        self.engine.run_one_cycle(mother_path)

        lessons_file = os.path.join(self.root, ".jobs", "recursion", "lessons_learned.json")
        self.assertTrue(os.path.exists(lessons_file))

        # Retrievable through the local RAG index
        results = self.engine.rag_retriever.query("asyncpg connection pool", top_k=2)
        self.assertTrue(results)

        # A brand new engine instance (fresh process equivalent) reads the same institutional memory
        fresh = self._new_engine()
        relevant = fresh.recursion_state.get_relevant_lessons("asyncpg connection pool timeout")
        self.assertTrue(relevant)
        self.assertIn(relevant[0].source_card_id, {"child-a", "child-b"})

        # ...and injects it into the next task's proposal
        proposals = self.spy_on_proposals(fresh)
        card_c = _child("child-c", "Tune asyncpg connection pool timeout under load",
                        [{"path": "src_out/tune.py", "description": "tune"}], [], max_iterations=1)
        fresh.execute_single_child(card_c, _mother(), os.path.join(fresh.jobs_dir, "child_c.yaml"), self.root)
        self.assertEqual(len(proposals), 1)
        self.assertTrue(proposals[0]["context"]["lessons"])

    def test_disabled_harness2_accumulates_no_recursion_state(self):
        self.write_script("check_ok.py", ALWAYS_PASS_SCRIPT)
        proposals = self.spy_on_proposals()
        child = _child("c1", self.OBJ_A, validation_commands=["python check_ok.py"], max_iterations=1)
        result = self.engine.execute_single_child(child, _mother(enable_harness2=False), self.child_path, self.root)

        self.assertEqual(result["phase"], "Completed")
        self.assertEqual(proposals, [])
        self.assertEqual(self.engine.recursion_state.list_probes("c1"), [])
        self.assertFalse([t for t in self.ledger_types() if t.startswith("HARNESS2")])


class TestMotherCardFleetCompatibility(_E2EBase):
    CARDS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "mother-cards")

    def _card_paths(self):
        return sorted(
            os.path.join(self.CARDS_DIR, n) for n in os.listdir(self.CARDS_DIR) if n.endswith(".yaml")
        )

    def test_fleet_has_twenty_mother_cards(self):
        self.assertEqual(len(self._card_paths()), 20)

    def test_every_mother_card_loads_with_backward_compatible_harness2_defaults(self):
        for path in self._card_paths():
            with self.subTest(card=os.path.basename(path)):
                mother = MotherCard.from_yaml(path)
                self.assertTrue(mother.id)
                self.assertFalse(mother.governance.enable_harness2)  # opt-in only
                self.assertFalse(self.engine._is_harness2_active(mother))
                self.assertGreaterEqual(mother.governance.max_recursion_depth, 1)

    def test_every_mother_card_runs_harness2_and_legacy_paths(self):
        self.write_script("check_ok.py", ALWAYS_PASS_SCRIPT)
        for idx, path in enumerate(self._card_paths()):
            mother = MotherCard.from_yaml(path)
            for enabled in (True, False):
                with self.subTest(card=os.path.basename(path), harness2=enabled):
                    mother.governance.enable_harness2 = enabled
                    cid = f"fleet-{idx}-{int(enabled)}"
                    child = _child(cid, "Implement the core module logic",
                                   [{"path": f"src_out/{cid}.py", "description": "module"}],
                                   ["python check_ok.py"], max_iterations=1)
                    child_path = os.path.join(self.engine.jobs_dir, f"child_{cid}.yaml")
                    result = self.engine.execute_single_child(child, mother, child_path, self.root)
                    self.assertEqual(result["phase"], "Completed")
                    probes = self.engine.recursion_state.list_probes(cid)
                    self.assertEqual(len(probes) == 3, enabled)




if __name__ == "__main__":
    unittest.main()
