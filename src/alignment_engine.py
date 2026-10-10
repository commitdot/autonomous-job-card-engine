import os
import re
import ast
import hashlib
from typing import List, Dict, Any, Set, Optional


class AlignmentEngine:
    """
    Continuous Multi-Tier Alignment Engine
    ======================================
    Calculates symbol blast radius and downstream dependencies across squads,
    generating targeted alignment ChildCards (QA test sync, OpenAPI doc sync, RAG re-index)
    while enforcing cryptographic task deduplication and a hard horizon ceiling (max_depth=3).
    """

    def __init__(self, workspace_root: str, max_wave_depth: int = 3):
        self.workspace_root = os.path.abspath(workspace_root)
        self.max_wave_depth = max_wave_depth
        self._seen_task_fingerprints: Set[str] = set()

    def calculate_task_fingerprint(
        self, parent_squad_id: str, tactical_objective: str, deliverable_paths: List[str]
    ) -> str:
        """Computes a deterministic SHA-256 fingerprint for cycle and duplicate prevention."""
        raw = f"{parent_squad_id.strip()}:::{tactical_objective.strip()}:::{','.join(sorted(deliverable_paths))}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def is_task_seen(self, fingerprint: str) -> bool:
        return fingerprint in self._seen_task_fingerprints

    def register_task_fingerprint(self, fingerprint: str) -> None:
        self._seen_task_fingerprints.add(fingerprint)

    def find_referencing_files(self, target_rel_path: str) -> List[str]:
        """
        Finds all files in the workspace that import or reference the target file or its module name.
        """
        referencing: List[str] = []
        target_norm = target_rel_path.replace("\\", "/")
        stem = os.path.splitext(os.path.basename(target_norm))[0]

        skip_dirs = {".git", ".jobs", "__pycache__", "node_modules", ".venv", "venv", ".worktrees"}

        for root, dirs, files in os.walk(self.workspace_root):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, self.workspace_root).replace("\\", "/")

                if rel_path == target_norm:
                    continue

                if rel_path.endswith((".py", ".ts", ".js", ".md", ".json", ".yaml", ".yml")):
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                        if stem in content or target_norm in content:
                            referencing.append(rel_path)
                    except Exception:
                        continue

        return referencing

    def calculate_blast_radius(self, modified_files: List[str]) -> Dict[str, Any]:
        """
        Computes the blast radius of a set of modified files across domains:
        - impacted_files: all files directly referencing the changed files
        - requires_qa_sync: whether test suites need alignment
        - requires_docs_sync: whether docs need alignment
        - requires_rag_reindex: whether local RAG store needs re-indexing
        """
        impacted_files: Set[str] = set()
        for f in modified_files:
            if not f:
                continue
            refs = self.find_referencing_files(f)
            impacted_files.update(refs)

        has_py_changes = any(f.endswith(".py") for f in modified_files)
        has_doc_changes = any(f.endswith(".md") or "doc" in f.lower() for f in modified_files)
        has_test_changes = any("test" in f.lower() for f in modified_files)

        qa_impacted = [f for f in impacted_files if "test" in f.lower()]
        doc_impacted = [f for f in impacted_files if f.endswith(".md") or "doc" in f.lower()]

        return {
            "modified_files": modified_files,
            "impacted_files": sorted(list(impacted_files)),
            "qa_impacted": qa_impacted,
            "doc_impacted": doc_impacted,
            "requires_qa_sync": has_py_changes and not has_test_changes,
            "requires_docs_sync": has_py_changes or has_doc_changes,
            "requires_rag_reindex": has_doc_changes or len(doc_impacted) > 0,
        }

    def generate_alignment_cards(
        self,
        completed_child_id: str,
        completed_child_name: str,
        parent_squad_id: str,
        current_wave_depth: int,
        modified_files: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Generates downstream alignment tasks when primary code or contracts change.
        Enforces horizon ceiling (max_wave_depth) and deduplication.
        """
        if current_wave_depth >= self.max_wave_depth:
            print(f"  [ALIGNMENT] Horizon ceiling reached (depth {current_wave_depth} >= {self.max_wave_depth}). Cascades halted.")
            return []

        blast = self.calculate_blast_radius(modified_files)
        alignment_tasks: List[Dict[str, Any]] = []

        # 1. QA Test Alignment Task
        if blast["requires_qa_sync"]:
            mod_str = ", ".join(modified_files[:3])
            qa_objective = f"Synchronize unit and regression test assertions for modified modules: {mod_str}"
            qa_deliverables = [{"path": f"tests/test_align_{completed_child_id.replace('-', '_')}.py", "description": f"Regression assertions for {mod_str}"}]
            qa_deliv_paths = [d["path"] for d in qa_deliverables]
            
            fp = self.calculate_task_fingerprint("squad-qa", qa_objective, qa_deliv_paths)
            if not self.is_task_seen(fp):
                self.register_task_fingerprint(fp)
                alignment_tasks.append({
                    "id": f"align-qa-{completed_child_id[:8]}",
                    "name": f"QA Alignment for {completed_child_name}",
                    "parent_squad_id": "squad-qa",
                    "tactical_objective": qa_objective,
                    "deliverables": qa_deliverables,
                    "validation_commands": ["python -m unittest discover -s tests"],
                    "wave_depth": current_wave_depth + 1,
                    "depends_on": [completed_child_id],
                })

        # 2. Documentation Alignment Task
        if blast["requires_docs_sync"] and not any(f.endswith(".md") for f in modified_files):
            mod_str = ", ".join(modified_files[:3])
            doc_objective = f"Update technical documentation and interface contracts for: {mod_str}"
            doc_deliverables = [{"path": "docs/alignment_changelog.md", "description": f"Changelog and contract alignment for {mod_str}"}]
            doc_deliv_paths = [d["path"] for d in doc_deliverables]

            fp = self.calculate_task_fingerprint("squad-docs", doc_objective, doc_deliv_paths)
            if not self.is_task_seen(fp):
                self.register_task_fingerprint(fp)
                alignment_tasks.append({
                    "id": f"align-doc-{completed_child_id[:8]}",
                    "name": f"Doc Alignment for {completed_child_name}",
                    "parent_squad_id": "squad-docs",
                    "tactical_objective": doc_objective,
                    "deliverables": doc_deliverables,
                    "validation_commands": [],
                    "wave_depth": current_wave_depth + 1,
                    "depends_on": [completed_child_id],
                })

        return alignment_tasks
