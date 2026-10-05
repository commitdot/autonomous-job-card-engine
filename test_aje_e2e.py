import os
import shutil
import json
import yaml
from src.daemon import AutonomousJobCardEngine

def setup_mock_project(workspace: str):
    """
    Sets up a clean test directory structure with initial Mother and Child Cards.
    """
    if os.path.exists(workspace):
        shutil.rmtree(workspace)
    
    os.makedirs(workspace, exist_ok=True)
    jobs_dir = os.path.join(workspace, ".jobs")
    os.makedirs(jobs_dir, exist_ok=True)

    # 1. Write the Mother Card
    mother_data = {
        "apiVersion": "agent.autonomous.io/v1alpha1",
        "kind": "MotherCard",
        "metadata": {
            "id": "mother-complaint-router",
            "name": "Customer Complaint Router Mother Card"
        },
        "spec": {
            "core_philosophy": "Strict types, TDD-driven, and robust password hashing.",
            "safety_policies": {
                "privacy_mode": "hybrid",
                "restricted_paths": ["**/secrets.env", "**/database/credentials/*"],
                "banned_commands": ["rm -rf", "docker system prune"]
            },
            "global_context": {
                "target_framework": "FastAPI",
                "testing_framework": "pytest"
            }
        },
        "status": {
            "family_health": "Healthy",
            "total_spend_usd": 0.0,
            "active_children": ["child-003-jwt-auth"],
            "completed_children": [],
            "backlog_queue": []
        }
    }
    
    mother_path = os.path.join(jobs_dir, "mother_card.yaml")
    with open(mother_path, 'w') as f:
        yaml.safe_dump(mother_data, f, default_flow_style=False)

    # 2. Write the Child Card (Target: Auth Module)
    child_data = {
        "apiVersion": "agent.autonomous.io/v1alpha1",
        "kind": "ChildCard",
        "metadata": {
            "id": "child-003-jwt-auth",
            "parent_mother_id": "mother-complaint-router",
            "name": "JWT Authentication System"
        },
        "spec": {
            "tactical_objective": "Create app/auth.py with verify_token and hash_password, and a test suite.",
            "max_iterations": 5,
            "deliverables": [
                {"path": "app/auth.py", "description": "Token operations module"},
                {"path": "tests/test_auth.py", "description": "Pytest auth unit tests"}
            ],
            "validation": {
                "test_commands": [
                    "python tests/test_auth.py"
                ]
            }
        },
        "status": {
            "phase": "Pending",
            "current_iteration": 0,
            "execution_profile_assigned": "local",
            "logs": []
        }
    }

    child_path = os.path.join(jobs_dir, "child_jwt_auth.yaml")
    with open(child_path, 'w') as f:
        yaml.safe_dump(child_data, f, default_flow_style=False)

    print(f"[OK] Created Mock Project inside: {workspace}")
    print(f"[OK] Seeded Mother Card at: {mother_path}")
    print(f"[OK] Seeded Child Card at: {child_path}")

def run_e2e_test():
    workspace = "test_workspace"
    setup_mock_project(workspace)

    # Instantiate the Autonomous Job-Card Engine
    engine = AutonomousJobCardEngine(workspace_root=workspace)
    
    # Run the orchestrator loop
    mother_card_path = os.path.join(workspace, ".jobs", "mother_card.yaml")
    engine.run_one_cycle(mother_card_path)

    # --- VERIFICATION ---
    print("\n==================================================")
    print("VERIFYING TEST OUTCOMES & AUTONOMOUS BEHAVIOR")
    print("==================================================")

    # 1. Did the engine edit the files?
    auth_file_path = os.path.join(workspace, "app", "auth.py")
    test_file_path = os.path.join(workspace, "tests", "test_auth.py")
    
    assert os.path.exists(auth_file_path), "[ERROR] app/auth.py was not created!"
    assert os.path.exists(test_file_path), "[ERROR] tests/test_auth.py was not created!"
    print("[PASS] Deliverable verification: Real code files generated inside workspace successfully!")

    # Read auth.py to ensure the final implementation is corrected/self-healed
    with open(auth_file_path, 'r') as f:
        auth_content = f.read()
    
    assert "broken_library" not in auth_content, "[ERROR] auth.py still contains the import error!"
    assert "hashlib" in auth_content, "[ERROR] auth.py did not self-heal with hashlib!"
    print("[PASS] Self-Healing verification: auth.py successfully healed and contains correct hashlib code.")

    # 2. Was the child card marked completed?
    child_card_path = os.path.join(workspace, ".jobs", "child_jwt_auth.yaml")
    with open(child_card_path, 'r') as f:
        child_state = yaml.safe_load(f)
    
    assert child_state["status"]["phase"] == "Completed", "[ERROR] Child card status phase is not Completed!"
    assert child_state["status"]["current_iteration"] == 2, "[ERROR] Expected exactly 2 iterations (Error -> Corrected)!"
    print(f"[PASS] State Machine verification: Child task marked as 'Completed' in {child_state['status']['current_iteration']} iterations!")

    # 3. Did the Mother autonomously generate successor tasks (Gap Analysis)?
    successor_1 = os.path.join(workspace, ".jobs", "child_child-004-jwt-refresh-tokens.yaml")
    successor_2 = os.path.join(workspace, ".jobs", "child_child-005-add-rate-limiting.yaml")
    
    assert os.path.exists(successor_1), "[ERROR] Successor 1 (Refresh Tokens) was not scheduled!"
    assert os.path.exists(successor_2), "[ERROR] Successor 2 (Rate Limiting) was not scheduled!"
    print("[PASS] Autonomous R&D verification: Successor child job cards generated and added to queue autonomously!")

    # 4. Print Forensic Audit Trail
    audit_log_path = os.path.join(workspace, ".jobs", "audit", "audit_child-003-jwt-auth.json")
    assert os.path.exists(audit_log_path), "[ERROR] Audit log file was not generated!"
    print("[PASS] Forensic Audit verification: Audit log file generated successfully!")

    with open(audit_log_path, 'r') as f:
        audit_data = json.load(f)
        
    print(f"\nFORENSIC AUDIT TRAILS ({len(audit_data)} events recorded):")
    for idx, entry in enumerate(audit_data):
        print(f"  [{idx+1}] {entry['timestamp']} | {entry['action'].upper()} | {entry['details']}")

    print("\n[SUCCESS] ALL TESTS PASSED! The Autonomous Job-Card Engine (AJE) concept is 100% executable and plausible. [SUCCESS]")

if __name__ == "__main__":
    run_e2e_test()
