import os
import tempfile
import unittest
from src.ast_skeleton import ASTSkeletonizer, skeletonize_python_code, skeletonize_markdown
from src.sandbox import extract_fault_frame, SandboxRunner
from src.privacy_guard import PrivacyGuard


SAMPLE_PYTHON_CODE = '''
import os
import sys
from typing import List, Dict, Optional

class PaymentProcessor:
    """Handles multi-currency transactions and bank transfers."""

    def __init__(self, api_key: str, timeout: int = 30):
        self.api_key = api_key
        self.timeout = timeout
        self._internal_state = {}

    def process_charge(self, amount_cents: int, currency: str = "USD") -> Dict[str, str]:
        """Submits a credit card charge to the upstream gateway."""
        if amount_cents <= 0:
            raise ValueError("Amount must be positive")
        res = {"status": "success", "id": "tx_12345", "currency": currency}
        self._internal_state["last_tx"] = res
        return res

    async def refund(self, tx_id: str) -> bool:
        """Processes an asynchronous refund."""
        print(f"Refunding {tx_id}")
        return True

def calculate_fee(amount: float) -> float:
    """Calculates gateway fee."""
    return amount * 0.029 + 0.30
'''

SAMPLE_PYTEST_OUTPUT = '''
============================= test session starts =============================
platform win32 -- Python 3.11.0, pytest-7.4.0, pluggy-1.0.0
rootdir: C:\\projects\\autonomous-job-card-engine
plugins: anyio-3.7.1, mock-3.11.1
collected 5 items

tests/test_payments.py ..F..                                            [100%]

================================== FAILURES ===================================
___________________________ test_invalid_charge ___________________________

    def test_invalid_charge():
        processor = PaymentProcessor("key_123")
>       result = processor.process_charge(-100)
E       ValueError: Amount must be positive

tests/test_payments.py:42: ValueError
------------------------------ Captured log call ------------------------------
DEBUG:root:Connecting to mock payment gateway...
INFO:root:Processing charge attempt for -100 USD...
=========================== short test summary info ===========================
FAILED tests/test_payments.py::test_invalid_charge - ValueError: Amount must be positive
========================= 1 failed, 4 passed in 0.12s =========================
'''


class TestASTSkeleton(unittest.TestCase):
    def test_python_skeletonizer_preserves_signatures(self):
        skeleton = skeletonize_python_code(SAMPLE_PYTHON_CODE)
        self.assertIn("class PaymentProcessor:", skeleton)
        self.assertIn("def __init__(self, api_key: str", skeleton)
        self.assertIn("def process_charge(self, amount_cents: int", skeleton)
        self.assertIn("-> Dict[str, str]:", skeleton)
        self.assertIn("async def refund(self, tx_id: str) -> bool:", skeleton)
        self.assertIn("def calculate_fee(amount: float) -> float:", skeleton)
        # Assert implementation body was replaced with Ellipsis
        self.assertNotIn("if amount_cents <= 0:", skeleton)
        self.assertNotIn("self._internal_state['last_tx'] = res", skeleton)
        self.assertIn("...", skeleton)

    def test_ast_skeletonizer_caching_and_reduction(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            py_file = os.path.join(tmpdir, "payments.py")
            with open(py_file, "w", encoding="utf-8") as f:
                f.write(SAMPLE_PYTHON_CODE)

            skeletonizer = ASTSkeletonizer(workspace_root=tmpdir)
            res = skeletonizer.skeletonize_file("payments.py")

            self.assertTrue(res["is_skeletonized"])
            self.assertGreater(res["reduction_pct"], 30.0)
            self.assertLess(res["skeleton_chars"], res["original_chars"])

            # Context builder
            ctx = skeletonizer.format_dependencies_context(["payments.py"])
            self.assertIn("[AST SKELETON]", ctx)
            self.assertIn("PaymentProcessor", ctx)

    def test_fault_frame_extractor(self):
        fault = extract_fault_frame(SAMPLE_PYTEST_OUTPUT)
        self.assertIn("ValueError: Amount must be positive", fault)
        self.assertIn("FAILED tests/test_payments.py::test_invalid_charge", fault)
        self.assertLess(len(fault.splitlines()), len(SAMPLE_PYTEST_OUTPUT.splitlines()))

    def test_privacy_guard_path_normalization(self):
        guard = PrivacyGuard(restricted_paths=["src/auth/*", "config/secrets.json"])

        # Windows path separators
        self.assertEqual(guard.evaluate_routing_profile(["src\\auth\\tokens.py"], "cloud"), "local")
        self.assertEqual(guard.evaluate_routing_profile(["config\\secrets.json"], "cloud"), "local")

        # POSIX path separators
        self.assertEqual(guard.evaluate_routing_profile(["src/auth/tokens.py"], "cloud"), "local")
        self.assertEqual(guard.evaluate_routing_profile(["src/public/view.py"], "cloud"), "cloud")


if __name__ == "__main__":
    unittest.main()
