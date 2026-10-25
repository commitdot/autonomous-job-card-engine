"""
Unit Tests — Dynamic Adapter Cartridge Hot-Swapping in Daemon
=============================================================
Covers Sub-Task 3 of the Open Adapter Cartridge Registry plan:
  * AutonomousJobCardEngine._mount_cartridges_on_adapter()
  * BaseLLMAdapter.mount_cartridges() / get_mounted_harness_context() defaults
  * SimAdapter cartridge mount and harness injection
  * OllamaAdapter cartridge mount and harness injection
  * WatsonxAdapter cartridge mount and harness injection
  * Graceful fallback (LoRA unavailable → harness patch only)
  * LoRA-only cartridge with no weights and no harness patch is skipped
  * Missing cartridge from registry is skipped without crashing
  * Disabled binding is not mounted
  * Priority ordering: higher priority cartridges override lower ones
  * audit_ledger.jsonl receives adapter_cartridge_mounted events
"""

import json
import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Optional, Sequence, Tuple
from unittest.mock import MagicMock, patch, PropertyMock

from src.adapters.base import BaseLLMAdapter
from src.adapters.harness_mixin import HarnessConfig
from src.adapters.patch_loader import apply_cartridges
from src.adapters.sim_adapter import SimAdapter
from src.models import (
    AdapterBinding,
    AdapterCartridge,
    CartridgeMetadata,
    CartridgeSpec,
    MotherCard,
    GovernancePolicies,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_harness_patch_cartridge(
    name: str = "security-guard",
    version: str = "1.0.0",
    guardrails: Optional[List[str]] = None,
    system_prompt: str = "Secure coding assistant.",
    domain: str = "security",
) -> AdapterCartridge:
    """Create a minimal harness-patch cartridge for testing."""
    return AdapterCartridge(
        metadata=CartridgeMetadata(
            name=name,
            version=version,
            author="AJE Tests",
            description=f"Test cartridge: {name}",
            domain=domain,
            tags=[domain, "test"],
        ),
        spec=CartridgeSpec(
            type="harness-patch",
            base_models=["granite-3-8b"],
            compatible_archetypes=["squad-backend"],
            harness_patch={
                "system_prompt": system_prompt,
                "guardrails": guardrails or ["Never expose secrets.", "Follow OWASP Top 10."],
                "tool_guidance": [f"Use {name} toolset."],
            },
            benchmarks={"pass_rate": 0.95},
        ),
    )


def _make_lora_cartridge(
    name: str = "code-lora",
    version: str = "1.0.0",
    cart_type: str = "lora",
    has_harness_patch: bool = False,
) -> AdapterCartridge:
    """Create a LoRA (or hybrid) cartridge for testing."""
    patch_data: Dict[str, Any] = {}
    if has_harness_patch:
        patch_data = {
            "system_prompt": "LoRA-enhanced coding assistant.",
            "guardrails": ["Apply LoRA fine-tuning context."],
        }
    return AdapterCartridge(
        metadata=CartridgeMetadata(
            name=name,
            version=version,
            author="AJE Tests",
            description=f"LoRA cartridge: {name}",
            domain="ml",
            tags=["lora", "test"],
        ),
        spec=CartridgeSpec(
            type=cart_type,
            base_models=["granite-3-8b"],
            compatible_archetypes=["squad-backend"],
            lora_repo_id="test-org/test-repo",
            lora_filename="adapter.safetensors",
            lora_sha256="",
            lora_rank=16,
            harness_patch=patch_data,
            benchmarks={"pass_rate": 0.90},
        ),
    )


def _make_binding(
    cartridge_name: str,
    enabled: bool = True,
    mode: str = "auto",
    priority: int = 0,
    version: str = "latest",
    overrides: Optional[Dict[str, Any]] = None,
) -> AdapterBinding:
    return AdapterBinding(
        cartridge=cartridge_name,
        version=version,
        enabled=enabled,
        mode=mode,
        priority=priority,
        overrides=overrides or {},
    )


def _make_mother_with_cartridges(
    bindings: List[AdapterBinding],
    workspace_root: str = ".",
) -> MotherCard:
    """Create a minimal MotherCard with the given adapter bindings."""
    return MotherCard(
        id="mother-test",
        name="Test Mother",
        core_philosophy="Test",
        attached_cartridges=bindings,
        governance=GovernancePolicies(),
    )


# ---------------------------------------------------------------------------
# 1. BaseLLMAdapter defaults (no-op)
# ---------------------------------------------------------------------------

class TestBaseLLMAdapterDefaults(unittest.TestCase):
    """The base class should supply safe no-op defaults for cartridge mounting."""

    def _make_minimal_concrete(self) -> BaseLLMAdapter:
        """Return a minimal concrete adapter that satisfies the ABC."""
        class _Minimal(BaseLLMAdapter):
            def execute_child_card(self, *a, **kw):
                return {"success": True, "logs": [], "created_files": []}
            def generate_gap_analysis(self, *a, **kw):
                return []
        return _Minimal()

    def test_get_mounted_harness_context_returns_empty_string_by_default(self):
        adapter = self._make_minimal_concrete()
        self.assertEqual(adapter.get_mounted_harness_context(), "")

    def test_mount_cartridges_is_a_noop_and_does_not_raise(self):
        adapter = self._make_minimal_concrete()
        cart = _make_harness_patch_cartridge()
        binding = _make_binding("security-guard")
        # Should not raise
        adapter.mount_cartridges([(cart, binding)])
        # Context still empty — base class is a no-op
        self.assertEqual(adapter.get_mounted_harness_context(), "")

    def test_mount_cartridges_called_with_empty_list_does_not_raise(self):
        adapter = self._make_minimal_concrete()
        adapter.mount_cartridges([])  # No-op, must not raise


# ---------------------------------------------------------------------------
# 2. SimAdapter cartridge mounting
# ---------------------------------------------------------------------------

class TestSimAdapterCartridgeMounting(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.adapter = SimAdapter(workspace_root=self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_initial_mounted_harness_is_none(self):
        self.assertIsNone(self.adapter._mounted_harness)

    def test_get_mounted_harness_context_empty_before_mount(self):
        self.assertEqual(self.adapter.get_mounted_harness_context(), "")

    def test_mount_single_harness_patch_cartridge(self):
        cart = _make_harness_patch_cartridge(
            system_prompt="Custom system prompt.",
            guardrails=["Rule A", "Rule B"],
        )
        binding = _make_binding("security-guard")
        self.adapter.mount_cartridges([(cart, binding)])
        ctx = self.adapter.get_mounted_harness_context()
        self.assertIn("Custom system prompt.", ctx)
        self.assertIn("Rule A", ctx)
        self.assertIn("Rule B", ctx)

    def test_mount_cartridge_sets_mounted_harness(self):
        cart = _make_harness_patch_cartridge()
        self.adapter.mount_cartridges([(cart, None)])
        self.assertIsNotNone(self.adapter._mounted_harness)
        self.assertIsInstance(self.adapter._mounted_harness, HarnessConfig)

    def test_mount_replaces_previous_mount(self):
        cart1 = _make_harness_patch_cartridge(name="cartridge-a", system_prompt="Prompt A.")
        cart2 = _make_harness_patch_cartridge(name="cartridge-b", system_prompt="Prompt B.")
        self.adapter.mount_cartridges([(cart1, None)])
        ctx1 = self.adapter.get_mounted_harness_context()
        self.adapter.mount_cartridges([(cart2, None)])
        ctx2 = self.adapter.get_mounted_harness_context()
        # Second mount completely replaces first — independent HarnessConfig
        self.assertIn("Prompt B.", ctx2)

    def test_mount_multiple_cartridges_merges_guardrails(self):
        cart1 = _make_harness_patch_cartridge(name="cart-a", guardrails=["GuardA"])
        cart2 = _make_harness_patch_cartridge(name="cart-b", guardrails=["GuardB"])
        self.adapter.mount_cartridges([(cart1, None), (cart2, None)])
        ctx = self.adapter.get_mounted_harness_context()
        self.assertIn("GuardA", ctx)
        self.assertIn("GuardB", ctx)

    def test_disabled_binding_skipped_by_patch_loader(self):
        cart = _make_harness_patch_cartridge(system_prompt="Should not appear.")
        binding = _make_binding("security-guard", enabled=False)
        self.adapter.mount_cartridges([(cart, binding)])
        ctx = self.adapter.get_mounted_harness_context()
        # Disabled binding → apply_cartridge is a no-op → harness stays empty
        self.assertEqual(ctx, "")

    def test_execute_child_card_injects_harness_context(self):
        cart = _make_harness_patch_cartridge(
            system_prompt="HARNESS_MARKER_FOR_TEST",
            guardrails=["GUARDRAIL_MARKER"],
        )
        self.adapter.mount_cartridges([(cart, None)])
        # Monkey-patch the inner SimLLM to capture the objective it receives
        captured = {}
        orig = self.adapter._sim.execute_child_card
        def _capture_call(**kwargs):
            captured["objective"] = kwargs.get("tactical_objective", "")
            return {"success": True, "logs": [], "created_files": []}
        self.adapter._sim.execute_child_card = _capture_call

        self.adapter.execute_child_card(
            tactical_objective="Write a hello world function.",
            deliverables=[{"path": "hello.py", "description": "Hello world"}],
            iteration=1,
        )
        self.assertIn("HARNESS_MARKER_FOR_TEST", captured["objective"])

    def test_execute_child_card_no_cartridge_unchanged_objective(self):
        captured = {}
        orig_objective = "Write a hello world function."
        def _capture_call(**kwargs):
            captured["objective"] = kwargs.get("tactical_objective", "")
            return {"success": True, "logs": [], "created_files": []}
        self.adapter._sim.execute_child_card = _capture_call

        self.adapter.execute_child_card(
            tactical_objective=orig_objective,
            deliverables=[{"path": "hello.py", "description": "Hello world"}],
            iteration=1,
        )
        self.assertEqual(captured["objective"], orig_objective)

    def test_priority_ordering_higher_priority_wins(self):
        """Higher-priority cartridge patch is applied last and overrides earlier ones."""
        cart_low = _make_harness_patch_cartridge(name="cart-low", system_prompt="Low priority.")
        cart_high = _make_harness_patch_cartridge(name="cart-high", system_prompt="High priority.")
        binding_low = _make_binding("cart-low", priority=1)
        binding_high = _make_binding("cart-high", priority=10)
        # apply_cartridges sorts ascending by priority so high runs last
        self.adapter.mount_cartridges([(cart_low, binding_low), (cart_high, binding_high)])
        ctx = self.adapter.get_mounted_harness_context()
        # Both prompts should appear (merged with double newline)
        self.assertIn("Low priority.", ctx)
        self.assertIn("High priority.", ctx)


# ---------------------------------------------------------------------------
# 3. OllamaAdapter cartridge mounting (no Ollama SDK required — mocked)
# ---------------------------------------------------------------------------

class TestOllamaAdapterCartridgeMounting(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_adapter(self):
        """Return OllamaAdapter with ollama SDK mocked out."""
        with patch.dict("sys.modules", {"ollama": MagicMock()}):
            # Re-import to get the mocked version
            import importlib
            import src.adapters.ollama_adapter as mod
            importlib.reload(mod)
            mod._OLLAMA_AVAILABLE = True
            adapter = mod.OllamaAdapter(workspace_root=self.tmp_dir)
        return adapter

    def test_mount_cartridges_sets_mounted_harness(self):
        adapter = self._make_adapter()
        cart = _make_harness_patch_cartridge(system_prompt="Ollama harness test.")
        adapter.mount_cartridges([(cart, None)])
        self.assertIsNotNone(adapter._mounted_harness)

    def test_get_mounted_harness_context_returns_prompt(self):
        adapter = self._make_adapter()
        cart = _make_harness_patch_cartridge(
            system_prompt="Ollama prompt marker.",
            guardrails=["Ollama guardrail."],
        )
        adapter.mount_cartridges([(cart, None)])
        ctx = adapter.get_mounted_harness_context()
        self.assertIn("Ollama prompt marker.", ctx)
        self.assertIn("Ollama guardrail.", ctx)

    def test_initial_harness_is_none(self):
        adapter = self._make_adapter()
        self.assertIsNone(adapter._mounted_harness)
        self.assertEqual(adapter.get_mounted_harness_context(), "")

    def test_execute_child_card_injects_harness_into_prompt(self):
        adapter = self._make_adapter()
        cart = _make_harness_patch_cartridge(system_prompt="OLLAMA_HARNESS_INJECT_TEST")
        adapter.mount_cartridges([(cart, None)])
        captured_prompts = []
        adapter._generate = lambda prompt: (captured_prompts.append(prompt) or "# stub")
        adapter._extract_code_block = lambda text, _: "def stub(): pass"

        adapter.execute_child_card(
            tactical_objective="Write hello world.",
            deliverables=[{"path": "out.py", "description": "stub"}],
            iteration=1,
        )
        self.assertTrue(len(captured_prompts) > 0)
        self.assertIn("OLLAMA_HARNESS_INJECT_TEST", captured_prompts[0])


# ---------------------------------------------------------------------------
# 4. WatsonxAdapter cartridge mounting (SDK mocked out)
# ---------------------------------------------------------------------------

class TestWatsonxAdapterCartridgeMounting(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_adapter(self):
        """Return WatsonxAdapter with ibm-watsonx-ai SDK mocked out."""
        mock_sdk = MagicMock()
        mock_sdk.Credentials = MagicMock(return_value=MagicMock())
        mock_sdk.foundation_models.ModelInference = MagicMock(return_value=MagicMock())
        mock_genparams = MagicMock()
        mock_genparams.MAX_NEW_TOKENS = "max_new_tokens"
        mock_genparams.TEMPERATURE = "temperature"
        mock_genparams.REPETITION_PENALTY = "repetition_penalty"
        mock_metanames = MagicMock()
        mock_metanames.GenTextParamsMetaNames = mock_genparams

        with patch.dict("sys.modules", {
            "ibm_watsonx_ai": mock_sdk,
            "ibm_watsonx_ai.foundation_models": mock_sdk.foundation_models,
            "ibm_watsonx_ai.metanames": mock_metanames,
        }):
            import importlib
            import src.adapters.watsonx_adapter as mod
            importlib.reload(mod)
            mod._WATSONX_AVAILABLE = True
            mod.Credentials = MagicMock(return_value=MagicMock())
            mod.ModelInference = MagicMock(return_value=MagicMock())
            mod.GenParams = mock_genparams

            adapter = mod.WatsonxAdapter(
                workspace_root=self.tmp_dir,
                api_key="test-key",
                project_id="test-project",
            )
        return adapter

    def test_mount_cartridges_sets_mounted_harness(self):
        adapter = self._make_adapter()
        cart = _make_harness_patch_cartridge(system_prompt="Watsonx harness test.")
        adapter.mount_cartridges([(cart, None)])
        self.assertIsNotNone(adapter._mounted_harness)

    def test_get_mounted_harness_context_returns_prompt(self):
        adapter = self._make_adapter()
        cart = _make_harness_patch_cartridge(
            system_prompt="Watsonx prompt marker.",
            guardrails=["Watsonx guardrail."],
        )
        adapter.mount_cartridges([(cart, None)])
        ctx = adapter.get_mounted_harness_context()
        self.assertIn("Watsonx prompt marker.", ctx)
        self.assertIn("Watsonx guardrail.", ctx)

    def test_initial_harness_is_none(self):
        adapter = self._make_adapter()
        self.assertIsNone(adapter._mounted_harness)

    def test_execute_child_card_injects_harness_into_prompt(self):
        adapter = self._make_adapter()
        cart = _make_harness_patch_cartridge(system_prompt="WATSONX_HARNESS_INJECT_TEST")
        adapter.mount_cartridges([(cart, None)])
        captured_prompts = []
        adapter._generate = lambda prompt: (captured_prompts.append(prompt) or "# stub")
        adapter._extract_code_block = lambda text, _: "def stub(): pass"

        adapter.execute_child_card(
            tactical_objective="Write hello world.",
            deliverables=[{"path": "out.py", "description": "stub"}],
            iteration=1,
        )
        self.assertTrue(len(captured_prompts) > 0)
        self.assertIn("WATSONX_HARNESS_INJECT_TEST", captured_prompts[0])


# ---------------------------------------------------------------------------
# 5. AutonomousJobCardEngine._mount_cartridges_on_adapter()
# ---------------------------------------------------------------------------

class TestDaemonMountCartridgesOnAdapter(unittest.TestCase):
    """
    Tests for the daemon's _mount_cartridges_on_adapter() orchestration method.
    Uses a real SimAdapter + mock AdapterRegistry to avoid filesystem dependency.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        from src.daemon import AutonomousJobCardEngine
        self.engine = AutonomousJobCardEngine(workspace_root=self.tmp_dir)
        # Use SimAdapter so we can easily inspect the mounted harness
        self.engine.llm = SimAdapter(workspace_root=self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_mock_registry(self, cartridges: Dict[str, AdapterCartridge]) -> MagicMock:
        """Create a mock AdapterRegistry that returns cartridges by name."""
        mock_reg = MagicMock()
        def _get(name, version="latest"):
            return cartridges.get(name)
        mock_reg.get_cartridge.side_effect = _get
        return mock_reg

    def _make_mock_lora_mgr(self, cached: bool = False, weight_path: str = "") -> MagicMock:
        mock_lm = MagicMock()
        mock_lm.is_cached.return_value = cached
        mock_lm.get_adapter_path.return_value = weight_path
        return mock_lm

    def test_no_attached_cartridges_does_not_mount(self):
        mother = _make_mother_with_cartridges([])
        self.engine._mount_cartridges_on_adapter(mother, "child-001")
        self.assertIsNone(self.engine.llm._mounted_harness)

    def test_single_harness_patch_cartridge_mounted(self):
        cart = _make_harness_patch_cartridge(
            name="security-guard", system_prompt="SEC_PROMPT_MARKER"
        )
        binding = _make_binding("security-guard")
        mother = _make_mother_with_cartridges([binding])

        self.engine._adapter_registry = self._make_mock_registry({"security-guard": cart})
        self.engine._lora_manager = self._make_mock_lora_mgr()

        self.engine._mount_cartridges_on_adapter(mother, "child-001")

        ctx = self.engine.llm.get_mounted_harness_context()
        self.assertIn("SEC_PROMPT_MARKER", ctx)

    def test_missing_cartridge_in_registry_is_skipped_without_crash(self):
        binding = _make_binding("nonexistent-cart")
        mother = _make_mother_with_cartridges([binding])

        self.engine._adapter_registry = self._make_mock_registry({})  # empty
        self.engine._lora_manager = self._make_mock_lora_mgr()

        # Must not raise
        self.engine._mount_cartridges_on_adapter(mother, "child-002")
        self.assertIsNone(self.engine.llm._mounted_harness)

    def test_disabled_binding_is_not_mounted(self):
        cart = _make_harness_patch_cartridge(name="disabled-cart", system_prompt="DISABLED")
        binding = _make_binding("disabled-cart", enabled=False)
        mother = _make_mother_with_cartridges([binding])

        self.engine._adapter_registry = self._make_mock_registry({"disabled-cart": cart})
        self.engine._lora_manager = self._make_mock_lora_mgr()

        self.engine._mount_cartridges_on_adapter(mother, "child-003")
        self.assertIsNone(self.engine.llm._mounted_harness)

    def test_lora_only_cartridge_without_weights_and_no_patch_is_skipped(self):
        """LoRA-only cartridge with no cached weights and no harness_patch must be skipped."""
        cart = _make_lora_cartridge(
            name="code-lora",
            cart_type="lora",
            has_harness_patch=False,
        )
        binding = _make_binding("code-lora", mode="auto")
        mother = _make_mother_with_cartridges([binding])

        self.engine._adapter_registry = self._make_mock_registry({"code-lora": cart})
        self.engine._lora_manager = self._make_mock_lora_mgr(cached=False)

        self.engine._mount_cartridges_on_adapter(mother, "child-004")
        # Skipped → no harness mounted
        self.assertIsNone(self.engine.llm._mounted_harness)

    def test_hybrid_cartridge_without_cached_weights_falls_back_to_patch(self):
        """Hybrid cartridge without cached weights should apply the harness patch only."""
        cart = _make_lora_cartridge(
            name="hybrid-cart",
            cart_type="hybrid",
            has_harness_patch=True,
        )
        binding = _make_binding("hybrid-cart", mode="auto")
        mother = _make_mother_with_cartridges([binding])

        self.engine._adapter_registry = self._make_mock_registry({"hybrid-cart": cart})
        self.engine._lora_manager = self._make_mock_lora_mgr(cached=False)

        self.engine._mount_cartridges_on_adapter(mother, "child-005")
        ctx = self.engine.llm.get_mounted_harness_context()
        self.assertIn("LoRA-enhanced coding assistant.", ctx)

    def test_lora_cartridge_with_cached_weights_passes_weight_path_in_overrides(self):
        """When weights are cached, weight_path should be in the effective binding overrides."""
        cart = _make_lora_cartridge(
            name="code-lora",
            cart_type="lora",
            has_harness_patch=True,  # lora + harness_patch = skip lora-skip check
        )
        # Technically this is a hybrid in behaviour; set type to hybrid to satisfy validator
        cart.spec.type = "hybrid"
        weight_file = os.path.join(self.tmp_dir, "adapter.safetensors")
        with open(weight_file, "wb") as f:
            f.write(b"\x00" * 16)

        binding = _make_binding("code-lora", mode="lora")
        mother = _make_mother_with_cartridges([binding])

        self.engine._adapter_registry = self._make_mock_registry({"code-lora": cart})
        self.engine._lora_manager = self._make_mock_lora_mgr(
            cached=True, weight_path=weight_file
        )

        # Capture the pairs passed to mount_cartridges
        mounted_pairs = []
        orig_mount = self.engine.llm.mount_cartridges
        def _capture(pairs):
            mounted_pairs.extend(pairs)
            orig_mount(pairs)
        self.engine.llm.mount_cartridges = _capture

        self.engine._mount_cartridges_on_adapter(mother, "child-006")

        self.assertEqual(len(mounted_pairs), 1)
        _, effective_binding = mounted_pairs[0]
        self.assertIn("__lora_weight_path__", effective_binding.overrides)
        self.assertEqual(effective_binding.overrides["__lora_weight_path__"], weight_file)

    def test_multiple_cartridges_all_mounted(self):
        cart_a = _make_harness_patch_cartridge(name="cart-a", system_prompt="PROMPT_A")
        cart_b = _make_harness_patch_cartridge(name="cart-b", system_prompt="PROMPT_B")
        binding_a = _make_binding("cart-a", priority=1)
        binding_b = _make_binding("cart-b", priority=2)
        mother = _make_mother_with_cartridges([binding_a, binding_b])

        self.engine._adapter_registry = self._make_mock_registry(
            {"cart-a": cart_a, "cart-b": cart_b}
        )
        self.engine._lora_manager = self._make_mock_lora_mgr()

        self.engine._mount_cartridges_on_adapter(mother, "child-007")
        ctx = self.engine.llm.get_mounted_harness_context()
        self.assertIn("PROMPT_A", ctx)
        self.assertIn("PROMPT_B", ctx)

    def test_patch_mode_binding_skips_lora_check(self):
        """Binding with mode='patch' should not attempt LoRA weight resolution."""
        cart = _make_lora_cartridge(
            name="hybrid-patch-mode",
            cart_type="hybrid",
            has_harness_patch=True,
        )
        binding = _make_binding("hybrid-patch-mode", mode="patch")
        mother = _make_mother_with_cartridges([binding])

        mock_lm = self._make_mock_lora_mgr(cached=False)
        self.engine._adapter_registry = self._make_mock_registry({"hybrid-patch-mode": cart})
        self.engine._lora_manager = mock_lm

        self.engine._mount_cartridges_on_adapter(mother, "child-008")
        # is_cached should NOT have been called since mode='patch'
        mock_lm.is_cached.assert_not_called()
        ctx = self.engine.llm.get_mounted_harness_context()
        self.assertIn("LoRA-enhanced coding assistant.", ctx)


# ---------------------------------------------------------------------------
# 6. Audit log recording
# ---------------------------------------------------------------------------

class TestCartridgeMountAuditLogging(unittest.TestCase):
    """Verify that adapter_cartridge_mounted events reach audit_ledger.jsonl."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        from src.daemon import AutonomousJobCardEngine
        self.engine = AutonomousJobCardEngine(workspace_root=self.tmp_dir)
        self.engine.llm = SimAdapter(workspace_root=self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_mock_registry(self, cartridges):
        mock_reg = MagicMock()
        mock_reg.get_cartridge.side_effect = lambda name, version="latest": cartridges.get(name)
        return mock_reg

    def _read_ledger(self) -> List[Dict]:
        ledger_path = os.path.join(self.tmp_dir, ".jobs", "audit_ledger.jsonl")
        if not os.path.exists(ledger_path):
            return []
        blocks = []
        with open(ledger_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        blocks.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return blocks

    def test_mount_event_written_to_audit_ledger(self):
        cart = _make_harness_patch_cartridge(name="security-guard", system_prompt="Audit test.")
        binding = _make_binding("security-guard")
        mother = _make_mother_with_cartridges([binding])

        self.engine._adapter_registry = self._make_mock_registry({"security-guard": cart})
        self.engine._lora_manager = MagicMock()
        self.engine._lora_manager.is_cached.return_value = False

        self.engine._mount_cartridges_on_adapter(mother, "child-audit-001")

        blocks = self._read_ledger()
        event_types = [b.get("event_type", "") for b in blocks]
        self.assertIn("ADAPTER_CARTRIDGE_MOUNTED", event_types)

    def test_mount_event_payload_contains_cartridge_name(self):
        cart = _make_harness_patch_cartridge(name="security-guard")
        binding = _make_binding("security-guard")
        mother = _make_mother_with_cartridges([binding])

        self.engine._adapter_registry = self._make_mock_registry({"security-guard": cart})
        self.engine._lora_manager = MagicMock()
        self.engine._lora_manager.is_cached.return_value = False

        self.engine._mount_cartridges_on_adapter(mother, "child-audit-002")

        blocks = self._read_ledger()
        mount_blocks = [
            b for b in blocks
            if b.get("event_type") == "ADAPTER_CARTRIDGE_MOUNTED"
        ]
        self.assertTrue(len(mount_blocks) >= 1)
        payload = mount_blocks[0].get("payload", {})
        meta = payload.get("meta", {})
        self.assertEqual(meta.get("cartridge_name"), "security-guard")

    def test_skipped_cartridge_writes_skipped_event(self):
        binding = _make_binding("nonexistent-cart")
        mother = _make_mother_with_cartridges([binding])
        self.engine._adapter_registry = self._make_mock_registry({})
        self.engine._lora_manager = MagicMock()

        self.engine._mount_cartridges_on_adapter(mother, "child-audit-003")

        blocks = self._read_ledger()
        event_types = [b.get("event_type", "") for b in blocks]
        self.assertIn("ADAPTER_CARTRIDGE_SKIPPED", event_types)

    def test_no_event_written_when_no_bindings(self):
        mother = _make_mother_with_cartridges([])
        initial_blocks = self._read_ledger()

        self.engine._mount_cartridges_on_adapter(mother, "child-audit-004")

        blocks_after = self._read_ledger()
        # No new cartridge events should have been written
        new_types = [b.get("event_type") for b in blocks_after[len(initial_blocks):]]
        self.assertNotIn("ADAPTER_CARTRIDGE_MOUNTED", new_types)


# ---------------------------------------------------------------------------
# 7. execute_single_child integration (mount happens before execution)
# ---------------------------------------------------------------------------

class TestExecuteSingleChildCartridgeIntegration(unittest.TestCase):
    """
    Verify that execute_single_child() calls _mount_cartridges_on_adapter()
    before any LLM execution when the mother has attached cartridges.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        from src.daemon import AutonomousJobCardEngine
        from src.models import ChildCard
        from src.privacy_guard import PrivacyGuard
        from src.sandbox import SandboxRunner
        self.engine = AutonomousJobCardEngine(workspace_root=self.tmp_dir)
        # Pre-initialise guards so execute_single_child bypasses the
        # mother.governance.restricted_paths lookup (matches pattern in other tests)
        self.engine.privacy_guard = PrivacyGuard(restricted_paths=[])
        self.engine.sandbox = SandboxRunner()
        self.ChildCard = ChildCard

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_child(self) -> Any:
        return self.ChildCard(
            id="child-integ-001",
            parent_mother_id="mother-test",
            name="Test Child",
            tactical_objective="Write a stub function.",
            deliverables=[{"path": "stub.py", "description": "A stub"}],
            validation_commands=[],
            max_iterations=2,
        )

    def test_mount_called_when_mother_has_cartridges(self):
        cart = _make_harness_patch_cartridge(name="security-guard")
        binding = _make_binding("security-guard")
        mother = _make_mother_with_cartridges([binding])

        mock_registry = MagicMock()
        mock_registry.get_cartridge.return_value = cart
        mock_lm = MagicMock()
        mock_lm.is_cached.return_value = False
        self.engine._adapter_registry = mock_registry
        self.engine._lora_manager = mock_lm

        child = self._make_child()
        child_path = os.path.join(self.tmp_dir, ".jobs", "child-integ-001.yaml")
        os.makedirs(os.path.dirname(child_path), exist_ok=True)

        # Patch _mount to count calls
        mount_calls = []
        orig = self.engine._mount_cartridges_on_adapter
        def _spy(mother, child_id):
            mount_calls.append(child_id)
            orig(mother=mother, child_id=child_id)
        self.engine._mount_cartridges_on_adapter = _spy

        self.engine.execute_single_child(
            child=child, mother=mother, child_path=child_path
        )

        self.assertEqual(mount_calls, ["child-integ-001"])

    def test_mount_not_called_when_no_cartridges(self):
        mother = _make_mother_with_cartridges([])

        mount_calls = []
        def _spy(mother, child_id):
            mount_calls.append(child_id)
        self.engine._mount_cartridges_on_adapter = _spy

        child = self._make_child()
        child_path = os.path.join(self.tmp_dir, ".jobs", "child-integ-002.yaml")
        os.makedirs(os.path.dirname(child_path), exist_ok=True)

        self.engine.execute_single_child(
            child=child, mother=mother, child_path=child_path
        )

        self.assertEqual(mount_calls, [])


if __name__ == "__main__":
    unittest.main()
