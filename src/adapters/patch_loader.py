"""
PatchLoader — YAML Harness Patch Merger
=======================================
Merges zero-weight and hybrid YAML harness patches from Adapter Cartridges
into HarnessConfig (AJE's 8-component modular harness).
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from src.adapters.harness_mixin import HarnessConfig
from src.models import AdapterBinding, AdapterCartridge, CartridgeSpec


LIST_COMPONENT_KEYS = (
    "guardrails",
    "memory",
    "tool_guidance",
    "subagents",
    "skills",
    "scripts",
    "plugins",
)


class PatchLoaderError(Exception):
    """Base exception for PatchLoader errors."""


def _merge_unique_list(base_list: List[str], new_items: Iterable[str]) -> List[str]:
    """Append new items to base list, preserving order and eliminating duplicate entries."""
    result = list(base_list)
    seen = set(base_list)
    for item in new_items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def extract_patch_dict(
    cartridge_or_patch: Union[AdapterCartridge, CartridgeSpec, Dict[str, Any]],
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Extract and normalize the harness patch dictionary from various input types.
    """
    patch_dict: Dict[str, Any] = {}
    if isinstance(cartridge_or_patch, AdapterCartridge):
        patch_dict = copy.deepcopy(cartridge_or_patch.spec.harness_patch)
    elif isinstance(cartridge_or_patch, CartridgeSpec):
        patch_dict = copy.deepcopy(cartridge_or_patch.harness_patch)
    elif isinstance(cartridge_or_patch, dict):
        if "spec" in cartridge_or_patch and isinstance(cartridge_or_patch["spec"], dict):
            patch_dict = copy.deepcopy(cartridge_or_patch["spec"].get("harness_patch", {}))
        elif "harness_patch" in cartridge_or_patch and isinstance(cartridge_or_patch["harness_patch"], dict):
            patch_dict = copy.deepcopy(cartridge_or_patch["harness_patch"])
        else:
            patch_dict = copy.deepcopy(cartridge_or_patch)
    else:
        raise PatchLoaderError(f"Unsupported cartridge/patch type: {type(cartridge_or_patch)}")

    if overrides:
        for k, v in overrides.items():
            if k in LIST_COMPONENT_KEYS and isinstance(v, (list, tuple)) and isinstance(patch_dict.get(k), list):
                patch_dict[k] = _merge_unique_list(patch_dict[k], v)
            elif isinstance(v, dict) and isinstance(patch_dict.get(k), dict):
                patch_dict[k] = {**patch_dict[k], **v}
            else:
                patch_dict[k] = copy.deepcopy(v)

    return patch_dict


def merge_harness_patch(
    base: HarnessConfig,
    cartridge_or_patch: Union[AdapterCartridge, CartridgeSpec, Dict[str, Any]],
    overrides: Optional[Dict[str, Any]] = None,
    cartridge_name: Optional[str] = None,
) -> HarnessConfig:
    """
    Merge a harness patch into an existing HarnessConfig instance, returning a new HarnessConfig.

    Rules:
      - system_prompt: Joined with double newlines if both present.
      - list fields (guardrails, memory, tool_guidance, subagents, skills, scripts, plugins):
        Appended without duplicate entries, preserving order.
      - metadata: Shallow-merged, with 'applied_cartridges' tracked.
    """
    patch = extract_patch_dict(cartridge_or_patch, overrides=overrides)
    if isinstance(cartridge_or_patch, AdapterCartridge) and not cartridge_name:
        cartridge_name = cartridge_or_patch.metadata.name

    # Create shallow copy of base dict to start
    merged_data = base.to_dict()

    # 1. System Prompt
    base_sp = (merged_data.get("system_prompt") or "").strip()
    patch_sp = (patch.get("system_prompt") or "").strip()
    if base_sp and patch_sp:
        merged_data["system_prompt"] = f"{base_sp}\n\n{patch_sp}"
    elif patch_sp:
        merged_data["system_prompt"] = patch_sp
    else:
        merged_data["system_prompt"] = base_sp

    # 2. List components
    for key in LIST_COMPONENT_KEYS:
        base_list = merged_data.get(key) or []
        patch_list = patch.get(key) or []
        if isinstance(patch_list, str):
            patch_list = [patch_list]
        merged_data[key] = _merge_unique_list(base_list, patch_list)

    # 3. Metadata
    base_meta = dict(merged_data.get("metadata") or {})
    patch_meta = dict(patch.get("metadata") or {})
    combined_meta = {**base_meta, **patch_meta}

    if cartridge_name:
        applied = list(combined_meta.get("applied_cartridges") or [])
        if cartridge_name not in applied:
            applied.append(cartridge_name)
        combined_meta["applied_cartridges"] = applied

    merged_data["metadata"] = combined_meta

    return HarnessConfig.from_dict(merged_data)


def apply_cartridge(
    base: HarnessConfig,
    cartridge: AdapterCartridge,
    binding: Optional[AdapterBinding] = None,
) -> HarnessConfig:
    """
    Apply a single AdapterCartridge to a HarnessConfig according to binding rules.
    If binding is disabled, returns unchanged base config.
    """
    if binding and not binding.enabled:
        return copy.deepcopy(base)

    overrides = binding.overrides if binding else None
    return merge_harness_patch(
        base=base,
        cartridge_or_patch=cartridge,
        overrides=overrides,
        cartridge_name=cartridge.metadata.name,
    )


def apply_cartridges(
    base: HarnessConfig,
    cartridges: Sequence[Union[AdapterCartridge, Tuple[AdapterCartridge, Optional[AdapterBinding]]]],
) -> HarnessConfig:
    """
    Apply multiple AdapterCartridges to a HarnessConfig in priority order.
    Higher priority bindings are processed later so they can build upon / refine earlier patches.
    """
    current_config = copy.deepcopy(base)

    normalized: List[Tuple[AdapterCartridge, Optional[AdapterBinding], int]] = []
    for item in cartridges:
        if isinstance(item, tuple):
            cart, bind = item
            prio = bind.priority if bind else 0
            normalized.append((cart, bind, prio))
        else:
            normalized.append((item, None, 0))

    # Sort stably by priority (ascending, so higher priority items are merged on top)
    normalized.sort(key=lambda x: x[2])

    for cart, bind, _ in normalized:
        current_config = apply_cartridge(current_config, cart, bind)

    return current_config


def load_and_merge_cartridge_file(
    base: HarnessConfig,
    cartridge_yaml_path: str,
    overrides: Optional[Dict[str, Any]] = None,
) -> HarnessConfig:
    """Load a cartridge YAML manifest from disk and merge its harness patch."""
    cartridge = AdapterCartridge.from_yaml(cartridge_yaml_path)
    return merge_harness_patch(base, cartridge, overrides=overrides)
