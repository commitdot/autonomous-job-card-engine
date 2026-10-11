"""Worktree Containment Jailer
AJE v4.0 Security Matrix Component

Enforces path sandboxing and directory jail constraints across worker agents,
Git worktrees, and job cards. Prevents path traversal attacks (../../),
absolute OS escapes, symlink redirection exploits, and unauthorized outside-boundary writes.
"""

import os
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional


class PathContainmentError(PermissionError):
    """Raised when an operation attempts to escape the designated workspace/worktree jail."""
    def __init__(self, message: str, attempted_path: str, root_boundary: str):
        super().__init__(message)
        self.attempted_path = attempted_path
        self.root_boundary = root_boundary


class WorktreeJailer:
    """
    Enforces strict path containment within a designated root directory (e.g. workspace or git worktree).
    """

    def __init__(self, jail_root: str = "."):
        self.jail_root = Path(jail_root).resolve()

    def is_contained(self, target_path: str) -> bool:
        """Checks whether target_path stays strictly within jail_root without raising exceptions."""
        try:
            self.validate_path(target_path)
            return True
        except (PathContainmentError, Exception):
            return False

    def validate_path(self, target_path: str) -> Path:
        """
        Validates and resolves target_path.
        Raises PathContainmentError if the resolved path escapes the jail boundary.
        """
        if not target_path or not str(target_path).strip():
            raise PathContainmentError("Target path cannot be empty", attempted_path=str(target_path), root_boundary=str(self.jail_root))

        # Check for obvious traversal patterns in raw string
        raw_normalized = str(target_path).replace("\\", "/")
        if ".." in raw_normalized.split("/"):
            # Check if relative traversal escapes boundary
            pass

        # Resolve path
        path_obj = Path(target_path)
        if not path_obj.is_absolute():
            resolved = (self.jail_root / path_obj).resolve()
        else:
            resolved = path_obj.resolve()

        # Check if resolved path is relative to jail_root
        try:
            resolved.relative_to(self.jail_root)
        except ValueError:
            raise PathContainmentError(
                f"Path containment violation: '{target_path}' resolves to '{resolved}' which is outside jail root '{self.jail_root}'",
                attempted_path=str(target_path),
                root_boundary=str(self.jail_root)
            )

        # Check symlink destination if file/symlink exists
        if resolved.is_symlink():
            real_target = resolved.resolve()
            try:
                real_target.relative_to(self.jail_root)
            except ValueError:
                raise PathContainmentError(
                    f"Symlink containment violation: symlink '{target_path}' points outside jail boundary to '{real_target}'",
                    attempted_path=str(target_path),
                    root_boundary=str(self.jail_root)
                )

        return resolved

    def safe_resolve(self, target_path: str) -> Path:
        """Resolves target_path ensuring it is safely within the jail."""
        return self.validate_path(target_path)

    def validate_card_file_targets(self, card_dict: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validates all file targets listed in a job card (target_files, files, read_files, write_files).
        Returns (all_contained: bool, violations: List[str]).
        """
        violations: List[str] = []
        target_keys = ["target_files", "files", "read_files", "write_files", "affected_files"]

        for key in target_keys:
            val = card_dict.get(key)
            if isinstance(val, list):
                for p in val:
                    if isinstance(p, str):
                        try:
                            self.validate_path(p)
                        except PathContainmentError as e:
                            violations.append(str(e))
            elif isinstance(val, str) and val.strip():
                try:
                    self.validate_path(val)
                except PathContainmentError as e:
                    violations.append(str(e))

        return len(violations) == 0, violations
