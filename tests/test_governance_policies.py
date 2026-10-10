import os
import tempfile
import unittest
from src.models import MotherCard, GovernancePolicies, ChildCard
from src.daemon import AutonomousJobCardEngine


class TestGovernancePolicies(unittest.TestCase):
    def test_mother_governance_serialization(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gov = GovernancePolicies(
                enable_repo_regex_scan=True,
                forbidden_patterns=[r"(?i)v2\.0", r"(?i)SECRET_LEAK"],
                enable_ast_skeletonization=True,
                context_budget_chars=12000,
                enable_alignment_cascades=True,
                max_wave_depth=2,
                max_concurrency=1,
                vram_threshold_pct=75.0,
                dual_pass_flakiness_check=True,
                composite_integration_commands=["python -m unittest"],
            )

            mother = MotherCard(
                id="mother-test-gov",
                name="Governance Guardian",
                core_philosophy="Zero architectural drift.",
                governance=gov,
            )

            yaml_path = os.path.join(tmpdir, "mother_card.yaml")
            mother.to_yaml(yaml_path)

            loaded = MotherCard.from_yaml(yaml_path)
            self.assertTrue(loaded.governance.enable_repo_regex_scan)
            self.assertEqual(loaded.governance.max_wave_depth, 2)
            self.assertEqual(loaded.governance.max_concurrency, 1)
            self.assertEqual(loaded.governance.vram_threshold_pct, 75.0)
            self.assertIn(r"(?i)SECRET_LEAK", loaded.governance.forbidden_patterns)
            self.assertEqual(loaded.governance.composite_integration_commands, ["python -m unittest"])

    def test_preflight_quality_scan_detects_forbidden_patterns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a dirty file containing a forbidden pattern
            bad_file = os.path.join(tmpdir, "legacy_module.py")
            with open(bad_file, "w", encoding="utf-8") as f:
                f.write("# Legacy code running on v2.0 engine\n# TODO: urgent fix this\n")

            gov = GovernancePolicies(
                enable_repo_regex_scan=True,
                forbidden_patterns=[r"(?i)v2\.0", r"(?i)TODO:\s*urgent"],
            )
            mother = MotherCard(id="m-test", name="M", core_philosophy="", governance=gov)

            engine = AutonomousJobCardEngine(workspace_root=tmpdir)
            scan_res = engine.run_preflight_quality_scan(mother)

            self.assertGreater(scan_res["scanned_files"], 0)
            self.assertEqual(len(scan_res["violations"]), 2)
            matched_patterns = [v["pattern"] for v in scan_res["violations"]]
            self.assertIn(r"(?i)v2\.0", matched_patterns)
            self.assertIn(r"(?i)TODO:\s*urgent", matched_patterns)


if __name__ == "__main__":
    unittest.main()
