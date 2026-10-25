# AJE LLM Adapter package
from .base import BaseLLMAdapter
from .harness_mixin import (
    HarnessMixin,
    HarnessConfig,
    HarnessCandidate,
    ComposedHarness,
)
from .lora_manager import (
    LoRAManager,
    LoRAManagerError,
    ChecksumMismatchError,
    LoRADownloadError,
)
from .patch_loader import (
    PatchLoaderError,
    merge_harness_patch,
    apply_cartridge,
    apply_cartridges,
    load_and_merge_cartridge_file,
)
from .registry import (
    AdapterRegistry,
    RegistryError,
    CartridgeNotFoundError,
)
from .sim_adapter import SimAdapter
from .watsonx_adapter import WatsonxAdapter
from .ollama_adapter import OllamaAdapter

__all__ = [
    "BaseLLMAdapter",
    "HarnessMixin",
    "HarnessConfig",
    "HarnessCandidate",
    "ComposedHarness",
    "LoRAManager",
    "LoRAManagerError",
    "ChecksumMismatchError",
    "LoRADownloadError",
    "PatchLoaderError",
    "merge_harness_patch",
    "apply_cartridge",
    "apply_cartridges",
    "load_and_merge_cartridge_file",
    "AdapterRegistry",
    "RegistryError",
    "CartridgeNotFoundError",
    "SimAdapter",
    "WatsonxAdapter",
    "OllamaAdapter",
]
