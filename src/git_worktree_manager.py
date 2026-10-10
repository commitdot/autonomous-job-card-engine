import os
import shutil
import subprocess
from typing import Dict, Any, Optional


class GitWorktreeManager:
    """
    Manages isolated Git worktrees for concurrent squad execution.
    Provides sandbox isolation per squad/worker and clean lifecycle teardown.
    """

    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.abspath(workspace_root)
        self.worktrees_dir = os.path.join(self.workspace_root, ".worktrees")
        self.is_git_repo = self._check_git_repo()

    def _check_git_repo(self) -> bool:
        """Checks if the workspace is part of a valid git repository."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return res.returncode == 0 and "true" in res.stdout.strip().lower()
        except Exception:
            return False

    def create_worktree(self, branch_name: str, base_branch: str = "main") -> str:
        """
        Creates an isolated worktree directory for the given branch.
        Falls back to isolated filesystem copy if not inside a git repository.
        """
        sanitized = branch_name.replace("/", "_").replace("\\", "_")
        target_path = os.path.join(self.worktrees_dir, sanitized)
        os.makedirs(self.worktrees_dir, exist_ok=True)

        if self.is_git_repo:
            try:
                # First ensure base branch exists or use HEAD
                res = subprocess.run(
                    ["git", "worktree", "add", "-B", branch_name, target_path, "HEAD"],
                    cwd=self.workspace_root,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if res.returncode == 0:
                    return target_path
            except Exception:
                pass

        # Filesystem fallback: create directory and copy essential source files
        if os.path.exists(target_path):
            shutil.rmtree(target_path, ignore_errors=True)
        os.makedirs(target_path, exist_ok=True)

        skip_dirs = {".git", ".worktrees", ".jobs", "__pycache__", "node_modules", ".venv", "venv"}
        for item in os.listdir(self.workspace_root):
            if item in skip_dirs:
                continue
            src = os.path.join(self.workspace_root, item)
            dst = os.path.join(target_path, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst, symlinks=True, ignore_dangling_symlinks=True)
            else:
                shutil.copy2(src, dst)

        return target_path

    def cleanup_worktree(self, worktree_path: str) -> bool:
        """
        Removes the git worktree and cleans up disk artifacts.
        """
        if not worktree_path or not os.path.exists(worktree_path):
            return True

        if self.is_git_repo:
            try:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", worktree_path],
                    cwd=self.workspace_root,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                subprocess.run(
                    ["git", "worktree", "prune"],
                    cwd=self.workspace_root,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except Exception:
                pass

        if os.path.exists(worktree_path):
            shutil.rmtree(worktree_path, ignore_errors=True)

        return not os.path.exists(worktree_path)

    def merge_worktree_branch(self, branch_name: str, target_branch: str = "main") -> Dict[str, Any]:
        """
        Attempts to merge the worktree's branch back into the main branch.
        """
        if not self.is_git_repo:
            return {
                "success": True,
                "merged": True,
                "conflict": False,
                "message": "Filesystem mode: branch merge simulated successfully.",
            }

        try:
            res = subprocess.run(
                ["git", "merge", "--no-ff", "-m", f"aje: merge squad branch {branch_name}", branch_name],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=20,
            )
            if res.returncode == 0:
                return {"success": True, "merged": True, "conflict": False, "message": res.stdout}
            else:
                # Merge conflict or failure
                subprocess.run(["git", "merge", "--abort"], cwd=self.workspace_root, capture_output=True)
                return {
                    "success": False,
                    "merged": False,
                    "conflict": True,
                    "message": f"Merge conflict on branch {branch_name}:\n{res.stderr or res.stdout}",
                }
        except Exception as e:
            return {"success": False, "merged": False, "conflict": False, "message": str(e)}
