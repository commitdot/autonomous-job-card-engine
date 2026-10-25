"""
Unit Tests for Adapter Registry, LoRA Manager, and Patch Loader
==============================================================
"""

import hashlib
import io
import os
import shutil
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from src.adapters import (
    AdapterRegistry,
    ChecksumMismatchError,
    HarnessConfig,
    LoRADownloadError,
    LoRAManager,
    PatchLoaderError,
    apply_cartridge,
    apply_cartridges,
    load_and_merge_cartridge_file,
    merge_harness_patch,
)
from src.adapters.registry import CartridgeNotFoundError, _parse_semver
from src.models import (
    AdapterBinding,
    AdapterCartridge,
    CartridgeMetadata,
    CartridgeSpec,
)


def _sample_cartridge(
    name: str = "security-guard",
    version: str = "1.0.0",
    domain: str = "security",
    tags: list = None,
    cart_type: str = "harness-patch",
    lora_repo_id: str = "",
    lora_filename: str = "",
    lora_sha256: str = "",
    guardrails: list = None,
    system_prompt: str = "Secure prompt",
) -> AdapterCartridge:
    return AdapterCartridge(
        metadata=CartridgeMetadata(
            name=name,
            version=version,
            author="AJE Team",
            description=f"Description for {name}",
            domain=domain,
            tags=tags or ["security", "owasp"],
        ),
        spec=CartridgeSpec(
            type=cart_type,
            base_models=["granite-3-8b", "llama-3"],
            compatible_archetypes=["squad-backend", "squad-qa"],
            lora_repo_id=lora_repo_id,
            lora_filename=lora_filename,
            lora_sha256=lora_sha256,
            harness_patch={
                "system_prompt": system_prompt,
                "guardrails": guardrails or ["Enforce OWASP Top 10", "Validate JWT tokens"],
                "tool_guidance": ["Use bandit for SAST"],
            },
            benchmarks={"pass_rate": 0.95},
        ),
    )


class TestLoRAManager(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.lora_mgr = LoRAManager(cache_dir=self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_compute_sha256_and_verify(self):
        test_file = os.path.join(self.tmp_dir, "test.bin")
        content = b"autonomous-job-card-engine-lora-weights"
        with open(test_file, "wb") as f:
            f.write(content)

        expected_sha = hashlib.sha256(content).hexdigest()
        self.assertEqual(self.lora_mgr.compute_sha256(test_file), expected_sha)
        self.assertTrue(self.lora_mgr.verify_checksum(test_file, expected_sha))
        self.assertTrue(self.lora_mgr.verify_checksum(test_file, expected_sha.upper()))
        self.assertFalse(self.lora_mgr.verify_checksum(test_file, "wrong" + "0" * 59))
        self.assertFalse(self.lora_mgr.verify_checksum("non_existent_file.bin", expected_sha))

    def test_save_weights_and_is_cached(self):
        cart = _sample_cartridge(
            name="cuda-opt",
            cart_type="lora",
            lora_repo_id="org/cuda-weights",
            lora_filename="adapter.safetensors",
        )
        content = b"fake-cuda-safetensors-data"
        expected_sha = hashlib.sha256(content).hexdigest()
        cart.spec.lora_sha256 = expected_sha

        # Initially not cached
        self.assertFalse(self.lora_mgr.is_cached(cart))

        # Save weights
        path = self.lora_mgr.save_weights_to_cache(cart, content, expected_sha256=expected_sha)
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(self.lora_mgr.is_cached(cart))
        self.assertEqual(self.lora_mgr.get_adapter_path(cart), path)

        # List cached
        cached_list = self.lora_mgr.list_cached()
        self.assertEqual(len(cached_list), 1)
        self.assertEqual(cached_list[0]["cartridge"], "cuda-opt")
        self.assertEqual(cached_list[0]["filename"], "adapter.safetensors")
        self.assertEqual(cached_list[0]["sha256"], expected_sha)

        # Clear cache for specific cartridge
        removed = self.lora_mgr.clear_cache("cuda-opt")
        self.assertEqual(removed, 1)
        self.assertFalse(self.lora_mgr.is_cached(cart))

    def test_save_weights_checksum_mismatch(self):
        cart = _sample_cartridge(
            name="cuda-opt",
            cart_type="lora",
            lora_repo_id="org/cuda-weights",
            lora_filename="adapter.safetensors",
        )
        content = b"data"
        with self.assertRaises(ChecksumMismatchError):
            self.lora_mgr.save_weights_to_cache(cart, content, expected_sha256="0" * 64)

    def test_build_hf_url(self):
        url = self.lora_mgr.build_hf_url("aje/fastapi-lora", "adapter.bin", revision="v1.0")
        self.assertEqual(url, "https://huggingface.co/aje/fastapi-lora/resolve/v1.0/adapter.bin")

    @patch("urllib.request.urlopen")
    def test_download_weights_success(self, mock_urlopen):
        content = b"downloaded-model-weights"
        sha = hashlib.sha256(content).hexdigest()

        mock_resp = MagicMock()
        mock_resp.read.side_effect = [content, b""]
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        cart = _sample_cartridge(
            name="fintech-lora",
            cart_type="lora",
            lora_repo_id="aje/fintech",
            lora_filename="adapter.safetensors",
            lora_sha256=sha,
        )

        path = self.lora_mgr.download_weights(cart)
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(self.lora_mgr.is_cached(cart))

        # Downloading again without force should return cached path without network call
        mock_urlopen.reset_mock()
        second_path = self.lora_mgr.download_weights(cart)
        self.assertEqual(path, second_path)
        mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_download_weights_checksum_mismatch(self, mock_urlopen):
        content = b"downloaded-content"
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [content, b""]
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        cart = _sample_cartridge(
            name="corrupted-lora",
            cart_type="lora",
            lora_repo_id="aje/corrupted",
            lora_filename="adapter.safetensors",
            lora_sha256="f" * 64,
        )

        with self.assertRaises(ChecksumMismatchError):
            self.lora_mgr.download_weights(cart)

    @patch("urllib.request.urlopen")
    def test_download_weights_http_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://huggingface.co", code=404, msg="Not Found", hdrs={}, fp=io.BytesIO()
        )

        cart = _sample_cartridge(
            name="missing-lora",
            cart_type="lora",
            lora_repo_id="aje/missing",
            lora_filename="adapter.safetensors",
        )

        with self.assertRaises(LoRADownloadError) as ctx:
            self.lora_mgr.download_weights(cart)
        self.assertIn("HTTP error 404", str(ctx.exception))

    def test_download_weights_missing_repo_or_filename(self):
        cart = _sample_cartridge(name="no-repo", cart_type="harness-patch")
        with self.assertRaises(ValueError):
            self.lora_mgr.download_weights(cart)


class TestPatchLoader(unittest.TestCase):
    def test_merge_harness_patch_basic(self):
        base = HarnessConfig(
            system_prompt="Base prompt.",
            guardrails=["Base guardrail 1"],
            tool_guidance=["Base tool guidance"],
        )
        cart = _sample_cartridge(
            name="security-guard",
            system_prompt="Cartridge prompt.",
            guardrails=["Base guardrail 1", "New security guardrail"],
        )

        merged = merge_harness_patch(base, cart)

        self.assertEqual(merged.system_prompt, "Base prompt.\n\nCartridge prompt.")
        # Deduplicated guardrails
        self.assertEqual(merged.guardrails, ["Base guardrail 1", "New security guardrail"])
        self.assertEqual(merged.tool_guidance, ["Base tool guidance", "Use bandit for SAST"])
        self.assertEqual(merged.metadata.get("applied_cartridges"), ["security-guard"])

    def test_merge_harness_patch_empty_base(self):
        base = HarnessConfig()
        cart = _sample_cartridge(name="empty-base-test")
        merged = merge_harness_patch(base, cart)
        self.assertEqual(merged.system_prompt, "Secure prompt")
        self.assertEqual(len(merged.guardrails), 2)

    def test_merge_harness_patch_with_overrides(self):
        base = HarnessConfig(guardrails=["Initial"])
        cart = _sample_cartridge(name="override-test", guardrails=["Rule A"])
        overrides = {
            "guardrails": ["Rule B", "Rule C"],
            "skills": ["custom-skill"],
        }
        merged = merge_harness_patch(base, cart, overrides=overrides)
        self.assertIn("Initial", merged.guardrails)
        self.assertIn("Rule A", merged.guardrails)
        self.assertIn("Rule B", merged.guardrails)
        self.assertIn("Rule C", merged.guardrails)
        self.assertEqual(merged.skills, ["custom-skill"])

    def test_apply_cartridge_disabled_binding(self):
        base = HarnessConfig(system_prompt="Unchanged prompt.")
        cart = _sample_cartridge(name="disabled-test")
        binding = AdapterBinding(cartridge="disabled-test", enabled=False)

        result = apply_cartridge(base, cart, binding)
        self.assertEqual(result.system_prompt, "Unchanged prompt.")
        self.assertEqual(result.guardrails, [])

    def test_apply_cartridges_priority_order(self):
        base = HarnessConfig(system_prompt="Base", guardrails=["Base Rule"])

        cart_low = _sample_cartridge(
            name="low-prio",
            system_prompt="Low Prio Prompt",
            guardrails=["Low Rule"],
        )
        cart_high = _sample_cartridge(
            name="high-prio",
            system_prompt="High Prio Prompt",
            guardrails=["High Rule"],
        )

        binding_low = AdapterBinding(cartridge="low-prio", priority=1)
        binding_high = AdapterBinding(cartridge="high-prio", priority=10)

        # Pass in reverse order to verify sort by priority
        result = apply_cartridges(base, [(cart_high, binding_high), (cart_low, binding_low)])

        # Base -> low-prio -> high-prio
        self.assertEqual(result.system_prompt, "Base\n\nLow Prio Prompt\n\nHigh Prio Prompt")
        self.assertEqual(result.guardrails, ["Base Rule", "Low Rule", "High Rule"])
        self.assertEqual(result.metadata.get("applied_cartridges"), ["low-prio", "high-prio"])

    def test_load_and_merge_cartridge_file(self):
        cart = _sample_cartridge(name="disk-cartridge")
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = os.path.join(tmp, "cartridge.yaml")
            cart.to_yaml(yaml_path)

            base = HarnessConfig(system_prompt="Base")
            merged = load_and_merge_cartridge_file(base, yaml_path)
            self.assertEqual(merged.system_prompt, "Base\n\nSecure prompt")
            self.assertIn("Enforce OWASP Top 10", merged.guardrails)


class TestAdapterRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.catalog_dir = os.path.join(self.tmp_dir, "catalog")
        self.installed_dir = os.path.join(self.tmp_dir, "installed")
        self.cache_dir = os.path.join(self.tmp_dir, "cache")

        os.makedirs(self.catalog_dir, exist_ok=True)
        os.makedirs(self.installed_dir, exist_ok=True)

        self.lora_mgr = LoRAManager(cache_dir=self.cache_dir)
        self.registry = AdapterRegistry(
            registry_dirs=[self.catalog_dir],
            installed_dir=self.installed_dir,
            lora_manager=self.lora_mgr,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _seed_catalog_cartridge(self, cart: AdapterCartridge, subpath: str = "") -> str:
        dest_dir = os.path.join(self.catalog_dir, subpath, cart.metadata.name)
        os.makedirs(dest_dir, exist_ok=True)
        manifest_path = os.path.join(dest_dir, "cartridge.yaml")
        cart.to_yaml(manifest_path)
        return manifest_path

    def test_semver_parser(self):
        self.assertEqual(_parse_semver("1.2.3"), (1, 2, 3))
        self.assertEqual(_parse_semver("0.1.0-alpha"), (0, 1, 0))
        self.assertEqual(_parse_semver("invalid"), (0, 0, 0))

    def test_indexing_and_search_by_query_and_domain(self):
        c1 = _sample_cartridge(name="security-shield", domain="cybersecurity", tags=["owasp", "jwt"])
        c2 = _sample_cartridge(name="fastapi-backend", domain="web", tags=["fastapi", "pydantic"])
        c3 = _sample_cartridge(name="healthcare-hipaa", domain="healthcare", tags=["hipaa", "fhir"])

        self._seed_catalog_cartridge(c1, "tech")
        self._seed_catalog_cartridge(c2, "tech")
        self._seed_catalog_cartridge(c3, "industry")

        indexed_count = self.registry.refresh_index()
        self.assertEqual(indexed_count, 3)

        # Free text search
        res = self.registry.search(query="fastapi")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].metadata.name, "fastapi-backend")

        # Tag search
        res_tag = self.registry.search(tag="owasp")
        self.assertEqual(len(res_tag), 1)
        self.assertEqual(res_tag[0].metadata.name, "security-shield")

        # Domain search
        res_domain = self.registry.search(domain="healthcare")
        self.assertEqual(len(res_domain), 1)
        self.assertEqual(res_domain[0].metadata.name, "healthcare-hipaa")

        # Non-matching search
        res_none = self.registry.search(query="nonexistent-xyz")
        self.assertEqual(len(res_none), 0)

    def test_search_filters_archetype_and_base_model(self):
        c1 = _sample_cartridge(name="cuda-opt")
        c1.spec.base_models = ["granite-3-8b"]
        c1.spec.compatible_archetypes = ["squad-performance"]

        c2 = _sample_cartridge(name="pytest-heal")
        c2.spec.base_models = ["llama-3-70b"]
        c2.spec.compatible_archetypes = ["squad-qa"]

        self._seed_catalog_cartridge(c1)
        self._seed_catalog_cartridge(c2)
        self.registry.refresh_index()

        res_arch = self.registry.search(archetype="squad-performance")
        self.assertEqual(len(res_arch), 1)
        self.assertEqual(res_arch[0].metadata.name, "cuda-opt")

        res_bm = self.registry.search(base_model="llama-3")
        self.assertEqual(len(res_bm), 1)
        self.assertEqual(res_bm[0].metadata.name, "pytest-heal")

    def test_version_resolution_and_latest(self):
        c_v1 = _sample_cartridge(name="versioned-cart", version="1.0.0")
        c_v2 = _sample_cartridge(name="versioned-cart", version="2.1.0")

        self._seed_catalog_cartridge(c_v1, "v1")
        self._seed_catalog_cartridge(c_v2, "v2")
        self.registry.refresh_index()

        latest = self.registry.get_cartridge("versioned-cart", "latest")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.metadata.version, "2.1.0")

        v1 = self.registry.get_cartridge("versioned-cart", "1.0.0")
        self.assertIsNotNone(v1)
        self.assertEqual(v1.metadata.version, "1.0.0")

    def test_pull_cartridge_and_list_installed(self):
        c = _sample_cartridge(name="installable-pack")
        self._seed_catalog_cartridge(c)
        self.registry.refresh_index()

        self.assertFalse(self.registry.is_installed("installable-pack"))
        self.assertEqual(len(self.registry.list_installed()), 0)

        # Pull
        pulled = self.registry.pull("installable-pack")
        self.assertEqual(pulled.metadata.name, "installable-pack")
        self.assertTrue(self.registry.is_installed("installable-pack"))

        installed = self.registry.list_installed()
        self.assertEqual(len(installed), 1)
        self.assertEqual(installed[0].metadata.name, "installable-pack")

    def test_pull_from_direct_file_path(self):
        c = _sample_cartridge(name="direct-file-cart")
        custom_path = os.path.join(self.tmp_dir, "custom_cart.yaml")
        c.to_yaml(custom_path)

        pulled = self.registry.pull(custom_path)
        self.assertEqual(pulled.metadata.name, "direct-file-cart")
        self.assertTrue(self.registry.is_installed("direct-file-cart"))

    def test_pull_nonexistent_raises_error(self):
        with self.assertRaises(CartridgeNotFoundError):
            self.registry.pull("totally-nonexistent-cartridge")

    def test_uninstall_cartridge(self):
        c = _sample_cartridge(name="to-uninstall")
        self._seed_catalog_cartridge(c)
        self.registry.refresh_index()

        self.registry.pull("to-uninstall")
        self.assertTrue(self.registry.is_installed("to-uninstall"))

        uninstalled = self.registry.uninstall("to-uninstall")
        self.assertTrue(uninstalled)
        self.assertFalse(self.registry.is_installed("to-uninstall"))

        # Uninstall again returns False
        self.assertFalse(self.registry.uninstall("to-uninstall"))

    def test_register_cartridge_in_memory(self):
        c = _sample_cartridge(name="in-memory-cart")
        self.registry.register_cartridge(c)

        found = self.registry.get_cartridge("in-memory-cart")
        self.assertIsNotNone(found)
        self.assertEqual(found.metadata.name, "in-memory-cart")


if __name__ == "__main__":
    unittest.main()
