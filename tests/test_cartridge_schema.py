import os
import tempfile
import unittest

import yaml

from src.adapters.harness_mixin import HarnessConfig
from src.models import (
    AdapterBinding,
    AdapterCartridge,
    CartridgeMetadata,
    CartridgeSpec,
    CartridgeValidationError,
    MotherCard,
)

SHA = "a" * 64


def make_patch_cartridge(**md_overrides) -> AdapterCartridge:
    md = dict(name="security-shield", version="1.2.0", author="aje", domain="security", tags=["owasp"])
    md.update(md_overrides)
    return AdapterCartridge(
        metadata=CartridgeMetadata(**md),
        spec=CartridgeSpec(
            type="harness-patch",
            base_models=["granite-3"],
            harness_patch={
                "system_prompt": "Be secure.",
                "guardrails": ["Use Argon2id"],
                "tool_guidance": ["Run bandit"],
            },
            benchmarks={"pass_rate": 0.92},
        ),
    )


def make_lora_cartridge() -> AdapterCartridge:
    return AdapterCartridge(
        metadata=CartridgeMetadata(name="cuda-kernel-opt"),
        spec=CartridgeSpec(
            type="hybrid",
            lora_repo_id="org/cuda-lora",
            lora_filename="adapter.safetensors",
            lora_sha256=SHA,
            lora_rank=16,
            harness_patch={"guardrails": ["Use CUTLASS"]},
        ),
    )


class TestCartridgeRoundtrip(unittest.TestCase):
    def test_patch_cartridge_roundtrip(self):
        cart = make_patch_cartridge()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "cartridge.yaml")
            cart.to_yaml(path)
            loaded = AdapterCartridge.from_yaml(path)
        self.assertEqual(cart, loaded)

    def test_hybrid_cartridge_roundtrip(self):
        cart = make_lora_cartridge()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cartridge.yaml")
            cart.to_yaml(path)
            loaded = AdapterCartridge.from_yaml(path)
        self.assertEqual(cart, loaded)
        self.assertEqual(loaded.spec.lora_rank, 16)

    def test_yaml_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cartridge.yaml")
            make_patch_cartridge().to_yaml(path)
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        self.assertEqual(data["apiVersion"], "agent.autonomous.io/v1alpha1")
        self.assertEqual(data["kind"], "AdapterCartridge")
        self.assertEqual(data["metadata"]["name"], "security-shield")

    def test_parse_minimal_manifest_applies_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cartridge.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    "kind: AdapterCartridge\n"
                    "metadata:\n  name: tiny\n"
                    "spec:\n  harness_patch:\n    guardrails: [no eval]\n"
                )
            cart = AdapterCartridge.from_yaml(path)
        self.assertEqual(cart.metadata.version, "0.1.0")
        self.assertEqual(cart.spec.type, "harness-patch")
        self.assertEqual(cart.spec.benchmarks, {})

    def test_numeric_version_is_coerced_to_string(self):
        cart = AdapterCartridge.from_dict({
            "metadata": {"name": "x1", "version": "1.0.0"},
            "spec": {"harness_patch": {"guardrails": ["a"]}},
        })
        self.assertIsInstance(cart.metadata.version, str)

    def test_harness_patch_compatible_with_harness_config(self):
        cart = make_patch_cartridge()
        cfg = HarnessConfig.from_dict(cart.spec.harness_patch)
        self.assertEqual(cfg.guardrails, ["Use Argon2id"])
        self.assertIn("Use Argon2id", cfg.render_prompt_block())


class TestCartridgeValidation(unittest.TestCase):
    def _base(self):
        return {
            "kind": "AdapterCartridge",
            "metadata": {"name": "valid-name", "version": "1.0.0"},
            "spec": {"type": "harness-patch", "harness_patch": {"guardrails": ["x"]}},
        }

    def test_valid_base(self):
        AdapterCartridge.from_dict(self._base())

    def test_non_mapping_rejected(self):
        with self.assertRaises(CartridgeValidationError):
            AdapterCartridge.from_dict(["not", "a", "dict"])

    def test_wrong_kind_rejected(self):
        data = self._base()
        data["kind"] = "MotherCard"
        with self.assertRaises(CartridgeValidationError):
            AdapterCartridge.from_dict(data)

    def test_missing_name_rejected(self):
        data = self._base()
        data["metadata"] = {}
        with self.assertRaises(CartridgeValidationError):
            AdapterCartridge.from_dict(data)

    def test_invalid_name_rejected(self):
        for bad in ("Bad Name", "UPPER", "-lead", "trail-", "a/b"):
            data = self._base()
            data["metadata"]["name"] = bad
            with self.subTest(name=bad), self.assertRaises(CartridgeValidationError):
                AdapterCartridge.from_dict(data)

    def test_invalid_version_rejected(self):
        data = self._base()
        data["metadata"]["version"] = "v1"
        with self.assertRaises(CartridgeValidationError):
            AdapterCartridge.from_dict(data)

    def test_invalid_type_rejected(self):
        data = self._base()
        data["spec"]["type"] = "magic"
        with self.assertRaises(CartridgeValidationError):
            AdapterCartridge.from_dict(data)

    def test_lora_requires_repo_and_filename(self):
        data = self._base()
        data["spec"] = {"type": "lora", "lora_repo_id": "org/x"}
        with self.assertRaises(CartridgeValidationError):
            AdapterCartridge.from_dict(data)

    def test_patch_requires_non_empty_harness_patch(self):
        data = self._base()
        data["spec"] = {"type": "harness-patch"}
        with self.assertRaises(CartridgeValidationError):
            AdapterCartridge.from_dict(data)

    def test_bad_sha256_rejected(self):
        data = self._base()
        data["spec"]["lora_sha256"] = "xyz"
        with self.assertRaises(CartridgeValidationError):
            AdapterCartridge.from_dict(data)

    def test_unknown_harness_patch_key_rejected(self):
        data = self._base()
        data["spec"]["harness_patch"]["bogus"] = 1
        with self.assertRaises(CartridgeValidationError):
            AdapterCartridge.from_dict(data)

    def test_non_numeric_benchmark_rejected(self):
        data = self._base()
        data["spec"]["benchmarks"] = {"pass_rate": "high"}
        with self.assertRaises(CartridgeValidationError):
            AdapterCartridge.from_dict(data)

    def test_to_yaml_validates_before_writing(self):
        cart = make_patch_cartridge(name="Bad Name")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cartridge.yaml")
            with self.assertRaises(CartridgeValidationError):
                cart.to_yaml(path)
            self.assertFalse(os.path.exists(path))


class TestAdapterBinding(unittest.TestCase):
    def test_from_string_shorthand(self):
        b = AdapterBinding.from_dict("security-shield")
        self.assertEqual(b.cartridge, "security-shield")
        self.assertEqual(b.version, "latest")
        self.assertTrue(b.enabled)
        self.assertEqual(b.mode, "auto")

    def test_dict_roundtrip(self):
        b = AdapterBinding(cartridge="pytest-self-healing", version="2.0.1", priority=5, mode="patch",
                           overrides={"guardrails": ["extra"]})
        self.assertEqual(AdapterBinding.from_dict(b.to_dict()), b)

    def test_invalid_binding_rejected(self):
        bad_inputs = [
            {"cartridge": "ok", "mode": "weird"},
            {"cartridge": "ok", "version": "abc"},
            {"cartridge": "Not Valid"},
            {"cartridge": "ok", "priority": "high"},
            {"nope": 1},
            42,
        ]
        for bad in bad_inputs:
            with self.subTest(bad=bad), self.assertRaises(CartridgeValidationError):
                AdapterBinding.from_dict(bad)


class TestMotherCardBackwardCompat(unittest.TestCase):
    def _mother(self, **kw):
        return MotherCard(id="m1", name="Mother", core_philosophy="test", **kw)

    def test_default_has_no_cartridges(self):
        self.assertEqual(self._mother().attached_cartridges, [])

    def test_legacy_yaml_without_cartridges_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mother.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    "kind: MotherCard\n"
                    "metadata:\n  id: legacy\n  name: Legacy\n"
                    "spec:\n  core_philosophy: old\n"
                )
            mother = MotherCard.from_yaml(path)
        self.assertEqual(mother.id, "legacy")
        self.assertEqual(mother.attached_cartridges, [])

    def test_no_cartridges_key_emitted_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mother.yaml")
            self._mother().to_yaml(path)
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        self.assertNotIn("attached_cartridges", data["spec"])

    def test_mother_roundtrip_with_cartridges(self):
        mother = self._mother(attached_cartridges=[
            AdapterBinding(cartridge="security-shield", version="1.2.0", priority=1),
            AdapterBinding(cartridge="fastapi-backend"),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mother.yaml")
            mother.to_yaml(path)
            loaded = MotherCard.from_yaml(path)
        self.assertEqual(loaded.attached_cartridges, mother.attached_cartridges)
        self.assertEqual(loaded.core_philosophy, "test")

    def test_mother_accepts_string_shorthand_bindings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mother.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    "metadata:\n  id: m\n  name: M\n"
                    "spec:\n  core_philosophy: p\n  attached_cartridges:\n    - security-shield\n"
                )
            mother = MotherCard.from_yaml(path)
        self.assertEqual(mother.attached_cartridges[0].cartridge, "security-shield")


if __name__ == "__main__":
    unittest.main()
