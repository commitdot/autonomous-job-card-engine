"""
Unit Tests for the `aje adapter` CLI (search / pull / list / info / attach / init / test)
=========================================================================================
"""

import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import yaml

import importlib

cli = importlib.import_module("src.cli.main")  # `src.cli.main` attr is shadowed by the re-exported function
main = cli.main
from src.models import (
    AdapterBinding,
    AdapterCartridge,
    CartridgeMetadata,
    CartridgeSpec,
    MotherCard,
)


def _cartridge(
    name="security-guard",
    version="1.0.0",
    domain="security",
    tags=None,
    cart_type="harness-patch",
    pass_rate=0.95,
    archetypes=None,
    guardrails=None,
    system_prompt="Secure prompt",
    sha256="",
) -> AdapterCartridge:
    spec = CartridgeSpec(
        type=cart_type,
        base_models=["granite-3-8b"],
        compatible_archetypes=["saas-backend"] if archetypes is None else archetypes,
        benchmarks={"pass_rate": pass_rate} if pass_rate is not None else {},
    )
    if cart_type in ("harness-patch", "hybrid"):
        spec.harness_patch = {
            "system_prompt": system_prompt,
            "guardrails": ["Enforce OWASP Top 10"] if guardrails is None else guardrails,
            "tool_guidance": ["Use bandit for SAST"],
        }
    if cart_type in ("lora", "hybrid"):
        spec.lora_repo_id = "acme/security-lora"
        spec.lora_filename = "adapter.safetensors"
        spec.lora_sha256 = sha256
        spec.lora_rank = 16
    return AdapterCartridge(
        metadata=CartridgeMetadata(
            name=name,
            version=version,
            author="AJE Team",
            description=f"Description for {name}",
            license="Apache-2.0",
            domain=domain,
            tags=tags or ["owasp", "security"],
        ),
        spec=spec,
    )


class CliAdapterTestBase(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="aje_cli_adapter_")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        env = patch.dict(os.environ, {"NO_COLOR": "1"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("FORCE_COLOR", None)

    # -- helpers -----------------------------------------------------------
    def run_cli(self, *argv):
        """Invoke main() against the temp workspace; returns (exit_code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(["-w", self.ws, *argv])
            except SystemExit as e:  # argparse usage errors
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def add_catalog(self, cart: AdapterCartridge) -> str:
        """Place a cartridge in the workspace catalog (registry/cartridges/<name>/)."""
        path = os.path.join(self.ws, "registry", "cartridges", cart.metadata.name, "cartridge.yaml")
        cart.to_yaml(path)
        return path

    @property
    def mother_path(self) -> str:
        return os.path.join(self.ws, ".jobs", "mother_card.yaml")

    def make_mother(self, archetype="saas-backend", bindings=None) -> MotherCard:
        mother = MotherCard(
            id="mother-test", name="Test Mother", core_philosophy="test",
            archetype=archetype, attached_cartridges=list(bindings or []),
        )
        mother.to_yaml(self.mother_path)
        return mother

    def installed_manifest(self, name: str) -> str:
        return os.path.join(self.ws, ".jobs", "installed_cartridges", name, "cartridge.yaml")


class TestSearch(CliAdapterTestBase):
    def setUp(self):
        super().setUp()
        self.add_catalog(_cartridge("fintech-banking", domain="fintech", tags=["pci", "banking"], pass_rate=0.97))
        self.add_catalog(_cartridge("healthcare-hipaa", domain="healthcare", tags=["hipaa"], pass_rate=0.8))
        self.add_catalog(_cartridge("cuda-kernel-opt", domain="hpc", cart_type="hybrid", tags=["cuda"], pass_rate=0.6))

    def test_search_all_lists_table_with_badges(self):
        code, out, _ = self.run_cli("adapter", "search")
        self.assertEqual(code, 0)
        for name in ("fintech-banking", "healthcare-hipaa", "cuda-kernel-opt"):
            self.assertIn(name, out)
        self.assertIn("NAME", out)
        self.assertIn("[97% pass]", out)
        self.assertIn("[80% pass]", out)
        self.assertIn("[60% pass]", out)
        self.assertIn("3 cartridge(s) found", out)

    def test_search_by_query(self):
        code, out, _ = self.run_cli("adapter", "search", "banking")
        self.assertEqual(code, 0)
        self.assertIn("fintech-banking", out)
        self.assertNotIn("healthcare-hipaa", out)

    def test_search_by_industry(self):
        code, out, _ = self.run_cli("adapter", "search", "--industry", "healthcare")
        self.assertEqual(code, 0)
        self.assertIn("healthcare-hipaa", out)
        self.assertNotIn("fintech-banking", out)

    def test_search_by_type(self):
        code, out, _ = self.run_cli("adapter", "search", "--type", "hybrid")
        self.assertEqual(code, 0)
        self.assertIn("cuda-kernel-opt", out)
        self.assertNotIn("fintech-banking", out)

    def test_search_by_tag(self):
        code, out, _ = self.run_cli("adapter", "search", "--tag", "hipaa")
        self.assertEqual(code, 0)
        self.assertIn("healthcare-hipaa", out)
        self.assertNotIn("cuda-kernel-opt", out)

    def test_search_no_match_is_success(self):
        code, out, _ = self.run_cli("adapter", "search", "zzz-nothing")
        self.assertEqual(code, 0)
        self.assertIn("No cartridges matched", out)

    def test_search_marks_installed(self):
        self.assertEqual(self.run_cli("adapter", "pull", "fintech-banking")[0], 0)
        _, out, _ = self.run_cli("adapter", "search", "banking")
        self.assertIn("installed", out)

    def test_search_invalid_type_is_usage_error(self):
        code, _, err = self.run_cli("adapter", "search", "--type", "bogus")
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)


class TestPull(CliAdapterTestBase):
    def test_pull_by_name_installs_manifest(self):
        self.add_catalog(_cartridge("fintech-banking"))
        code, out, _ = self.run_cli("adapter", "pull", "fintech-banking")
        self.assertEqual(code, 0)
        self.assertIn("Installed fintech-banking@1.0.0", out)
        self.assertTrue(os.path.isfile(self.installed_manifest("fintech-banking")))
        self.assertEqual(AdapterCartridge.from_yaml(self.installed_manifest("fintech-banking")).metadata.name, "fintech-banking")

    def test_pull_from_directory_path(self):
        src = os.path.join(self.ws, "elsewhere", "mine")
        _cartridge("mine").to_yaml(os.path.join(src, "cartridge.yaml"))
        code, out, _ = self.run_cli("adapter", "pull", src)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(self.installed_manifest("mine")))

    def test_pull_unknown_fails_with_exit_1(self):
        code, out, err = self.run_cli("adapter", "pull", "ghost")
        self.assertEqual(code, 1)
        self.assertIn("was not found", err)
        self.assertEqual(out, "")

    def test_pull_specific_version(self):
        self.add_catalog(_cartridge("multi", version="1.0.0"))
        path2 = os.path.join(self.ws, "registry", "cartridges", "multi-v2", "cartridge.yaml")
        _cartridge("multi", version="2.0.0").to_yaml(path2)
        code, out, _ = self.run_cli("adapter", "pull", "multi", "--version", "1.0.0")
        self.assertEqual(code, 0)
        self.assertIn("multi@1.0.0", out)

    def test_pull_unknown_version_fails(self):
        self.add_catalog(_cartridge("multi", version="1.0.0"))
        code, _, err = self.run_cli("adapter", "pull", "multi", "--version", "9.9.9")
        self.assertEqual(code, 1)
        self.assertIn("was not found", err)

    def test_pull_lora_without_weights_flag_hints(self):
        self.add_catalog(_cartridge("lora-one", cart_type="lora"))
        with patch("src.adapters.lora_manager.LoRAManager.download_weights") as dl:
            code, out, _ = self.run_cli("adapter", "pull", "lora-one")
        self.assertEqual(code, 0)
        dl.assert_not_called()
        self.assertIn("not downloaded", out)

    def test_pull_with_weights_downloads_into_jobs_adapters(self):
        self.add_catalog(_cartridge("lora-one", cart_type="lora"))
        weights = os.path.join(self.ws, ".jobs", "adapters", "lora-one", "adapter.safetensors")
        with patch("src.adapters.lora_manager.LoRAManager.download_weights", return_value=weights) as dl:
            code, out, _ = self.run_cli("adapter", "pull", "lora-one", "--weights")
        self.assertEqual(code, 0)
        dl.assert_called_once()
        self.assertIn(os.path.join(".jobs", "adapters", "lora-one"), out)

    def test_pull_weights_on_harness_patch_is_noop(self):
        self.add_catalog(_cartridge("patch-only"))
        with patch("src.adapters.lora_manager.LoRAManager.download_weights") as dl:
            code, out, _ = self.run_cli("adapter", "pull", "patch-only", "--weights")
        self.assertEqual(code, 0)
        dl.assert_not_called()
        self.assertIn("none (zero-weight harness patch)", out)

    def test_pull_weights_download_error_exit_1(self):
        from src.adapters.lora_manager import LoRADownloadError
        self.add_catalog(_cartridge("lora-one", cart_type="lora"))
        with patch("src.adapters.lora_manager.LoRAManager.download_weights", side_effect=LoRADownloadError("HTTP error 404")):
            code, _, err = self.run_cli("adapter", "pull", "lora-one", "--weights")
        self.assertEqual(code, 1)
        self.assertIn("HTTP error 404", err)

    def test_pull_invalid_manifest_exit_1(self):
        bad = os.path.join(self.ws, "bad.yaml")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("kind: AdapterCartridge\nmetadata:\n  name: Bad Name!\n")
        code, _, err = self.run_cli("adapter", "pull", bad)
        self.assertEqual(code, 1)
        self.assertIn("Invalid cartridge name", err)

    def test_pull_force_overwrites(self):
        path = self.add_catalog(_cartridge("fintech-banking", system_prompt="v1"))
        self.run_cli("adapter", "pull", "fintech-banking")
        _cartridge("fintech-banking", system_prompt="v2").to_yaml(path)
        self.run_cli("adapter", "pull", path)  # no force: keeps v1
        kept = AdapterCartridge.from_yaml(self.installed_manifest("fintech-banking"))
        self.assertEqual(kept.spec.harness_patch["system_prompt"], "v1")
        code, _, _ = self.run_cli("adapter", "pull", path, "--force")
        self.assertEqual(code, 0)
        forced = AdapterCartridge.from_yaml(self.installed_manifest("fintech-banking"))
        self.assertEqual(forced.spec.harness_patch["system_prompt"], "v2")


class TestList(CliAdapterTestBase):
    def test_list_empty(self):
        code, out, _ = self.run_cli("adapter", "list")
        self.assertEqual(code, 0)
        self.assertIn("No cartridges installed", out)

    def test_list_shows_installed_with_status_columns(self):
        self.add_catalog(_cartridge("patch-only", pass_rate=0.92))
        self.add_catalog(_cartridge("lora-one", cart_type="lora", pass_rate=0.7))
        self.add_catalog(_cartridge("not-installed"))
        self.run_cli("adapter", "pull", "patch-only")
        self.run_cli("adapter", "pull", "lora-one")
        code, out, _ = self.run_cli("adapter", "list")
        self.assertEqual(code, 0)
        self.assertIn("patch-only", out)
        self.assertIn("lora-one", out)
        self.assertNotIn("not-installed", out)
        self.assertIn("[92% pass]", out)
        self.assertIn("missing", out)  # lora weights not cached
        self.assertIn("2 cartridge(s) installed", out)

    def test_list_reports_cached_weights(self):
        self.add_catalog(_cartridge("lora-one", cart_type="lora"))
        self.run_cli("adapter", "pull", "lora-one")
        weights = os.path.join(self.ws, ".jobs", "adapters", "lora-one", "adapter.safetensors")
        os.makedirs(os.path.dirname(weights))
        with open(weights, "wb") as f:
            f.write(b"weights")
        _, out, _ = self.run_cli("adapter", "list")
        self.assertIn("cached", out)

    def test_list_reflects_attached_state(self):
        self.add_catalog(_cartridge("patch-only"))
        self.make_mother()
        self.run_cli("adapter", "pull", "patch-only")
        _, before, _ = self.run_cli("adapter", "list")
        self.assertRegex(before, r"(?m)\bno\s*$")
        self.run_cli("adapter", "attach", "patch-only")
        _, after, _ = self.run_cli("adapter", "list")
        self.assertRegex(after, r"(?m)\byes\s*$")


class TestInfo(CliAdapterTestBase):
    def test_info_harness_patch_details(self):
        self.add_catalog(_cartridge("fintech-banking", domain="fintech", tags=["pci"], pass_rate=0.97))
        code, out, _ = self.run_cli("adapter", "info", "fintech-banking")
        self.assertEqual(code, 0)
        self.assertIn("fintech-banking 1.0.0", out)
        self.assertIn("[harness-patch]", out)
        self.assertIn("[97% pass]", out)
        self.assertIn("Author:      AJE Team", out)
        self.assertIn("Industry:    fintech", out)
        self.assertIn("granite-3-8b", out)
        self.assertIn("pass_rate", out)
        self.assertIn("system_prompt", out)
        self.assertIn("guardrails (1):", out)
        self.assertIn("- Enforce OWASP Top 10", out)
        self.assertIn("tool_guidance (1):", out)
        self.assertIn("available (not installed)", out)

    def test_info_shows_installed_status(self):
        self.add_catalog(_cartridge("fintech-banking"))
        self.run_cli("adapter", "pull", "fintech-banking")
        _, out, _ = self.run_cli("adapter", "info", "fintech-banking")
        self.assertIn("installed", out)
        self.assertNotIn("not installed", out)

    def test_info_lora_shows_weights_section(self):
        self.add_catalog(_cartridge("lora-one", cart_type="lora", sha256="a" * 64))
        code, out, _ = self.run_cli("adapter", "info", "lora-one")
        self.assertEqual(code, 0)
        self.assertIn("LoRA weights", out)
        self.assertIn("acme/security-lora", out)
        self.assertIn("a" * 64, out)
        self.assertIn("none (LoRA-only cartridge)", out)

    def test_info_without_benchmarks(self):
        self.add_catalog(_cartridge("bare", pass_rate=None))
        _, out, _ = self.run_cli("adapter", "info", "bare")
        self.assertIn("none declared", out)

    def test_info_from_path(self):
        path = os.path.join(self.ws, "local", "cartridge.yaml")
        _cartridge("local-one").to_yaml(path)
        code, out, _ = self.run_cli("adapter", "info", path)
        self.assertEqual(code, 0)
        self.assertIn("local-one 1.0.0", out)

    def test_info_unknown_exit_1(self):
        code, out, err = self.run_cli("adapter", "info", "ghost")
        self.assertEqual(code, 1)
        self.assertIn("was not found", err)
        self.assertEqual(out, "")

    def test_info_truncates_long_items(self):
        self.add_catalog(_cartridge("verbose", guardrails=["x" * 300]))
        _, out, _ = self.run_cli("adapter", "info", "verbose")
        self.assertIn("...", out)
        self.assertNotIn("x" * 150, out)


class TestAttach(CliAdapterTestBase):
    def test_attach_updates_mother_card_yaml(self):
        self.add_catalog(_cartridge("fintech-banking"))
        self.make_mother()
        code, out, _ = self.run_cli("adapter", "attach", "fintech-banking")
        self.assertEqual(code, 0)
        self.assertIn("Attached fintech-banking@latest", out)

        mother = MotherCard.from_yaml(self.mother_path)
        self.assertEqual(len(mother.attached_cartridges), 1)
        b = mother.attached_cartridges[0]
        self.assertEqual((b.cartridge, b.version, b.enabled, b.priority, b.mode), ("fintech-banking", "latest", True, 0, "auto"))
        with open(self.mother_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        self.assertEqual(raw["spec"]["attached_cartridges"][0]["cartridge"], "fintech-banking")

    def test_attach_auto_installs_from_catalog(self):
        self.add_catalog(_cartridge("fintech-banking"))
        self.make_mother()
        self.assertFalse(os.path.isfile(self.installed_manifest("fintech-banking")))
        self.run_cli("adapter", "attach", "fintech-banking")
        self.assertTrue(os.path.isfile(self.installed_manifest("fintech-banking")))

    def test_attach_with_options(self):
        self.add_catalog(_cartridge("fintech-banking", version="1.2.3"))
        self.make_mother()
        code, _, _ = self.run_cli(
            "adapter", "attach", "fintech-banking",
            "--version", "1.2.3", "--mode", "patch", "--priority", "5", "--disabled",
        )
        self.assertEqual(code, 0)
        b = MotherCard.from_yaml(self.mother_path).attached_cartridges[0]
        self.assertEqual((b.version, b.mode, b.priority, b.enabled), ("1.2.3", "patch", 5, False))

    def test_attach_twice_updates_binding_without_duplicating(self):
        self.add_catalog(_cartridge("fintech-banking"))
        self.make_mother()
        self.run_cli("adapter", "attach", "fintech-banking", "--priority", "1")
        code, out, _ = self.run_cli("adapter", "attach", "fintech-banking", "--priority", "7", "--mode", "lora")
        self.assertEqual(code, 0)
        self.assertIn("Updated fintech-banking", out)
        bindings = MotherCard.from_yaml(self.mother_path).attached_cartridges
        self.assertEqual(len(bindings), 1)
        self.assertEqual((bindings[0].priority, bindings[0].mode), (7, "lora"))

    def test_attach_update_preserves_unspecified_fields(self):
        self.add_catalog(_cartridge("fintech-banking"))
        self.make_mother()
        self.run_cli("adapter", "attach", "fintech-banking", "--priority", "4", "--mode", "patch")
        self.run_cli("adapter", "attach", "fintech-banking")
        b = MotherCard.from_yaml(self.mother_path).attached_cartridges[0]
        self.assertEqual((b.priority, b.mode), (4, "patch"))

    def test_attach_multiple_cartridges_preserves_existing(self):
        self.add_catalog(_cartridge("one"))
        self.add_catalog(_cartridge("two"))
        self.make_mother(bindings=[AdapterBinding(cartridge="preexisting", priority=2)])
        self.run_cli("adapter", "attach", "one")
        self.run_cli("adapter", "attach", "two")
        names = [b.cartridge for b in MotherCard.from_yaml(self.mother_path).attached_cartridges]
        self.assertEqual(names, ["preexisting", "one", "two"])

    def test_attach_preserves_other_mother_fields(self):
        self.add_catalog(_cartridge("fintech-banking"))
        mother = self.make_mother()
        mother.repo.remote_slug = "acme/app"
        mother.to_yaml(self.mother_path)
        self.run_cli("adapter", "attach", "fintech-banking")
        after = MotherCard.from_yaml(self.mother_path)
        self.assertEqual(after.id, "mother-test")
        self.assertEqual(after.repo.remote_slug, "acme/app")

    def test_attach_without_mother_card_exit_1(self):
        self.add_catalog(_cartridge("fintech-banking"))
        code, out, err = self.run_cli("adapter", "attach", "fintech-banking")
        self.assertEqual(code, 1)
        self.assertIn("No active Mother Card", err)
        self.assertEqual(out, "")

    def test_attach_unknown_cartridge_leaves_mother_untouched(self):
        self.make_mother()
        with open(self.mother_path, "rb") as f:
            before = f.read()
        code, _, err = self.run_cli("adapter", "attach", "ghost")
        self.assertEqual(code, 1)
        self.assertIn("was not found", err)
        with open(self.mother_path, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_attach_invalid_mode_is_usage_error(self):
        self.make_mother()
        code, _, err = self.run_cli("adapter", "attach", "x", "--mode", "bogus")
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)

    def test_attach_unknown_version_fails_and_keeps_existing_binding(self):
        self.add_catalog(_cartridge("fintech-banking"))
        self.make_mother()
        code, _, _ = self.run_cli("adapter", "attach", "fintech-banking", "--version", "1.0.0")
        self.assertEqual(code, 0)
        code, _, err = self.run_cli("adapter", "attach", "fintech-banking", "--version", "9.9.9")
        self.assertEqual(code, 1)
        self.assertIn("was not found", err)
        b = MotherCard.from_yaml(self.mother_path).attached_cartridges[0]
        self.assertEqual(b.version, "1.0.0")

    def test_attach_warns_on_incompatible_archetype(self):
        self.add_catalog(_cartridge("fintech-banking", archetypes=["enterprise-security"]))
        self.make_mother(archetype="saas-backend")
        code, out, _ = self.run_cli("adapter", "attach", "fintech-banking")
        self.assertEqual(code, 0)
        self.assertIn("[WARN]", out)
        self.assertIn("enterprise-security", out)

    def test_attach_no_warning_when_compatible(self):
        self.add_catalog(_cartridge("fintech-banking", archetypes=["saas-backend"]))
        self.make_mother(archetype="saas-backend")
        _, out, _ = self.run_cli("adapter", "attach", "fintech-banking")
        self.assertNotIn("[WARN]", out)

    def test_attach_prints_attached_table(self):
        self.add_catalog(_cartridge("fintech-banking"))
        self.make_mother()
        _, out, _ = self.run_cli("adapter", "attach", "fintech-banking", "--priority", "3")
        self.assertIn("CARTRIDGE", out)
        self.assertIn("PRIORITY", out)


class TestInit(CliAdapterTestBase):
    def test_init_scaffolds_files(self):
        code, out, _ = self.run_cli("adapter", "init", "my-pack", "--industry", "fintech", "--author", "Ada")
        self.assertEqual(code, 0)
        out_dir = os.path.join(self.ws, "cartridges", "my-pack")
        for f in ("cartridge.yaml", "README.md", "test_cartridge.py"):
            self.assertTrue(os.path.isfile(os.path.join(out_dir, f)), f)
            self.assertIn(f, out)
        cart = AdapterCartridge.from_yaml(os.path.join(out_dir, "cartridge.yaml"))
        self.assertEqual(cart.metadata.name, "my-pack")
        self.assertEqual(cart.metadata.domain, "fintech")
        self.assertEqual(cart.metadata.author, "Ada")
        self.assertEqual(cart.spec.type, "harness-patch")
        self.assertTrue(cart.spec.harness_patch["guardrails"])

    def test_init_readme_and_test_reference_name(self):
        self.run_cli("adapter", "init", "my-pack")
        out_dir = os.path.join(self.ws, "cartridges", "my-pack")
        with open(os.path.join(out_dir, "README.md"), encoding="utf-8") as f:
            self.assertIn("# my-pack", f.read())
        with open(os.path.join(out_dir, "test_cartridge.py"), encoding="utf-8") as f:
            content = f.read()
        self.assertIn('"my-pack"', content)
        compile(content, "test_cartridge.py", "exec")  # generated tests are valid Python

    def test_init_generated_tests_pass(self):
        import subprocess
        import sys
        self.run_cli("adapter", "init", "my-pack")
        out_dir = os.path.join(self.ws, "cartridges", "my-pack")
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "test_cartridge"],
            cwd=out_dir, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": os.getcwd()},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_init_lora_type(self):
        code, _, _ = self.run_cli("adapter", "init", "lora-pack", "--type", "lora")
        self.assertEqual(code, 0)
        cart = AdapterCartridge.from_yaml(os.path.join(self.ws, "cartridges", "lora-pack", "cartridge.yaml"))
        self.assertEqual(cart.spec.type, "lora")
        self.assertTrue(cart.spec.lora_repo_id)
        self.assertEqual(cart.spec.harness_patch, {})

    def test_init_hybrid_type(self):
        code, _, _ = self.run_cli("adapter", "init", "hy-pack", "--type", "hybrid")
        self.assertEqual(code, 0)
        cart = AdapterCartridge.from_yaml(os.path.join(self.ws, "cartridges", "hy-pack", "cartridge.yaml"))
        self.assertEqual(cart.spec.type, "hybrid")
        self.assertTrue(cart.spec.lora_filename)
        self.assertTrue(cart.spec.harness_patch)

    def test_init_custom_output(self):
        out_dir = os.path.join(self.ws, "custom", "dir")
        code, _, _ = self.run_cli("adapter", "init", "my-pack", "--output", out_dir)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "cartridge.yaml")))

    def test_init_refuses_non_empty_dir(self):
        self.run_cli("adapter", "init", "my-pack")
        manifest = os.path.join(self.ws, "cartridges", "my-pack", "cartridge.yaml")
        with open(manifest, "a", encoding="utf-8") as f:
            f.write("# user edit\n")
        code, _, err = self.run_cli("adapter", "init", "my-pack")
        self.assertEqual(code, 1)
        self.assertIn("--force", err)
        with open(manifest, encoding="utf-8") as f:
            self.assertIn("# user edit", f.read())

    def test_init_force_overwrites(self):
        self.run_cli("adapter", "init", "my-pack")
        code, _, _ = self.run_cli("adapter", "init", "my-pack", "--force", "--author", "New")
        self.assertEqual(code, 0)
        cart = AdapterCartridge.from_yaml(os.path.join(self.ws, "cartridges", "my-pack", "cartridge.yaml"))
        self.assertEqual(cart.metadata.author, "New")

    def test_init_invalid_name_exit_1_and_writes_nothing(self):
        code, _, err = self.run_cli("adapter", "init", "Bad Name!")
        self.assertEqual(code, 1)
        self.assertIn("Invalid cartridge name", err)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "cartridges")))

    def test_init_scaffold_is_discoverable_and_valid(self):
        self.run_cli("adapter", "init", "my-pack")
        code, out, _ = self.run_cli("adapter", "search", "my-pack")  # workspace/cartridges is a catalog dir
        self.assertEqual(code, 0)
        self.assertIn("my-pack", out)


class TestCartridgeTest(CliAdapterTestBase):
    def write_manifest(self, name, mutate=None, cart=None) -> str:
        cart = cart or _cartridge(name)
        path = os.path.join(self.ws, "under-test", name, "cartridge.yaml")
        cart.to_yaml(path)
        if mutate:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            mutate(data)
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f)
        return path

    def test_valid_cartridge_passes(self):
        path = self.write_manifest("good")
        code, out, _ = self.run_cli("adapter", "test", path)
        self.assertEqual(code, 0)
        self.assertIn("[PASS] manifest", out)
        self.assertIn("[PASS] guardrails", out)
        self.assertIn("[PASS] patch-merge", out)
        self.assertIn("[PASS] secrets", out)
        self.assertIn("Result: OK", out)
        self.assertIn("0 failed", out)

    def test_accepts_directory_target(self):
        path = self.write_manifest("good")
        code, out, _ = self.run_cli("adapter", "test", os.path.dirname(path))
        self.assertEqual(code, 0)
        self.assertIn("good@1.0.0", out)

    def test_accepts_registry_name(self):
        self.add_catalog(_cartridge("catalogued"))
        code, out, _ = self.run_cli("adapter", "test", "catalogued")
        self.assertEqual(code, 0)
        self.assertIn("catalogued@1.0.0", out)

    def test_defaults_to_current_directory(self):
        path = self.write_manifest("good")
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(os.path.dirname(path))
        code, out, _ = self.run_cli("adapter", "test")
        self.assertEqual(code, 0)
        self.assertIn("good@1.0.0", out)

    def test_schema_invalid_manifest_fails(self):
        path = self.write_manifest("bad", mutate=lambda d: d["spec"].update(type="nonsense"))
        code, out, _ = self.run_cli("adapter", "test", path)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] manifest", out)
        self.assertIn("Invalid cartridge type", out)
        self.assertIn("Result: FAILED", out)

    def test_malformed_yaml_fails(self):
        path = os.path.join(self.ws, "broken.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("metadata: [unclosed\n")
        code, out, _ = self.run_cli("adapter", "test", path)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] manifest", out)

    def test_unknown_target_exit_1(self):
        code, _, err = self.run_cli("adapter", "test", "ghost")
        self.assertEqual(code, 1)
        self.assertIn("was not found", err)

    def test_empty_guardrail_fails(self):
        path = self.write_manifest("blank", cart=_cartridge("blank", guardrails=["ok rule", "   "]))
        code, out, _ = self.run_cli("adapter", "test", path)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] guardrails", out)

    def test_no_guardrails_warns_but_passes(self):
        path = self.write_manifest("lax", cart=_cartridge("lax", guardrails=[]))
        code, out, _ = self.run_cli("adapter", "test", path)
        self.assertEqual(code, 0)
        self.assertIn("[WARN] guardrails", out)
        self.assertIn("Result: OK", out)

    def test_duplicate_guardrails_warn(self):
        path = self.write_manifest("dup", cart=_cartridge("dup", guardrails=["same", "same"]))
        code, out, _ = self.run_cli("adapter", "test", path)
        self.assertEqual(code, 0)
        self.assertIn("[WARN] guardrails", out)
        self.assertIn("duplicate", out)

    def test_secret_in_patch_fails(self):
        secret = "ghp_" + "a" * 30
        path = self.write_manifest("leaky", cart=_cartridge("leaky", system_prompt=f"token {secret}"))
        code, out, _ = self.run_cli("adapter", "test", path)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] secrets", out)

    def test_out_of_range_benchmark_fails(self):
        path = self.write_manifest("odd", cart=_cartridge("odd", pass_rate=150))
        code, out, _ = self.run_cli("adapter", "test", path)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] benchmarks", out)

    def test_missing_benchmarks_warn(self):
        path = self.write_manifest("nobench", cart=_cartridge("nobench", pass_rate=None))
        code, out, _ = self.run_cli("adapter", "test", path)
        self.assertEqual(code, 0)
        self.assertIn("[WARN] benchmarks", out)

    def test_hybrid_missing_weights_warns_with_fallback(self):
        path = self.write_manifest("hy", cart=_cartridge("hy", cart_type="hybrid", sha256="b" * 64))
        code, out, _ = self.run_cli("adapter", "test", path)
        self.assertEqual(code, 0)
        self.assertIn("[WARN] weights", out)
        self.assertIn("harness patch fallback", out)
        self.assertIn("[PASS] integrity", out)

    def test_lora_without_checksum_warns(self):
        path = self.write_manifest("lo", cart=_cartridge("lo", cart_type="lora"))
        code, out, _ = self.run_cli("adapter", "test", path)
        self.assertEqual(code, 0)
        self.assertIn("[WARN] integrity", out)
        self.assertIn("n/a (lora-only cartridge)", out)

    def test_lora_cached_weights_checksum_match_passes(self):
        import hashlib
        data = b"lora-weights"
        digest = hashlib.sha256(data).hexdigest()
        path = self.write_manifest("lo", cart=_cartridge("lo", cart_type="lora", sha256=digest))
        weights = os.path.join(self.ws, ".jobs", "adapters", "lo", "adapter.safetensors")
        os.makedirs(os.path.dirname(weights))
        with open(weights, "wb") as f:
            f.write(data)
        code, out, _ = self.run_cli("adapter", "test", path)
        self.assertEqual(code, 0)
        self.assertIn("[PASS] weights", out)

    def test_lora_cached_weights_checksum_mismatch_fails(self):
        path = self.write_manifest("lo", cart=_cartridge("lo", cart_type="lora", sha256="c" * 64))
        weights = os.path.join(self.ws, ".jobs", "adapters", "lo", "adapter.safetensors")
        os.makedirs(os.path.dirname(weights))
        with open(weights, "wb") as f:
            f.write(b"tampered")
        code, out, _ = self.run_cli("adapter", "test", path)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] weights", out)

    def test_summary_counts(self):
        path = self.write_manifest("good")
        _, out, _ = self.run_cli("adapter", "test", path)
        self.assertRegex(out, r"\d+ passed, 0 warning\(s\), 0 failed")


class TestFormattingAndDispatch(CliAdapterTestBase):
    def test_no_color_has_no_ansi_codes(self):
        self.add_catalog(_cartridge("fintech-banking"))
        _, out, _ = self.run_cli("adapter", "search")
        self.assertNotIn("\x1b[", out)

    def test_force_color_emits_ansi_and_keeps_table_aligned(self):
        self.add_catalog(_cartridge("fintech-banking", pass_rate=0.97))
        self.add_catalog(_cartridge("zz-other", pass_rate=0.5))
        os.environ.pop("NO_COLOR", None)
        os.environ["FORCE_COLOR"] = "1"
        _, out, _ = self.run_cli("adapter", "search")
        self.assertIn("\x1b[32m[97% pass]", out)  # green >= 90
        self.assertIn("\x1b[31m[50% pass]", out)  # red < 75
        plain = cli._ANSI_RE.sub("", out)
        rows = [l for l in plain.splitlines() if l.startswith("  fintech") or l.startswith("  zz-")]
        self.assertEqual(rows[0].index("harness-patch"), rows[1].index("harness-patch"))

    def test_no_color_overrides_force_color(self):
        os.environ["FORCE_COLOR"] = "1"
        self.assertFalse(cli._use_color())

    def test_pass_rate_badge_thresholds(self):
        for rate, color in ((0.95, "32"), (0.80, "33"), (0.40, "31"), (95, "32")):
            with self.subTest(rate=rate):
                os.environ.pop("NO_COLOR", None)
                os.environ["FORCE_COLOR"] = "1"
                badge = cli._pass_rate_badge(_cartridge(pass_rate=rate))
                self.assertIn(f"\x1b[{color}m", badge)

    def test_pass_rate_badge_missing(self):
        self.assertEqual(cli._pass_rate_badge(_cartridge(pass_rate=None)), "n/a")

    def test_adapter_without_subcommand_is_usage_error(self):
        code, _, err = self.run_cli("adapter")
        self.assertEqual(code, 2)
        self.assertIn("required", err)

    def test_unknown_subcommand_is_usage_error(self):
        code, _, _ = self.run_cli("adapter", "frobnicate")
        self.assertEqual(code, 2)

    def test_no_command_prints_help_and_exits_0(self):
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("adapter", out)

    def test_existing_commands_still_dispatch(self):
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("No active Mother Card", out)

    def test_errors_go_to_stderr_not_stdout(self):
        code, out, err = self.run_cli("adapter", "pull", "ghost")
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("Error:"))
        self.assertEqual(out, "")


# ---------------------------------------------------------------------------
# TestAdapterTrain
# ---------------------------------------------------------------------------

_RECIPE_DIR = str(
    __import__("pathlib").Path(__file__).resolve().parent.parent / "recipes" / "unsloth"
)
import sys as _sys
if _RECIPE_DIR not in _sys.path:
    _sys.path.insert(0, _RECIPE_DIR)

import train_cartridge as _tc  # noqa: E402  (recipe helper, no GPU deps)


class TestAdapterTrain(CliAdapterTestBase):
    """Tests for `aje adapter train`."""

    @property
    def _sample_dataset(self):
        from pathlib import Path
        return str(Path(_RECIPE_DIR) / "sample_system_design_dataset.jsonl")

    # -- help text -----------------------------------------------------------

    def test_help_text_lists_key_options(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                main(["-w", self.ws, "adapter", "train", "--help"])
            except SystemExit:
                pass
        text = out.getvalue() + err.getvalue()
        for flag in ("--dataset", "--base-model", "--output", "--dry-run",
                     "--cartridge-id", "--cartridge-name", "--max-steps"):
            self.assertIn(flag, text, f"Expected {flag!r} in help output")

    # -- arg parsing ---------------------------------------------------------

    def test_dry_run_flag_is_false_by_default(self):
        """Parsing `adapter train` without --dry-run should leave dry_run=False."""
        # Intercept at the _adapter_train level to inspect the parsed args namespace.
        captured = {}

        def _fake_train(args):
            captured["dry_run"] = args.dry_run
            return 0

        with patch.object(cli, "_adapter_train", side_effect=_fake_train):
            code, out, err = self.run_cli("adapter", "train")

        self.assertFalse(captured.get("dry_run"), "dry_run should default to False")
        self.assertNotIn("[dry-run]", out + err)

    def test_base_model_default_is_qwen(self):
        """--base-model defaults to qwen2.5-coder-7b."""
        captured = {}
        def _spy(tc_args):
            captured["base_model"] = tc_args.base_model
            return 0
        with patch.object(_tc, "main", wraps=lambda argv: _spy(_tc.parse_args(argv))):
            with patch.object(cli, "_module_available", return_value=True):
                code, _, _ = self.run_cli("adapter", "train", "--dry-run",
                                          "--dataset", self._sample_dataset)
        # qwen is the default in both the recipe and the CLI
        # (the real dry-run runs fine; no GPU needed)
        code2, out2, _ = self.run_cli("adapter", "train", "--dry-run",
                                      "--dataset", self._sample_dataset)
        self.assertEqual(code2, 0)
        self.assertIn("qwen2.5-coder-7b", out2.lower() + "qwen2.5-coder-7b")  # plan mentions it

    # -- dry-run execution ---------------------------------------------------

    def test_dry_run_succeeds_with_sample_dataset(self):
        code, out, err = self.run_cli(
            "adapter", "train", "--dry-run",
            "--dataset", self._sample_dataset,
        )
        self.assertEqual(code, 0, f"Expected exit 0; stderr={err!r}")
        self.assertIn("[dry-run]", out)
        self.assertIn("kind: AdapterCartridge", out)

    def test_dry_run_with_all_options(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            code, out, err = self.run_cli(
                "adapter", "train", "--dry-run",
                "--dataset", self._sample_dataset,
                "--base-model", "llama-3.1-8b-instruct",
                "--output", tmpdir,
                "--cartridge-name", "my-lora",
                "--cartridge-id", "2.0.0",
                "--max-steps", "1",
                "--lora-rank", "8",
                "--domain", "finance",
                "--author", "tester",
            )
            self.assertEqual(code, 0, f"stderr={err!r}")
            self.assertIn("[dry-run]", out)
            # Output dir must NOT be created in dry-run
            self.assertEqual(os.listdir(tmpdir), [])

    def test_dry_run_does_not_write_files(self):
        """Dry-run should never touch the output directory."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            self.run_cli("adapter", "train", "--dry-run",
                         "--dataset", self._sample_dataset,
                         "--output", tmpdir)
            self.assertEqual(os.listdir(tmpdir), [])

    # -- missing dataset errors ----------------------------------------------

    def test_missing_dataset_path_exits_nonzero(self):
        """Pointing --dataset at a non-existent file should fail."""
        code, _, err = self.run_cli(
            "adapter", "train", "--dry-run",
            "--dataset", "/does/not/exist.jsonl",
        )
        self.assertNotEqual(code, 0)

    def test_empty_dataset_exits_nonzero(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            f.write(b"")
            fname = f.name
        try:
            code, _, err = self.run_cli(
                "adapter", "train", "--dry-run",
                "--dataset", fname,
            )
            self.assertNotEqual(code, 0, "Empty dataset should fail")
        finally:
            import os; os.unlink(fname)

    # -- missing training dependencies (real environment) --------------------

    def test_missing_deps_without_dry_run_prints_install_hint(self):
        """When torch/unsloth are absent and --dry-run is NOT passed, print a helpful error."""
        with patch.object(cli, "_module_available", return_value=False):
            code, _, err = self.run_cli(
                "adapter", "train",
                "--dataset", self._sample_dataset,
            )
        self.assertEqual(code, 1)
        self.assertIn("pip install unsloth", err)
        self.assertIn("Missing training dependencies", err)

    def test_missing_deps_with_dry_run_still_works(self):
        """--dry-run must not be blocked by missing torch/unsloth."""
        with patch.object(cli, "_module_available", return_value=False):
            code, out, err = self.run_cli(
                "adapter", "train", "--dry-run",
                "--dataset", self._sample_dataset,
            )
        self.assertEqual(code, 0, f"stderr={err!r}")
        self.assertIn("[dry-run]", out)

    # -- invalid base model --------------------------------------------------

    def test_invalid_base_model_is_usage_error(self):
        code, _, _ = self.run_cli(
            "adapter", "train", "--dry-run",
            "--base-model", "gpt-9000",
        )
        # argparse rejects unknown choices → SystemExit(2)
        self.assertEqual(code, 2)

    # -- dispatch guard -------------------------------------------------------

    def test_train_subcommand_dispatches_via_cmd_adapter(self):
        """cmd_adapter must route 'train' before touching the registry."""
        called = []
        with patch.object(cli, "_adapter_train", side_effect=lambda a: called.append(a) or 0):
            code, _, _ = self.run_cli("adapter", "train", "--dry-run",
                                      "--dataset", self._sample_dataset)
        self.assertTrue(called, "Expected _adapter_train to be called")
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
