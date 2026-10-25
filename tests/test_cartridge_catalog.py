"""
tests/test_cartridge_catalog.py
================================
Validates every cartridge manifest in the community starter catalog:
  - registry/cartridges/tech/   (4 technical starter cartridges)
  - registry/cartridges/industry/  (7 pre-populated industry cartridges)

Each test asserts:
  1. The YAML file can be loaded with AdapterCartridge.from_yaml().
  2. The manifest passes validate() without raising CartridgeValidationError.
  3. Required metadata fields (name, version, author, description, domain) are non-empty.
  4. spec.harness_patch is non-empty and contains only valid HARNESS_PATCH_KEYS.
  5. spec.benchmarks contains at least one entry and all values are numeric.
  6. A to_dict() → from_dict() round-trip produces an equal object.
"""

import os
import unittest

from src.models import (
    AdapterCartridge,
    CartridgeValidationError,
    HARNESS_PATCH_KEYS,
)

# ---------------------------------------------------------------------------
# Catalog definition: (category, cartridge-slug)
# ---------------------------------------------------------------------------

TECH_CARTRIDGES = [
    "security-shield",
    "pytest-self-healing",
    "cuda-kernel-opt",
    "fastapi-backend",
]

INDUSTRY_CARTRIDGES = [
    "fintech-banking",
    "healthcare-hipaa",
    "cybersecurity-defense",
    "automotive-embedded",
    "legal-compliance",
    "ecommerce-retail",
    "devops-cloud-infra",
]

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cartridge_path(category: str, slug: str) -> str:
    return os.path.join(REPO_ROOT, "registry", "cartridges", category, slug, "cartridge.yaml")


def _load(category: str, slug: str) -> AdapterCartridge:
    path = _cartridge_path(category, slug)
    return AdapterCartridge.from_yaml(path)


# ---------------------------------------------------------------------------
# Shared assertion helpers
# ---------------------------------------------------------------------------

class CartridgeCatalogBase(unittest.TestCase):
    """Mixin providing shared assertion methods for catalog tests."""

    def _assert_metadata(self, cart: AdapterCartridge, slug: str) -> None:
        md = cart.metadata
        self.assertEqual(md.name, slug, f"metadata.name mismatch for {slug}")
        self.assertRegex(md.version, r"^\d+\.\d+\.\d+", f"version not semver for {slug}")
        self.assertTrue(md.author, f"metadata.author is empty for {slug}")
        self.assertTrue(md.description, f"metadata.description is empty for {slug}")
        self.assertTrue(md.domain, f"metadata.domain is empty for {slug}")
        self.assertTrue(md.tags, f"metadata.tags is empty for {slug}")

    def _assert_spec(self, cart: AdapterCartridge, slug: str) -> None:
        sp = cart.spec
        self.assertIn(sp.type, ("harness-patch", "lora", "hybrid"), f"invalid type for {slug}")
        # All catalog cartridges are harness-patch only — no LoRA weights required
        self.assertEqual(sp.type, "harness-patch", f"{slug} should be harness-patch type")
        self.assertTrue(sp.harness_patch, f"harness_patch is empty for {slug}")
        unknown_keys = set(sp.harness_patch) - set(HARNESS_PATCH_KEYS)
        self.assertFalse(unknown_keys, f"Unknown harness_patch keys in {slug}: {unknown_keys}")

    def _assert_benchmarks(self, cart: AdapterCartridge, slug: str) -> None:
        benchmarks = cart.spec.benchmarks
        self.assertTrue(benchmarks, f"benchmarks dict is empty for {slug}")
        for key, value in benchmarks.items():
            self.assertIsInstance(
                value, (int, float),
                f"Benchmark '{key}' in {slug} is not numeric: {value!r}",
            )
            self.assertFalse(
                isinstance(value, bool),
                f"Benchmark '{key}' in {slug} must not be bool",
            )
            self.assertGreaterEqual(value, 0.0, f"Benchmark '{key}' in {slug} is negative")

    def _assert_roundtrip(self, cart: AdapterCartridge, slug: str) -> None:
        reloaded = AdapterCartridge.from_dict(cart.to_dict())
        self.assertEqual(cart, reloaded, f"Round-trip failed for {slug}")

    def _assert_validates(self, cart: AdapterCartridge, slug: str) -> None:
        try:
            cart.validate()
        except CartridgeValidationError as exc:
            self.fail(f"validate() raised for {slug}: {exc}")

    def _full_assert(self, category: str, slug: str) -> None:
        path = _cartridge_path(category, slug)
        self.assertTrue(
            os.path.isfile(path),
            f"Cartridge file not found: {path}",
        )
        cart = _load(category, slug)
        self._assert_validates(cart, slug)
        self._assert_metadata(cart, slug)
        self._assert_spec(cart, slug)
        self._assert_benchmarks(cart, slug)
        self._assert_roundtrip(cart, slug)


# ---------------------------------------------------------------------------
# Technical starter cartridges
# ---------------------------------------------------------------------------

class TestTechCartridges(CartridgeCatalogBase):

    def test_security_shield_loads(self):
        self._full_assert("tech", "security-shield")

    def test_security_shield_guardrails_present(self):
        cart = _load("tech", "security-shield")
        guardrails = cart.spec.harness_patch.get("guardrails", [])
        self.assertTrue(len(guardrails) >= 3, "security-shield should have at least 3 guardrails")
        combined = " ".join(guardrails)
        self.assertIn("Argon2id", combined, "security-shield guardrails should mention Argon2id")

    def test_pytest_self_healing_loads(self):
        self._full_assert("tech", "pytest-self-healing")

    def test_pytest_self_healing_scripts_present(self):
        cart = _load("tech", "pytest-self-healing")
        scripts = cart.spec.harness_patch.get("scripts", [])
        self.assertTrue(scripts, "pytest-self-healing should define scripts")

    def test_cuda_kernel_opt_loads(self):
        self._full_assert("tech", "cuda-kernel-opt")

    def test_cuda_kernel_opt_guardrails_block_dynamic_alloc(self):
        cart = _load("tech", "cuda-kernel-opt")
        guardrails = cart.spec.harness_patch.get("guardrails", [])
        combined = " ".join(guardrails)
        self.assertIn("dynamic", combined.lower(), "cuda-kernel-opt should block dynamic memory allocation")

    def test_fastapi_backend_loads(self):
        self._full_assert("tech", "fastapi-backend")

    def test_fastapi_backend_mentions_pydantic(self):
        cart = _load("tech", "fastapi-backend")
        combined = " ".join([
            cart.spec.harness_patch.get("system_prompt", ""),
            *cart.spec.harness_patch.get("guardrails", []),
        ])
        self.assertIn("Pydantic", combined, "fastapi-backend should reference Pydantic")


# ---------------------------------------------------------------------------
# Industry cartridges
# ---------------------------------------------------------------------------

class TestIndustryCartridges(CartridgeCatalogBase):

    def test_fintech_banking_loads(self):
        self._full_assert("industry", "fintech-banking")

    def test_fintech_banking_blocks_floats(self):
        cart = _load("industry", "fintech-banking")
        guardrails = cart.spec.harness_patch.get("guardrails", [])
        combined = " ".join(guardrails)
        self.assertIn("float", combined.lower(), "fintech-banking must block float arithmetic")

    def test_healthcare_hipaa_loads(self):
        self._full_assert("industry", "healthcare-hipaa")

    def test_healthcare_hipaa_mentions_fhir(self):
        cart = _load("industry", "healthcare-hipaa")
        combined = " ".join([
            cart.spec.harness_patch.get("system_prompt", ""),
            *cart.spec.harness_patch.get("tool_guidance", []),
        ])
        self.assertIn("FHIR", combined, "healthcare-hipaa should reference FHIR R4")

    def test_cybersecurity_defense_loads(self):
        self._full_assert("industry", "cybersecurity-defense")

    def test_cybersecurity_defense_blocks_injection(self):
        cart = _load("industry", "cybersecurity-defense")
        guardrails = cart.spec.harness_patch.get("guardrails", [])
        combined = " ".join(guardrails)
        self.assertIn("injection", combined.lower(), "cybersecurity-defense must block injection")

    def test_automotive_embedded_loads(self):
        self._full_assert("industry", "automotive-embedded")

    def test_automotive_embedded_blocks_dynamic_alloc(self):
        cart = _load("industry", "automotive-embedded")
        guardrails = cart.spec.harness_patch.get("guardrails", [])
        combined = " ".join(guardrails)
        self.assertIn("malloc", combined, "automotive-embedded must block malloc")

    def test_legal_compliance_loads(self):
        self._full_assert("industry", "legal-compliance")

    def test_legal_compliance_gdpr_erasure(self):
        cart = _load("industry", "legal-compliance")
        combined = " ".join([
            cart.spec.harness_patch.get("system_prompt", ""),
            *cart.spec.harness_patch.get("guardrails", []),
        ])
        self.assertIn("GDPR", combined, "legal-compliance must reference GDPR")

    def test_ecommerce_retail_loads(self):
        self._full_assert("industry", "ecommerce-retail")

    def test_ecommerce_retail_idempotency(self):
        cart = _load("industry", "ecommerce-retail")
        guardrails = cart.spec.harness_patch.get("guardrails", [])
        combined = " ".join(guardrails)
        self.assertIn("idempotency", combined.lower(), "ecommerce-retail must enforce idempotency")

    def test_devops_cloud_infra_loads(self):
        self._full_assert("industry", "devops-cloud-infra")

    def test_devops_cloud_infra_blocks_wildcard_iam(self):
        cart = _load("industry", "devops-cloud-infra")
        guardrails = cart.spec.harness_patch.get("guardrails", [])
        combined = " ".join(guardrails)
        self.assertIn("*", combined, "devops-cloud-infra must block wildcard IAM")


# ---------------------------------------------------------------------------
# Cross-catalog aggregate tests
# ---------------------------------------------------------------------------

class TestCatalogCoverage(CartridgeCatalogBase):
    """Ensures the complete 11-cartridge catalog is discoverable and valid."""

    def test_all_11_cartridges_present_on_disk(self):
        missing = []
        for slug in TECH_CARTRIDGES:
            if not os.path.isfile(_cartridge_path("tech", slug)):
                missing.append(f"tech/{slug}")
        for slug in INDUSTRY_CARTRIDGES:
            if not os.path.isfile(_cartridge_path("industry", slug)):
                missing.append(f"industry/{slug}")
        self.assertFalse(missing, f"Missing cartridge files: {missing}")

    def test_all_11_cartridges_load_cleanly(self):
        errors = []
        for slug in TECH_CARTRIDGES:
            try:
                _load("tech", slug)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"tech/{slug}: {exc}")
        for slug in INDUSTRY_CARTRIDGES:
            try:
                _load("industry", slug)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"industry/{slug}: {exc}")
        self.assertFalse(errors, f"Cartridges failed to load:\n" + "\n".join(errors))

    def test_all_11_cartridges_pass_validation(self):
        errors = []
        for slug in TECH_CARTRIDGES:
            try:
                _load("tech", slug).validate()
            except CartridgeValidationError as exc:
                errors.append(f"tech/{slug}: {exc}")
        for slug in INDUSTRY_CARTRIDGES:
            try:
                _load("industry", slug).validate()
            except CartridgeValidationError as exc:
                errors.append(f"industry/{slug}: {exc}")
        self.assertFalse(errors, f"Cartridge validation errors:\n" + "\n".join(errors))

    def test_all_11_cartridges_have_benchmarks(self):
        errors = []
        for slug in TECH_CARTRIDGES:
            cart = _load("tech", slug)
            if not cart.spec.benchmarks:
                errors.append(f"tech/{slug} has no benchmarks")
        for slug in INDUSTRY_CARTRIDGES:
            cart = _load("industry", slug)
            if not cart.spec.benchmarks:
                errors.append(f"industry/{slug} has no benchmarks")
        self.assertFalse(errors, "\n".join(errors))

    def test_all_11_cartridges_have_pass_rate_benchmark(self):
        errors = []
        for slug in TECH_CARTRIDGES:
            cart = _load("tech", slug)
            if "pass_rate" not in cart.spec.benchmarks:
                errors.append(f"tech/{slug} missing pass_rate benchmark")
            else:
                v = cart.spec.benchmarks["pass_rate"]
                if not (0.0 <= v <= 1.0):
                    errors.append(f"tech/{slug} pass_rate {v} out of [0, 1]")
        for slug in INDUSTRY_CARTRIDGES:
            cart = _load("industry", slug)
            if "pass_rate" not in cart.spec.benchmarks:
                errors.append(f"industry/{slug} missing pass_rate benchmark")
            else:
                v = cart.spec.benchmarks["pass_rate"]
                if not (0.0 <= v <= 1.0):
                    errors.append(f"industry/{slug} pass_rate {v} out of [0, 1]")
        self.assertFalse(errors, "\n".join(errors))

    def test_all_11_cartridges_have_non_empty_harness_patch(self):
        errors = []
        for slug in TECH_CARTRIDGES:
            cart = _load("tech", slug)
            if not cart.spec.harness_patch:
                errors.append(f"tech/{slug} has empty harness_patch")
        for slug in INDUSTRY_CARTRIDGES:
            cart = _load("industry", slug)
            if not cart.spec.harness_patch:
                errors.append(f"industry/{slug} has empty harness_patch")
        self.assertFalse(errors, "\n".join(errors))

    def test_all_11_cartridges_roundtrip(self):
        errors = []
        for slug in TECH_CARTRIDGES:
            try:
                cart = _load("tech", slug)
                reloaded = AdapterCartridge.from_dict(cart.to_dict())
                if cart != reloaded:
                    errors.append(f"tech/{slug}: round-trip produced a different object")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"tech/{slug}: {exc}")
        for slug in INDUSTRY_CARTRIDGES:
            try:
                cart = _load("industry", slug)
                reloaded = AdapterCartridge.from_dict(cart.to_dict())
                if cart != reloaded:
                    errors.append(f"industry/{slug}: round-trip produced a different object")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"industry/{slug}: {exc}")
        self.assertFalse(errors, "\n".join(errors))


if __name__ == "__main__":
    unittest.main()
