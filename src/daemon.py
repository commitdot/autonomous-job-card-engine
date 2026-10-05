import os
import time
import json
from datetime import datetime
from typing import List, Dict, Any

from .models import MotherCard, ChildCard
from .privacy_guard import PrivacyGuard
from .sandbox import SandboxRunner
from .sim_llm import SimLLM

class AutonomousJobCardEngine:
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.jobs_dir = os.path.join(workspace_root, ".jobs")
        self.audit_dir = os.path.join(self.jobs_dir, "audit")
        
        os.makedirs(self.workspace_root, exist_ok=True)
        os.makedirs(self.jobs_dir, exist_ok=True)
        os.makedirs(self.audit_dir, exist_ok=True)

        # Initialize components
        self.privacy_guard = None
        self.sandbox = None
        self.llm = SimLLM(workspace_root=self.workspace_root)

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
                with open(audit_file, 'r') as f:
                    logs = json.load(f)
            except Exception:
                pass
        
        logs.append(entry)
        with open(audit_file, 'w') as f:
            json.dump(logs, f, indent=2)

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

        # Set up safety elements derived from Mother spec
        self.privacy_guard = PrivacyGuard(restricted_paths=mother.restricted_paths)
        self.sandbox = SandboxRunner(banned_commands=mother.banned_commands)

        # Find Child Cards
        for file_name in os.listdir(self.jobs_dir):
            if file_name.startswith("child_") and file_name.endswith(".yaml"):
                child_path = os.path.join(self.jobs_dir, file_name)
                child = ChildCard.from_yaml(child_path)

                if child.phase in ["Completed", "Failed"]:
                    continue # Skip processed children

                print(f"\n[INFO] Found Pending Child Card: {child.name} [ID: {child.id}]")
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
                        tactical_objective=child.tactical_objective,
                        deliverables=child.deliverables,
                        iteration=2 # Direct healed result
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
                                with open(file_path, 'r') as f:
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
                        is_system_1 = False # Escalates to standard System 2 loop below
                
                if not is_system_1:
                    print("[COGNITIVE] Triage: Routing to SYSTEM 2 (Deliberative Sandbox Validation Loop).")
                    
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

                        # Trigger simulated LLM code writing
                        result = self.llm.execute_child_card(
                            tactical_objective=child.tactical_objective,
                            deliverables=child.deliverables,
                            iteration=child.current_iteration
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
                            # Validation failed: write card yaml state and continue loop (triggers self-healing next iteration)
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
                    new_tasks = self.llm.generate_gap_analysis(current_repo_state="Auth system implemented.")
                    
                    for task in new_tasks:
                        new_child_filename = f"child_{task['id']}.yaml"
                        new_child_path = os.path.join(self.jobs_dir, new_child_filename)
                        
                        if not os.path.exists(new_child_path):
                            # Create new child card autonomously!
                            new_child = ChildCard(
                                id=task["id"],
                                parent_mother_id=mother.id,
                                name=task["name"],
                                tactical_objective=task["tactical_objective"],
                                deliverables=task["deliverables"],
                                validation_commands=task["test_commands"],
                                phase="Pending"
                            )
                            new_child.to_yaml(new_child_path)
                            print(f"[NEW] Mother autonomously scheduled successor: {new_child.name} [ID: {new_child.id}]")
                            mother.backlog_queue.append({"id": task["id"], "name": task["name"]})
                            
                            self.write_audit_log(
                                child_id=child.id,
                                action_type="autonomous_discovery",
                                details=f"Autonomously generated successor card: {new_child.name}",
                                meta={"new_child_id": new_child.id}
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
