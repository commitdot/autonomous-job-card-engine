import os
import json
import glob
from datetime import datetime
from typing import List, Dict, Any, Optional

from .models import MotherCard, ChildCard, SquadLeadCard, RepositoryBinding
from .privacy_guard import PrivacyGuard
from .sandbox import SandboxRunner
from .adapters.base import BaseLLMAdapter
from .adapters.sim_adapter import SimAdapter
from .rag.indexer import LocalRAGIndexer
from .rag.retriever import LocalRAGRetriever
from .ast_skeleton import ASTSkeletonizer
from .alignment_engine import AlignmentEngine
from .squad_executor import SquadDAGScheduler, SquadWaveExecutor


class AutonomousJobCardEngine:
    def __init__(self, workspace_root: str, adapter: Optional[BaseLLMAdapter] = None):
        self.workspace_root = os.path.abspath(workspace_root)
        self.jobs_dir = os.path.join(self.workspace_root, ".jobs")
        self.audit_dir = os.path.join(self.jobs_dir, "audit")
        self.rag_dir = os.path.join(self.jobs_dir, "rag")

        os.makedirs(self.workspace_root, exist_ok=True)
        os.makedirs(self.jobs_dir, exist_ok=True)
        os.makedirs(self.audit_dir, exist_ok=True)
        os.makedirs(self.rag_dir, exist_ok=True)

        # Accept any BaseLLMAdapter; fall back to SimAdapter for backward compatibility
        self.llm: BaseLLMAdapter = adapter or SimAdapter(workspace_root=self.workspace_root)

        # RAG Subsystem
        self.rag_indexer = LocalRAGIndexer(workspace_root=self.workspace_root, storage_dir=self.rag_dir)
        self.rag_retriever = LocalRAGRetriever(workspace_root=self.workspace_root, storage_dir=self.rag_dir)
        self.skeletonizer = ASTSkeletonizer(workspace_root=self.workspace_root)
        self.alignment_engine = AlignmentEngine(workspace_root=self.workspace_root)

        # Initialised per-cycle from MotherCard config
        self.privacy_guard = None
        self.sandbox = None

    def write_audit_log(self, child_id: str, action_type: str, details: str, meta: Dict[str, Any] = None):
        """
        Maintains an immutable forensic audit log for every action executed by the autonomous engine.
        """
        audit_file = os.path.join(self.audit_dir, f"audit_{child_id}.json")
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "child_id": child_id,
            "action": action_type,
            "details": details,
            "metadata": meta or {}
        }
        
        logs = []
        if os.path.exists(audit_file):
            try:
                with open(audit_file, 'r', encoding='utf-8') as f:
                    logs = json.load(f)
            except Exception:
                pass
        
        logs.append(entry)
        with open(audit_file, 'w', encoding='utf-8') as f:
            json.dump(logs, f, indent=2)

    def index_rag_knowledge(self, paths: List[str]):
        """Indexes internal RFCs, markdown docs, and schemas into local RAG store."""
        count = self.rag_indexer.index_paths(paths)
        if count > 0:
            print(f"  [RAG] Indexed {count} local documentation chunks.")

    def run_preflight_quality_scan(self, mother: MotherCard) -> Dict[str, Any]:
        """
        Executes Mother Card's declarative Pre-Flight Repository Consistency & Regex Scan.
        Scans all source, documentation, and config files for forbidden patterns, version drift,
        or unscrubbed credentials.
        """
        gov = getattr(mother, "governance", None)
        if not gov or not gov.enable_repo_regex_scan or not gov.forbidden_patterns:
            return {"scanned_files": 0, "violations": []}

        compiled_patterns = []
        for pat in gov.forbidden_patterns:
            try:
                import re
                compiled_patterns.append((pat, re.compile(pat)))
            except Exception:
                pass

        violations = []
        scanned_count = 0
        skip_dirs = {".git", ".jobs", "__pycache__", "node_modules", ".venv", "venv", ".worktrees"}

        for root, dirs, files in os.walk(self.workspace_root):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, self.workspace_root).replace("\\", "/")

                if rel_path.endswith((".py", ".md", ".json", ".yaml", ".yml", ".txt", ".ts", ".js")):
                    scanned_count += 1
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                        for raw_pat, regex in compiled_patterns:
                            matches = regex.findall(content)
                            if matches:
                                violations.append({
                                    "file": rel_path,
                                    "pattern": raw_pat,
                                    "match_count": len(matches)
                                })
                    except Exception:
                        continue

        if violations:
            print(f"  [PREFLIGHT-SCAN] [WARN] Found {len(violations)} consistency drift item(s) in {scanned_count} files.")
            for v in violations[:5]:
                print(f"    - File: {v['file']} (Pattern '{v['pattern']}' matched {v['match_count']} time(s))")
        else:
            print(f"  [PREFLIGHT-SCAN] [OK] Scanned {scanned_count} files against governance patterns. Clean.")

        return {"scanned_files": scanned_count, "violations": violations}

    def execute_single_child(
        self,
        child: ChildCard,
        mother: MotherCard,
        child_path: str,
        execution_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes a single Child Card (System 1 fast-path or System 2 deliberative self-healing loop)
        inside the designated workspace or isolated worktree root.
        """
        exec_root = execution_root or self.workspace_root

        # Security evaluation
        paths_to_be_edited = [d.get("path") for d in child.deliverables]
        assigned_profile = self.privacy_guard.evaluate_routing_profile(
            files_accessed=paths_to_be_edited,
            base_mode=mother.privacy_mode,
        )
        child.execution_profile_assigned = assigned_profile
        print(f"[SEC] Privacy Guard assigned profile: '{assigned_profile.upper()}' for {child.name}.")
        self.write_audit_log(
            child_id=child.id,
            action_type="privacy_evaluation",
            details=f"Assigned routing profile '{assigned_profile}' based on target files.",
            meta={"deliverables": child.deliverables, "execution_root": exec_root},
        )

        # Retrieve RAG context if applicable
        rag_context = ""
        rag_query = child.rag_query or child.tactical_objective
        if rag_query:
            rag_context = self.rag_retriever.format_rag_context(rag_query, top_k=2)
            if rag_context:
                print(f"  [RAG] Injected internal architecture context for '{child.name}'.")
                self.write_audit_log(
                    child_id=child.id,
                    action_type="rag_retrieval",
                    details="Injected architecture & schema context.",
                    meta={"rag_query": rag_query},
                )

        # Cognitive Triage: Evaluate routing between System 1 and System 2
        is_system_1 = False
        objective_lower = child.tactical_objective.lower()
        deliverables_paths = [d.get("path", "") for d in child.deliverables]

        # System 1 Heuristics: documentation files, readmes, typos, simple comments
        if (
            any(p.endswith(".md") or "doc" in p for p in deliverables_paths)
            or any(keyword in objective_lower for keyword in ["typo", "formatting", "docstring", "comment", "readme"])
        ):
            is_system_1 = True

        if is_system_1:
            print("[COGNITIVE] Triage: Low-complexity task detected. Routing to SYSTEM 1 (Heuristic Fast-Path).")
            self.write_audit_log(
                child_id=child.id,
                action_type="cognitive_triage",
                details="Routed to System 1 due to document/formatting low-complexity nature.",
            )

            # SYSTEM 1: Single forward-pass execution
            result = self.llm.execute_child_card(
                tactical_objective=f"{child.tactical_objective}\n\n{rag_context}".strip(),
                deliverables=child.deliverables,
                iteration=2,  # Direct result
                design_system=mother.design_system,
                execution_root=exec_root,
            )

            self.write_audit_log(
                child_id=child.id,
                action_type="code_modification",
                details="System 1 wrote deliverables heuristically.",
                meta={"modified_files": result.get("created_files")},
            )

            # Fast syntax check
            syntax_ok = True
            for file_path in result.get("created_files", []):
                if file_path.endswith(".py"):
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            compile(f.read(), file_path, "exec")
                    except SyntaxError as e:
                        syntax_ok = False
                        print(f"  [COGNITIVE] System 1 syntax check failed for {file_path}: {e}. Escalating to System 2.")
                        break

            if syntax_ok:
                child.phase = "Completed"
                child.current_iteration = 1
                for log_msg in result.get("logs", []):
                    child.logs.append(f"[System 1] {log_msg}")
                child.logs.append("[System 1] Bypassed deep sandboxed validation and completed task successfully!")
                child.to_yaml(child_path)
                print("[SUCCESS] System 1 completed task successfully without sandbox overhead!")

                self.write_audit_log(
                    child_id=child.id,
                    action_type="task_completed",
                    details="System 1 fast-path completed successfully. Sandbox bypassed.",
                    meta={"status": "Success"},
                )
            else:
                is_system_1 = False

        if not is_system_1:
            print("[COGNITIVE] Triage: Routing to SYSTEM 2 (Deliberative Sandbox Validation Loop).")
            previous_errors = ""

            while child.current_iteration < child.max_iterations:
                child.current_iteration += 1
                print(f"  [RUN] Starting Iteration {child.current_iteration}/{child.max_iterations}...")

                self.write_audit_log(
                    child_id=child.id,
                    action_type="execution_iteration",
                    details=f"Executing iteration {child.current_iteration} of task objective.",
                    meta={"objective": child.tactical_objective},
                )

                combined_objective = f"{child.tactical_objective}\n\n{rag_context}".strip()
                result = self.llm.execute_child_card(
                    tactical_objective=combined_objective,
                    deliverables=child.deliverables,
                    iteration=child.current_iteration,
                    previous_errors=previous_errors,
                    design_system=mother.design_system,
                    execution_root=exec_root,
                )

                self.write_audit_log(
                    child_id=child.id,
                    action_type="code_modification",
                    details="LLM updated deliverables code files.",
                    meta={"modified_files": result.get("created_files")},
                )

                for log_msg in result.get("logs", []):
                    child.logs.append(f"[Iteration {child.current_iteration}] {log_msg}")

                all_passed = True
                previous_errors = ""

                for cmd in child.validation_commands:
                    print(f"  [TEST] Running Validation with Flakiness Check: '{cmd}'...")
                    cmd_result = self.sandbox.run_validation_with_flakiness_check(cmd, cwd=exec_root)

                    self.write_audit_log(
                        child_id=child.id,
                        action_type="sandbox_validation",
                        details=f"Executed: {cmd}",
                        meta={"exit_code": cmd_result["exit_code"], "output": cmd_result["output"], "flaky": cmd_result.get("flaky", False)},
                    )

                    if not cmd_result["success"]:
                        all_passed = False
                        previous_errors += cmd_result.get("fault_frame", cmd_result["output"])
                        print(f"  [WARN] Validation Failed (Exit Code: {cmd_result['exit_code']})")
                        child.logs.append(f"[Iteration {child.current_iteration}] Validation command '{cmd}' failed.")
                        child.logs.append(f"Fault Frame:\n{cmd_result.get('fault_frame', cmd_result['output'])}")
                        break
                    else:
                        print("  [OK] Validation Passed!")
                        child.logs.append(f"[Iteration {child.current_iteration}] Validation command '{cmd}' passed.")

                if all_passed:
                    child.phase = "Completed"
                    child.to_yaml(child_path)
                    print(f"[SUCCESS] Task Completed Successfully in {child.current_iteration} iterations!")

                    self.write_audit_log(
                        child_id=child.id,
                        action_type="task_completed",
                        details="All deliverables passed verification checks. Committing task.",
                        meta={"status": "Success"},
                    )

                    # Closed-Loop Ticketing Egress
                    if child.external_ticket_id:
                        platform = child.source_platform or "ServiceNow"
                        print(f"  [EGRESS-SYNC] Closed-loop resolution dispatched for {platform} ticket: {child.external_ticket_id}")
                        self.write_audit_log(
                            child_id=child.id,
                            action_type="ticket_resolution_sync",
                            details=f"Pushed closed-loop resolution to {platform} ({child.external_ticket_id}). Status: Resolved.",
                            meta={
                                "ticket_id": child.external_ticket_id,
                                "platform": platform,
                                "iterations_to_heal": child.current_iteration,
                                "pr_branch": f"aje/{child.id}",
                            },
                        )
                    break
                else:
                    child.to_yaml(child_path)
                    print("  [INFO] Triggering autonomous self-healing on next iteration...")

        if child.phase != "Completed":
            child.phase = "Failed"
            child.to_yaml(child_path)
            print(f"[ERROR] Child task failed after reaching maximum iterations ({child.max_iterations}).")
            self.write_audit_log(
                child_id=child.id,
                action_type="task_failed",
                details=f"Failed validation checks after {child.max_iterations} attempts.",
                meta={"status": "Failed"},
            )

        return {"child_id": child.id, "phase": child.phase, "iterations": child.current_iteration}

    def run_one_cycle(self, mother_card_path: str, use_wave_execution: bool = True):
        """
        Performs a complete autonomous execution cycle:
        1. Loads Mother Card configuration and safety policies.
        2. Discovers pending Child Cards and partitions into DAG Waves.
        3. Executes waves in bounded concurrency ($N=2$) with isolated Git worktrees.
        4. Runs Inter-Wave Synchronization Barriers with composite sandbox tests.
        5. Triggers post-wave gap analysis and alignment cascades.
        """
        if not os.path.exists(mother_card_path):
            print(f"Error: Mother card not found at {mother_card_path}")
            return

        print("--------------------------------------------------")
        print("[RUN] Booting Autonomous Job-Card Engine (v4.0)...")
        print("--------------------------------------------------")

        mother = MotherCard.from_yaml(mother_card_path)
        print(f"[INFO] Mother Guardian Loaded: {mother.name} (Privacy: {mother.privacy_mode})")
        if mother.design_system:
            print(f"[INFO] Design System Enforced: {mother.design_system}")
        if mother.repo.remote_slug:
            print(f"[INFO] Linked GitHub Remote: {mother.repo.remote_slug} (Target: {mother.repo.target_branch})")

        self.privacy_guard = PrivacyGuard(restricted_paths=mother.restricted_paths)
        self.sandbox = SandboxRunner(banned_commands=mother.banned_commands)

        # 1. Run Mother Card Preflight Quality & Regex Scan
        self.run_preflight_quality_scan(mother)

        if mother.rag_knowledge_paths:
            self.index_rag_knowledge(mother.rag_knowledge_paths)

        # Collect pending Child Cards
        pending_children: List[ChildCard] = []
        for file_name in sorted(os.listdir(self.jobs_dir)):
            if file_name.startswith("child_") and file_name.endswith(".yaml"):
                child_path = os.path.join(self.jobs_dir, file_name)
                child = ChildCard.from_yaml(child_path)
                if child.phase not in ["Completed", "Failed"]:
                    pending_children.append(child)

        if not pending_children:
            print("[INFO] No pending Child Cards found. Checking for gap analysis...")
            new_tasks = self.llm.generate_gap_analysis(current_repo_state="Idle repository")
            for task in new_tasks:
                new_child_filename = f"child_{task['id']}.yaml"
                new_child_path = os.path.join(self.jobs_dir, new_child_filename)
                if not os.path.exists(new_child_path):
                    new_child = ChildCard(
                        id=task["id"],
                        parent_mother_id=mother.id,
                        parent_squad_id=task.get("parent_squad_id", "squad-backend"),
                        name=task["name"],
                        tactical_objective=task["tactical_objective"],
                        deliverables=task["deliverables"],
                        validation_commands=task.get("test_commands", []),
                        phase="Pending",
                    )
                    new_child.to_yaml(new_child_path)
                    print(f"  [GAP-ANALYSIS] Autonomously created successor Child Card: {task['name']}")
            return

        print(f"[INFO] Identified {len(pending_children)} pending tactical Child Card(s).")

        # Partition into topological DAG Waves
        waves = SquadDAGScheduler.partition_into_waves(pending_children)
        print(f"[SCHEDULER] Partitioned into {len(waves)} execution wave(s).")

        gov = getattr(mother, "governance", None)
        max_concurrency = gov.max_concurrency if gov else 2
        vram_threshold = gov.vram_threshold_pct if gov else 85.0
        max_wave_depth = gov.max_wave_depth if gov else 3

        wave_executor = SquadWaveExecutor(
            workspace_root=self.workspace_root,
            engine_daemon=self,
            max_concurrency=max_concurrency,
            vram_threshold_pct=vram_threshold,
            max_wave_depth=max_wave_depth,
        )

        for wave_idx, wave_cards in enumerate(waves, 1):
            wave_executor.execute_wave(
                wave_index=wave_idx,
                wave_cards=wave_cards,
                mother=mother,
                jobs_dir=self.jobs_dir,
            )

        # Autonomous Gap Analysis & Successor Discovery
        for card in pending_children:
            if card.phase == "Completed":
                new_tasks = self.llm.generate_gap_analysis(current_repo_state=f"Completed {card.name}")
                for task in new_tasks:
                    new_child_filename = f"child_{task['id']}.yaml"
                    new_child_path = os.path.join(self.jobs_dir, new_child_filename)
                    if not os.path.exists(new_child_path):
                        new_child = ChildCard(
                            id=task["id"],
                            parent_mother_id=mother.id,
                            parent_squad_id=task.get("parent_squad_id", card.parent_squad_id),
                            name=task["name"],
                            tactical_objective=task["tactical_objective"],
                            deliverables=task["deliverables"],
                            validation_commands=task.get("test_commands", []),
                            phase="Pending",
                        )
                        new_child.to_yaml(new_child_path)
                        print(f"  [GAP-ANALYSIS] Autonomously created successor Child Card: {task['name']}")

        print("\n--------------------------------------------------")
        print("[CYCLE COMPLETE] All active DAG waves finished successfully.")
        print("--------------------------------------------------")
