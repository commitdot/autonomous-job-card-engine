"""
LoRAManager — Download, Cache, and Verify LoRA Weights
======================================================
Manages local storage and Hugging Face Hub downloading of LoRA adapter weights
with SHA-256 checksum verification and local caching in `.jobs/adapters/`.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import urllib.request
import urllib.error

from src.models import AdapterCartridge, CartridgeSpec


class LoRAManagerError(Exception):
    """Base exception for LoRAManager operations."""


class ChecksumMismatchError(LoRAManagerError):
    """Raised when downloaded weights do not match the expected SHA-256 checksum."""


class LoRADownloadError(LoRAManagerError):
    """Raised when downloading LoRA weights fails."""


class LoRAManager:
    """
    Manages downloading, local caching, and checksum verification for LoRA weights.

    Weights are cached under:
      `<cache_dir>/<cartridge_name_or_repo>/<filename>`
    Default cache root is `.jobs/adapters/`.
    """

    DEFAULT_CACHE_DIR = os.path.join(".jobs", "adapters")

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = os.path.abspath(
            cache_dir or os.environ.get("AJE_ADAPTERS_CACHE", self.DEFAULT_CACHE_DIR)
        )
        os.makedirs(self.cache_dir, exist_ok=True)

    @staticmethod
    def compute_sha256(file_path: Union[str, Path], chunk_size: int = 65536) -> str:
        """Compute the hex-encoded SHA-256 digest of a local file."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(chunk_size):
                hasher.update(chunk)
        return hasher.hexdigest().lower()

    @classmethod
    def verify_checksum(cls, file_path: Union[str, Path], expected_sha256: str) -> bool:
        """Verify whether a file's SHA-256 checksum matches the expected digest."""
        if not expected_sha256 or not os.path.isfile(file_path):
            return False
        return cls.compute_sha256(file_path) == expected_sha256.strip().lower()

    def _resolve_spec(
        self,
        cartridge_or_spec: Union[AdapterCartridge, CartridgeSpec, Dict[str, Any]],
    ) -> tuple[str, str, str, str]:
        """
        Extract (cartridge_name, repo_id, filename, expected_sha256) from various input types.
        """
        if isinstance(cartridge_or_spec, AdapterCartridge):
            name = cartridge_or_spec.metadata.name
            spec = cartridge_or_spec.spec
            return name, spec.lora_repo_id, spec.lora_filename, spec.lora_sha256
        elif isinstance(cartridge_or_spec, CartridgeSpec):
            spec = cartridge_or_spec
            name = spec.lora_repo_id.replace("/", "_") if spec.lora_repo_id else "unknown"
            return name, spec.lora_repo_id, spec.lora_filename, spec.lora_sha256
        elif isinstance(cartridge_or_spec, dict):
            name = cartridge_or_spec.get("name") or cartridge_or_spec.get("metadata", {}).get("name", "unknown")
            spec_dict = cartridge_or_spec.get("spec", cartridge_or_spec)
            repo_id = spec_dict.get("lora_repo_id", "")
            filename = spec_dict.get("lora_filename", "")
            sha256 = spec_dict.get("lora_sha256", "")
            return name, repo_id, filename, sha256
        else:
            raise ValueError(f"Unsupported cartridge/spec type: {type(cartridge_or_spec)}")

    def get_adapter_path(
        self,
        cartridge_or_spec: Union[AdapterCartridge, CartridgeSpec, Dict[str, Any], str],
        filename: Optional[str] = None,
    ) -> str:
        """
        Return the absolute path where weights are or will be cached.
        """
        if isinstance(cartridge_or_spec, str):
            cartridge_dir = cartridge_or_spec
            fn = filename or "adapter.safetensors"
        else:
            name, repo_id, spec_fn, _ = self._resolve_spec(cartridge_or_spec)
            cartridge_dir = name or repo_id.replace("/", "_")
            fn = filename or spec_fn or "adapter.safetensors"

        # Sanitize directory name
        safe_dir = cartridge_dir.replace("/", "_").replace("\\", "_")
        return os.path.join(self.cache_dir, safe_dir, fn)

    def is_cached(
        self,
        cartridge_or_spec: Union[AdapterCartridge, CartridgeSpec, Dict[str, Any], str],
        filename: Optional[str] = None,
        expected_sha256: Optional[str] = None,
    ) -> bool:
        """
        Check if the adapter weights exist locally in the cache and match checksum if provided.
        """
        path = self.get_adapter_path(cartridge_or_spec, filename)
        if not os.path.isfile(path):
            return False

        if expected_sha256 is None and not isinstance(cartridge_or_spec, str):
            _, _, _, spec_sha = self._resolve_spec(cartridge_or_spec)
            expected_sha256 = spec_sha or None

        if expected_sha256:
            return self.verify_checksum(path, expected_sha256)
        return True

    def build_hf_url(self, repo_id: str, filename: str, revision: str = "main") -> str:
        """Construct Hugging Face Hub download URL."""
        endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
        return f"{endpoint}/{repo_id}/resolve/{revision}/{filename}"

    def download_weights(
        self,
        cartridge_or_spec: Union[AdapterCartridge, CartridgeSpec, Dict[str, Any]],
        force: bool = False,
        timeout: int = 30,
    ) -> str:
        """
        Download LoRA weights from Hugging Face Hub (or mock endpoint) and cache locally.

        Args:
            cartridge_or_spec: AdapterCartridge or CartridgeSpec containing repo_id and filename.
            force: If True, re-download even if already cached.
            timeout: HTTP request timeout in seconds.

        Returns:
            Absolute path to the downloaded cached weight file.

        Raises:
            ValueError: If repo_id or filename is missing.
            ChecksumMismatchError: If downloaded file does not match expected SHA-256.
            LoRADownloadError: If downloading fails (network, 404, auth error).
        """
        name, repo_id, filename, expected_sha256 = self._resolve_spec(cartridge_or_spec)

        if not repo_id or not filename:
            raise ValueError(
                f"Cartridge '{name}' is missing required 'lora_repo_id' or 'lora_filename'."
            )

        target_path = self.get_adapter_path(cartridge_or_spec)
        target_dir = os.path.dirname(target_path)
        os.makedirs(target_dir, exist_ok=True)

        if not force and self.is_cached(cartridge_or_spec):
            return target_path

        url = self.build_hf_url(repo_id, filename)
        headers = {
            "User-Agent": "Autonomous-Job-Card-Engine/4.0 (AdapterHub)",
        }
        token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = urllib.request.Request(url, headers=headers)
        temp_fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix="download_", suffix=".tmp")
        os.close(temp_fd)

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response, open(temp_path, "wb") as out_file:
                shutil.copyfileobj(response, out_file)

            if expected_sha256:
                actual_sha = self.compute_sha256(temp_path)
                if actual_sha != expected_sha256.strip().lower():
                    raise ChecksumMismatchError(
                        f"Checksum mismatch for '{name}' ({filename}): "
                        f"expected {expected_sha256.lower()}, got {actual_sha}"
                    )

            # Atomic move into final position
            if os.path.exists(target_path):
                os.remove(target_path)
            shutil.move(temp_path, target_path)
            return target_path

        except urllib.error.HTTPError as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise LoRADownloadError(
                f"HTTP error {e.code} ({e.reason}) downloading LoRA weights from {url}"
            ) from e
        except urllib.error.URLError as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise LoRADownloadError(
                f"Network error downloading LoRA weights from {url}: {e.reason}"
            ) from e
        except ChecksumMismatchError:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise LoRADownloadError(
                f"Unexpected error downloading LoRA weights for '{name}': {e}"
            ) from e

    def save_weights_to_cache(
        self,
        cartridge_or_spec: Union[AdapterCartridge, CartridgeSpec, Dict[str, Any], str],
        data_or_src_path: Union[bytes, str, Path],
        filename: Optional[str] = None,
        expected_sha256: Optional[str] = None,
    ) -> str:
        """
        Manually store weight bytes or copy a local file into the adapter cache.
        """
        target_path = self.get_adapter_path(cartridge_or_spec, filename)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        if isinstance(data_or_src_path, bytes):
            with open(target_path, "wb") as f:
                f.write(data_or_src_path)
        else:
            shutil.copyfile(str(data_or_src_path), target_path)

        if expected_sha256:
            actual_sha = self.compute_sha256(target_path)
            if actual_sha != expected_sha256.strip().lower():
                os.remove(target_path)
                raise ChecksumMismatchError(
                    f"Checksum mismatch saving weights: expected {expected_sha256}, got {actual_sha}"
                )

        return target_path

    def clear_cache(self, cartridge_name: Optional[str] = None) -> int:
        """
        Clear cached adapter weights. If cartridge_name is provided, clears only that cartridge.
        Returns the number of removed files.
        """
        if not os.path.isdir(self.cache_dir):
            return 0

        count = 0
        if cartridge_name:
            safe_name = cartridge_name.replace("/", "_").replace("\\", "_")
            cart_dir = os.path.join(self.cache_dir, safe_name)
            if os.path.isdir(cart_dir):
                for root, _, files in os.walk(cart_dir):
                    count += len(files)
                shutil.rmtree(cart_dir)
        else:
            for item in os.listdir(self.cache_dir):
                item_path = os.path.join(self.cache_dir, item)
                if os.path.isdir(item_path):
                    for _, _, files in os.walk(item_path):
                        count += len(files)
                    shutil.rmtree(item_path)
                elif os.path.isfile(item_path):
                    os.remove(item_path)
                    count += 1

        return count

    def list_cached(self) -> List[Dict[str, Any]]:
        """
        List all cached adapter files with metadata.
        """
        if not os.path.isdir(self.cache_dir):
            return []

        results = []
        for root, _, files in os.walk(self.cache_dir):
            for f in files:
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, self.cache_dir)
                parts = rel_path.split(os.sep)
                cartridge_name = parts[0] if len(parts) > 1 else "root"
                stat = os.stat(full_path)
                results.append({
                    "cartridge": cartridge_name,
                    "filename": f,
                    "path": full_path,
                    "size_bytes": stat.st_size,
                    "sha256": self.compute_sha256(full_path),
                })
        return results
