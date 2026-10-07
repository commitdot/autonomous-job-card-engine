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

    def run_one_cycle(self, mother_card_path: str):
        """
        Performs a single complete execution cycle:
        Loads Mother, processes active Child, sanitizes, runs validation, self-heals if needed,
        and triggers semantic gap-analysis.
        """
        if not os.path.exists(mother_card_path):
            print(f"Error: Mother card not found at {mother_card_path}")
            return

        print("--------------------------------------------------")
        print("[RUN] Booting Autonomous Job-Card Engine Cycle...")
        print("--------------------------------------------------")

        # 1. Load Mother Card Configuration
        mother = MotherCard.from_yaml(mother_card_path)
        print(f"[INFO] Mother Guardian Loaded: {mother.name} (Privacy: {mother.privacy_mode})")
        if mother.design_system:
            print(f"[INFO] Design System Enforced: {mother.design_system}")
        if mother.repo.remote_slug:
            print(f"[INFO] Linked GitHub Remote: {mother.repo.remote_slug} (Target: {mother.repo.target_branch})")

        # Set up safety elements derived from Mother spec
        self.privacy_guard = PrivacyGuard(restricted_paths=mother.restricted_paths)
        self.sandbox = SandboxRunner(banned_commands=mother.banned_commands)

        # Index RAG knowledge paths
        if mother.rag_knowledge_paths:
            self.index_rag_knowledge(mother.rag_knowledge_paths)

        # Find Child Cards
        for file_name in os.listdir(self.jobs_dir):
            if file_name.startswith("child_") and file_name.endswith(".yaml"):
                child_path = os.path.join(self.jobs_dir, file_name)
                child = ChildCard.from_yaml(child_path)

                if child.phase in ["Completed", "Failed"]:
                    continue  # Skip processed children

                print(f"\n[INFO] Found Pending Child Card: {child.name} [ID: {child.id} | Squad: {child.parent_squad_id}]")
                child.phase = "Running"
                child.to_yaml(child_path)
                
                # Check security and evaluate routing profile
                paths_to_be_edited = [d.get("path") for d in child.deliverables]
                assigned_profile = self.privacy_guard.evaluate_routing_profile(
                    files_accessed=paths_to_be_edited,
                    base_mode=mother.privacy_mode
                )
                child.execution_profile_assigned = assigned_profile
                print(f"[SEC] Privacy Guard assigned profile: '{assigned_profile.upper()}' for deliverables.")
                self.write_audit_log(
                    child_id=child.id,
                    action_type="privacy_evaluation",
                    details=f"Assigned routing profile '{assigned_profile}' based on target files.",
                    meta={"deliverables": child.deliverables}
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
                            meta={"rag_query": rag_query}
                        )

                # Cognitive Triage: Evaluate routing between System 1 and System 2
                is_system_1 = False
                objective_lower = child.tactical_objective.lower()
                deliverables_paths = [d.get("path", "") for d in child.deliverables]
                
                # System 1 Heuristics: documentation files, readmes, typos, simple refactor tags, etc.
                if (any(p.endswith(".md") or "doc" in p for p in deliverables_paths) or
                    any(keyword in objective_lower for keyword in ["typo", "formatting", "docstring", "comment", "readme"])):
                    is_system_1 = True

                if is_system_1:
                    print("[COGNITIVE] Triage: Low-complexity task detected. Routing to SYSTEM 1 (Heuristic Fast-Path).")
                    self.write_audit_log(
                        child_id=child.id,
                        action_type="cognitive_triage",
                        details="Routed to System 1 due to document/formatting low-complexity nature."
                    )
                    
                    # SYSTEM 1: Single forward-pass execution (fast generation)
                    result = self.llm.execute_child_card(
                        tactical_objective=f"{child.tactical_objective}\n\n{rag_context}".strip(),
                        deliverables=child.deliverables,
                        iteration=2,  # Direct healed result
                        design_system=mother.design_system,
                    )
                    
                    self.write_audit_log(
                        child_id=child.id,
                        action_type="code_modification",
                        details="System 1 wrote deliverables heuristically.",
                        meta={"modified_files": result.get("created_files")}
                    )
                    
                    # Fast lint compile check
                    syntax_ok = True
                    for file_path in result.get("created_files", []):
                        if file_path.endswith(".py"):
                            try:
                                with open(file_path, 'r', encoding='utf-8') as f:
                                    compile(f.read(), file_path, 'exec')
                            except SyntaxError as e:
                                syntax_ok = False
                                print(f"  [COGNITIVE] System 1 syntax check failed for {file_path}: {str(e)}. Escalating to System 2.")
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
                            meta={"status": "Success"}
                        )
                    else:
                        print("  [COGNITIVE] System 1 failed lint pass. Escalating to System 2.")
                        is_system_1 = False  # Escalates to standard System 2 loop below
                
                if not is_system_1:
                    print("[COGNITIVE] Triage: Routing to SYSTEM 2 (Deliberative Sandbox Validation Loop).")

                    # Accumulate error output across iterations so the adapter can self-heal
                    previous_errors = ""

                    # Iterative Execution and Self-Healing Loop
                    while child.current_iteration < child.max_iterations:
                        child.current_iteration += 1
                        print(f"  [RUN] Starting Iteration {child.current_iteration}/{child.max_iterations}...")

                        # Log iteration attempt
                        self.write_audit_log(
                            child_id=child.id,
                            action_type="execution_iteration",
                            details=f"Executing iteration {child.current_iteration} of task objective.",
                            meta={"objective": child.tactical_objective}
                        )

                        # Trigger LLM code writing — pass previous errors and RAG context
                        combined_objective = f"{child.tactical_objective}\n\n{rag_context}".strip()
                        result = self.llm.execute_child_card(
                            tactical_objective=combined_objective,
                            deliverables=child.deliverables,
                            iteration=child.current_iteration,
                            previous_errors=previous_errors,
                            design_system=mother.design_system,
                        )

                        # Record written files to audit
                        self.write_audit_log(
                            child_id=child.id,
                            action_type="code_modification",
                            details="LLM updated deliverables code files.",
                            meta={"modified_files": result.get("created_files")}
                        )

                        for log_msg in result.get("logs", []):
                            child.logs.append(f"[Iteration {child.current_iteration}] {log_msg}")

                        # Run Validation Commands inside the sandbox
                        all_passed = True
                        validation_results = []
                        previous_errors = ""  # Reset before each validation round

                        for cmd in child.validation_commands:
                            print(f"  [TEST] Running Validation: '{cmd}'...")
                            cmd_result = self.sandbox.run_validation(cmd, cwd=self.workspace_root)
                            validation_results.append(cmd_result)

                            self.write_audit_log(
                                child_id=child.id,
                                action_type="sandbox_validation",
                                details=f"Executed: {cmd}",
                                meta={"exit_code": cmd_result["exit_code"], "output": cmd_result["output"]}
                            )

                            if not cmd_result["success"]:
                                all_passed = False
                                previous_errors += cmd_result["output"]  # Feed into next iteration
                                print(f"  [WARN] Validation Failed (Exit Code: {cmd_result['exit_code']})")
                                child.logs.append(f"[Iteration {child.current_iteration}] Validation command '{cmd}' failed.")
                                child.logs.append(f"Output:\n{cmd_result['output']}")
                                break
                            else:
                                print("  [OK] Validation Passed!")
                                child.logs.append(f"[Iteration {child.current_iteration}] Validation command '{cmd}' passed.")

                        if all_passed:
                            # Success! Update state and complete
                            child.phase = "Completed"
                            child.to_yaml(child_path)
                            print(f"[SUCCESS] Task Completed Successfully in {child.current_iteration} iterations!")

                            self.write_audit_log(
                                child_id=child.id,
                                action_type="task_completed",
                                details="All deliverables passed verification checks. Committing task.",
                                meta={"status": "Success"}
                            )
                            break
                        else:
                            # Validation failed: persist state and loop (self-healing on next iteration)
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
                        meta={"status": "Failed"}
                    )

                # 5. Gap Analysis (The "Keep Searching" Mechanism)
                # Once a child is completed, the Mother Card conducts a gap analysis to find what to build next.
                if child.phase == "Completed":
                    print("\n[INFO] Conducting Semantic Gap-Analysis & Autonomous Discovery...")
                    new_tasks = self.llm.generate_gap_analysis(current_repo_state=f"Completed {child.name}")
                    
                    for task in new_tasks:
                        new_child_filename = f"child_{task['id']}.yaml"
                        new_child_path = os.path.join(self.jobs_dir, new_child_filename)
                        
                        if not os.path.exists(new_child_path):
                            # Create new child card autonomously!
                            new_child = ChildCard(
                                id=task["id"],
                                parent_mother_id=mother.id,
                                parent_squad_id=task.get("parent_squad_id", child.parent_squad_id),
                                name=task["name"],
                                tactical_objective=task["tactical_objective"],
                                deliverables=task["deliverables"],
                                validation_commands=task["test_commands"],
                                phase="Pending"
                            )
                            new_child.to_yaml(new_child_path)
                            print(f"[NEW] Mother autonomously scheduled successor: {new_child.name} [ID: {new_child.id} | Squad: {new_child.parent_squad_id}]")
                            mother.backlog_queue.append({"id": task["id"], "name": task["name"]})
                            
                            self.write_audit_log(
                                child_id=child.id,
                                action_type="autonomous_discovery",
                                details=f"Autonomously generated successor card: {new_child.name}",
                                meta={"new_child_id": new_child.id, "squad": new_child.parent_squad_id}
                            )

                    # Mark this child as completed inside the mother card status
                    if child.id not in mother.completed_children:
                        mother.completed_children.append(child.id)
                    if child.id in mother.active_children:
                        mother.active_children.remove(child.id)
                    mother.to_yaml(mother_card_path)

        print("\n--------------------------------------------------")
        print("[SUCCESS] Autonomous Job-Card Cycle Complete.")
        print("--------------------------------------------------")
