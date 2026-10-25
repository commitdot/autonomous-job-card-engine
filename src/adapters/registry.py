"""
AdapterRegistry — Cartridge Catalog Indexing, Discovery, and Installation
========================================================================
Manages local and remote adapter cartridge registries, search indexing,
pull/install workflows, and integration with LoRAManager for weights.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from src.adapters.lora_manager import LoRAManager
from src.models import (
    AdapterCartridge,
    CartridgeMetadata,
    CartridgeSpec,
    CartridgeValidationError,
)


class RegistryError(Exception):
    """Base exception for AdapterRegistry operations."""


class CartridgeNotFoundError(RegistryError):
    """Raised when a requested cartridge is not found in the registry."""


def _parse_semver(v: str) -> tuple:
    """Helper to parse a basic semver string into sortable integers."""
    clean = v.split("-")[0].split("+")[0]
    parts = clean.split(".")
    try:
        return tuple(int(p) for p in parts[:3])
    except ValueError:
        return (0, 0, 0)


class AdapterRegistry:
    """
    Central hub for discovering, querying, installing, and managing Adapter Cartridges.

    Directories:
      - registry_dirs: Local catalog paths scanned for available cartridges (e.g. `registry/cartridges/`)
      - installed_dir: Local path where pulled/installed cartridges are stored (default `.jobs/installed_cartridges/`)
    """

    DEFAULT_REGISTRY_DIRS = ["registry/cartridges", "cartridges", ".jobs/cartridges"]
    DEFAULT_INSTALLED_DIR = os.path.join(".jobs", "installed_cartridges")

    def __init__(
        self,
        registry_dirs: Optional[Sequence[str]] = None,
        installed_dir: Optional[str] = None,
        lora_manager: Optional[LoRAManager] = None,
    ):
        if registry_dirs is None:
            self.registry_dirs = [os.path.abspath(d) for d in self.DEFAULT_REGISTRY_DIRS]
        else:
            self.registry_dirs = [os.path.abspath(d) for d in registry_dirs]

        self.installed_dir = os.path.abspath(installed_dir or self.DEFAULT_INSTALLED_DIR)
        os.makedirs(self.installed_dir, exist_ok=True)

        self.lora_manager = lora_manager or LoRAManager()
        self._index: Dict[str, Dict[str, AdapterCartridge]] = {}  # name -> {version: cartridge}
        self.refresh_index()

    def refresh_index(self) -> int:
        """
        Scan all registry_dirs and index all discovered cartridge manifests.
        Returns total count of indexed cartridges.
        """
        self._index.clear()
        total_found = 0

        for r_dir in self.registry_dirs:
            if not os.path.isdir(r_dir):
                continue
            for root, _, files in os.walk(r_dir):
                for f in files:
                    if f == "cartridge.yaml" or f.endswith(".cartridge.yaml"):
                        full_path = os.path.join(root, f)
                        try:
                            cart = AdapterCartridge.from_yaml(full_path)
                            self._add_to_index(cart)
                            total_found += 1
                        except Exception:
                            # Skip malformed manifests during discovery
                            continue

        return total_found

    def _add_to_index(self, cartridge: AdapterCartridge) -> None:
        """Internal helper to store a cartridge in the in-memory lookup table."""
        name = cartridge.metadata.name
        ver = str(cartridge.metadata.version)
        if name not in self._index:
            self._index[name] = {}
        self._index[name][ver] = cartridge

    def register_cartridge(
        self, cartridge_or_path: Union[AdapterCartridge, str]
    ) -> AdapterCartridge:
        """
        Manually register an in-memory cartridge or cartridge file into the registry index.
        """
        if isinstance(cartridge_or_path, str):
            cart = AdapterCartridge.from_yaml(cartridge_or_path)
        elif isinstance(cartridge_or_path, AdapterCartridge):
            cart = cartridge_or_path
        else:
            raise RegistryError(f"Unsupported cartridge input: {type(cartridge_or_path)}")

        cart.validate()
        self._add_to_index(cart)
        return cart

    def search(
        self,
        query: str = "",
        domain: Optional[str] = None,
        tag: Optional[str] = None,
        cartridge_type: Optional[str] = None,
        base_model: Optional[str] = None,
        archetype: Optional[str] = None,
    ) -> List[AdapterCartridge]:
        """
        Search indexed and installed cartridges matching criteria.

        Args:
            query: Free-text search matching name, description, author, domain, tags.
            domain: Filter by exact domain (case-insensitive).
            tag: Filter by tag presence.
            cartridge_type: Filter by 'harness-patch', 'lora', 'hybrid'.
            base_model: Filter by base model compatibility.
            archetype: Filter by tactical worker archetype compatibility.

        Returns:
            Sorted list of matching AdapterCartridge instances (latest version of each).
        """
        # Collect all candidates (indexed + installed)
        all_candidates: Dict[str, AdapterCartridge] = {}

        # 1. From index
        for name, versions in self._index.items():
            best_ver = self._select_latest_version(versions)
            if best_ver:
                all_candidates[name] = best_ver

        # 2. From installed
        for inst in self.list_installed():
            all_candidates[inst.metadata.name] = inst

        q = query.strip().lower() if query else ""
        results: List[AdapterCartridge] = []

        for cart in all_candidates.values():
            md = cart.metadata
            sp = cart.spec

            # 1. Free text query
            if q:
                searchable_text = f"{md.name} {md.description} {md.author} {md.domain} {' '.join(md.tags)}".lower()
                if q not in searchable_text:
                    continue

            # 2. Domain filter
            if domain and md.domain.strip().lower() != domain.strip().lower():
                continue

            # 3. Tag filter
            if tag and tag.lower() not in [t.lower() for t in md.tags]:
                continue

            # 4. Cartridge type filter
            if cartridge_type and sp.type != cartridge_type:
                continue

            # 5. Base model filter
            if base_model and not any(
                base_model.lower() in bm.lower() for bm in sp.base_models
            ):
                continue

            # 6. Archetype filter
            if archetype and not any(
                archetype.lower() in arch.lower() for arch in sp.compatible_archetypes
            ):
                continue

            results.append(cart)

        # Sort results alphabetically by name
        results.sort(key=lambda c: c.metadata.name)
        return results

    def _select_latest_version(
        self, versions: Dict[str, AdapterCartridge]
    ) -> Optional[AdapterCartridge]:
        """Select the cartridge with the highest semver from a version dictionary."""
        if not versions:
            return None
        sorted_vers = sorted(versions.keys(), key=_parse_semver, reverse=True)
        return versions[sorted_vers[0]]

    def get_cartridge(
        self, name: str, version: str = "latest"
    ) -> Optional[AdapterCartridge]:
        """
        Retrieve a cartridge by name and version. Checks installed first, then indexed catalog.
        """
        # 1. Check installed cartridges
        installed_path = os.path.join(self.installed_dir, name, "cartridge.yaml")
        if os.path.isfile(installed_path):
            try:
                cart = AdapterCartridge.from_yaml(installed_path)
                if version == "latest" or str(cart.metadata.version) == str(version):
                    return cart
            except Exception:
                pass

        # 2. Check catalog index
        if name in self._index:
            versions = self._index[name]
            if version == "latest":
                return self._select_latest_version(versions)
            return versions.get(str(version))

        return None

    def list_installed(self) -> List[AdapterCartridge]:
        """List all currently installed cartridges in installed_dir."""
        installed: List[AdapterCartridge] = []
        if not os.path.isdir(self.installed_dir):
            return installed

        for item in sorted(os.listdir(self.installed_dir)):
            item_dir = os.path.join(self.installed_dir, item)
            if os.path.isdir(item_dir):
                yaml_file = os.path.join(item_dir, "cartridge.yaml")
                if os.path.isfile(yaml_file):
                    try:
                        cart = AdapterCartridge.from_yaml(yaml_file)
                        installed.append(cart)
                    except Exception:
                        continue
        return installed

    def is_installed(self, name: str, version: str = "latest") -> bool:
        """Check if a cartridge is installed locally."""
        cart = self.get_cartridge(name, version)
        if not cart:
            return False
        installed_path = os.path.join(self.installed_dir, name, "cartridge.yaml")
        return os.path.isfile(installed_path)

    def pull(
        self,
        name_or_path: str,
        version: str = "latest",
        download_weights: bool = False,
        force: bool = False,
    ) -> AdapterCartridge:
        """
        Pull/install a cartridge into installed_dir.

        Args:
            name_or_path: Name of indexed cartridge OR direct path to cartridge.yaml / directory.
            version: Cartridge version to pull (default 'latest').
            download_weights: If True and cartridge has LoRA weights, downloads them via LoRAManager.
            force: If True, overwrites existing installed manifest and weights.

        Returns:
            The installed AdapterCartridge instance.

        Raises:
            CartridgeNotFoundError: If cartridge cannot be found.
            RegistryError: If installation fails.
        """
        cart: Optional[AdapterCartridge] = None

        # Check if local path
        if os.path.isfile(name_or_path):
            cart = AdapterCartridge.from_yaml(name_or_path)
        elif os.path.isdir(name_or_path):
            candidate = os.path.join(name_or_path, "cartridge.yaml")
            if os.path.isfile(candidate):
                cart = AdapterCartridge.from_yaml(candidate)

        # Otherwise look up from catalog
        if cart is None:
            cart = self.get_cartridge(name_or_path, version)

        if cart is None:
            raise CartridgeNotFoundError(
                f"Cartridge '{name_or_path}' (version: {version}) was not found in registry."
            )

        name = cart.metadata.name
        dest_dir = os.path.join(self.installed_dir, name)
        dest_yaml = os.path.join(dest_dir, "cartridge.yaml")

        if not os.path.isfile(dest_yaml) or force:
            os.makedirs(dest_dir, exist_ok=True)
            cart.to_yaml(dest_yaml)

        # Optionally download LoRA weights
        if download_weights and cart.spec.type in ("lora", "hybrid"):
            if cart.spec.lora_repo_id and cart.spec.lora_filename:
                self.lora_manager.download_weights(cart, force=force)

        # Refresh index so installed version is immediately discoverable
        self._add_to_index(cart)
        return cart

    def uninstall(self, name: str, remove_weights: bool = True) -> bool:
        """
        Uninstall a cartridge from installed_dir and optionally purge its cached weights.
        Returns True if uninstalled, False if it was not installed.
        """
        cart_dir = os.path.join(self.installed_dir, name)
        if not os.path.isdir(cart_dir):
            return False

        shutil.rmtree(cart_dir)

        if remove_weights:
            self.lora_manager.clear_cache(name)

        self.refresh_index()
        return True
