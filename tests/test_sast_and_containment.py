"""Unit tests for AST SAST Scanner, Worktree Jailer, and Tamper-Evident Audit Chain."""

import unittest
import tempfile
import os
import shutil
from pathlib import Path

from src.security.sast_scanner import ASTSecurityScanner, SASTRiskLevel
from src.security.containment import WorktreeJailer, PathContainmentError
from src.security.audit_chain import AuditChain, AuditBlock


class TestSASTAndContainment(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.sast = ASTSecurityScanner()
        self.jailer = WorktreeJailer(jail_root=self.test_dir)
        self.chain = AuditChain(workspace_root=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_sast_detects_eval_and_exec(self):
        code = "def run_code(user_input):\n    eval(user_input)\n"
        res = self.sast.scan_source(code)
        self.assertFalse(res.passed)
        self.assertEqual(res.highest_risk, SASTRiskLevel.CRITICAL)
        self.assertTrue(any(f.rule_id == "AJE-SAST-001" for f in res.findings))

    def test_sast_detects_subprocess_shell_true(self):
        code = "import subprocess\ndef execute(cmd):\n    subprocess.Popen(cmd, shell=True)\n"
        res = self.sast.scan_source(code)
        self.assertFalse(res.passed)
        self.assertEqual(res.highest_risk, SASTRiskLevel.HIGH)
        self.assertTrue(any(f.rule_id == "AJE-SAST-003" for f in res.findings))

    def test_sast_detects_hardcoded_secrets(self):
        code = "AWS_SECRET = 'AKIAIOSFODNN7EXAMPLE'\napi_key = 'sk-live-12345678901234567890'\n"
        res = self.sast.scan_source(code)
        self.assertFalse(res.passed)
        self.assertTrue(any(f.rule_id == "AJE-SAST-007" for f in res.findings))

    def test_supply_chain_scanner_catches_deprecated_packages(self):
        code = "import pycrypto\nfrom Crypto.Cipher import AES\n"
        res = self.sast.scan_source(code)
        self.assertFalse(res.passed)
        self.assertTrue(any(f.rule_id == "AJE-SUPPLY-001" for f in res.findings))

    def test_worktree_jailer_allows_contained_paths(self):
        inside_path = "src/modules/auth.py"
        resolved = self.jailer.validate_path(inside_path)
        self.assertTrue(self.jailer.is_contained(inside_path))
        self.assertEqual(resolved, (Path(self.test_dir) / inside_path).resolve())

    def test_worktree_jailer_blocks_path_traversal(self):
        outside_path = "../../../etc/passwd"
        self.assertFalse(self.jailer.is_contained(outside_path))
        with self.assertRaises(PathContainmentError):
            self.jailer.validate_path(outside_path)

    def test_audit_chain_append_and_verify(self):
        self.chain.append_event("TEST_EVENT_1", "TestRunner", {"status": "ok"})
        self.chain.append_event("TEST_EVENT_2", "TestRunner", {"count": 42})

        is_valid, err = self.chain.verify_integrity()
        self.assertTrue(is_valid)
        self.assertIsNone(err)

        blocks = self.chain.get_all_blocks()
        self.assertEqual(len(blocks), 3)  # Genesis + 2 events
        self.assertEqual(blocks[1].event_type, "TEST_EVENT_1")
        self.assertEqual(blocks[2].event_type, "TEST_EVENT_2")

    def test_audit_chain_tamper_detection(self):
        self.chain.append_event("SECURE_EVENT", "Auditor", {"action": "approved"})

        # Tamper with the ledger file directly
        ledger_file = Path(self.test_dir) / ".jobs" / "audit_ledger.jsonl"
        lines = ledger_file.read_text(encoding="utf-8").splitlines()
        
        # Modify the payload in the last line
        import json
        last_block = json.loads(lines[-1])
        last_block["payload"]["action"] = "tampered_rejected"
        lines[-1] = json.dumps(last_block)
        ledger_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Verify should now catch the tampering
        is_valid, err = self.chain.verify_integrity()
        self.assertFalse(is_valid)
        self.assertIn("Tampered block detected", str(err))


if __name__ == "__main__":
    unittest.main()
