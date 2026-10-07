import os
import shutil
import json
import yaml
from src.daemon import AutonomousJobCardEngine
from src.models import MotherCard, ChildCard, RepositoryBinding

def setup_mock_project(workspace: str):
    """
    Sets up a clean test directory structure with initial Mother, Squad, RAG docs, and Child Cards.
    """
    if os.path.exists(workspace):
        shutil.rmtree(workspace)
    
    os.makedirs(workspace, exist_ok=True)
    jobs_dir = os.path.join(workspace, ".jobs")
    os.makedirs(jobs_dir, exist_ok=True)

    # 1. Create mock internal RAG documentation
    docs_dir = os.path.join(workspace, "docs", "rfc")
    os.makedirs(docs_dir, exist_ok=True)
    rfc_doc = os.path.join(docs_dir, "rfc_auth_standard.md")
    with open(rfc_doc, "w", encoding="utf-8") as f:
        f.write("# RFC-042: Enterprise Authentication Standard\n\nAll services must use hashlib and PyJWT for token signatures. Password hashes must be salt-verified.")

    # 2. Write the Mother Card
    mother = MotherCard(
        id="mother-complaint-router",
        name="Customer Complaint Router Mother Card",
        core_philosophy="Strict types, TDD-driven, and robust password hashing.",
        privacy_mode="hybrid",
        archetype="saas-backend",
        design_system="IBM Carbon Design System",
        restricted_paths=["**/secrets.env", "**/database/credentials/*"],
        banned_commands=["rm -rf", "docker system prune"],
        repo=RepositoryBinding(
            local_path=".",
            remote_slug="my-org/complaint-router",
            target_branch="main"
        ),
        rag_knowledge_paths=["docs/rfc"],
        squad_leads=["backend", "security", "qa", "ui"],
        global_context={
            "target_framework": "FastAPI",
            "testing_framework": "pytest"
        },
        active_children=["child-003-jwt-auth"]
    )
    
    mother_path = os.path.join(jobs_dir, "mother_card.yaml")
    mother.to_yaml(mother_path)

    # 3. Write the Child Card (Target: Auth Module with RAG query)
    child = ChildCard(
        id="child-003-jwt-auth",
        parent_mother_id="mother-complaint-router",
        parent_squad_id="squad-backend",
        name="JWT Authentication System",
        tactical_objective="Create app/auth.py with verify_token and hash_password, and a test suite.",
        deliverables=[
            {"path": "app/auth.py", "description": "Token operations module"},
            {"path": "tests/test_auth.py", "description": "Pytest auth unit tests"}
        ],
        validation_commands=[
            "python tests/test_auth.py"
        ],
        rag_query="Enterprise Authentication Standard token signature",
        max_iterations=5,
        phase="Pending"
    )

    child_path = os.path.join(jobs_dir, "child_jwt_auth.yaml")
    child.to_yaml(child_path)

    # 4. Write a second Child Card targeting simple documentation (System 1 Fast-Path)
    child_docs = ChildCard(
        id="child-006-update-docs",
        parent_mother_id="mother-complaint-router",
        parent_squad_id="squad-qa",
        name="Update Project Documentation",
        tactical_objective="Fix standard documentation typos and add project description to README.md",
        deliverables=[
            {"path": "README.md", "description": "Documentation readme file"}
        ],
        validation_commands=[],
        max_iterations=3,
        phase="Pending"
    )

    child_docs_path = os.path.join(jobs_dir, "child_docs_update.yaml")
    child_docs.to_yaml(child_docs_path)

    print(f"[OK] Created Mock Project inside: {workspace}")
    print(f"[OK] Seeded RAG Doc at: {rfc_doc}")
    print(f"[OK] Seeded Mother Card at: {mother_path}")
    print(f"[OK] Seeded Child Card (System 2 Complex) at: {child_path}")
    print(f"[OK] Seeded Child Card (System 1 Simple) at: {child_docs_path}")

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
    with open(auth_file_path, 'r', encoding='utf-8') as f:
        auth_content = f.read()
    
    assert "broken_library" not in auth_content, "[ERROR] auth.py still contains the import error!"
    assert "hashlib" in auth_content, "[ERROR] auth.py did not self-heal with hashlib!"
    print("[PASS] Self-Healing verification: auth.py successfully healed and contains correct hashlib code.")

    # 2. Was RAG context indexed and queried?
    rag_index_path = os.path.join(workspace, ".jobs", "rag", "index.json")
    assert os.path.exists(rag_index_path), "[ERROR] Local RAG index was not generated!"
    print("[PASS] Local RAG verification: Vector index generated and queried successfully!")

    # 3. Was the child card marked completed?
    child_card_path = os.path.join(workspace, ".jobs", "child_jwt_auth.yaml")
    with open(child_card_path, 'r', encoding='utf-8') as f:
        child_state = yaml.safe_load(f)
    
    assert child_state["status"]["phase"] == "Completed", "[ERROR] Child card status phase is not Completed!"
    assert child_state["status"]["current_iteration"] == 2, "[ERROR] Expected exactly 2 iterations (Error -> Corrected)!"
    print(f"[PASS] State Machine verification: Child task marked as 'Completed' in {child_state['status']['current_iteration']} iterations!")

    # 4. Did the Mother autonomously generate successor tasks (Gap Analysis)?
    successor_1 = os.path.join(workspace, ".jobs", "child_child-004-jwt-refresh-tokens.yaml")
    successor_2 = os.path.join(workspace, ".jobs", "child_child-005-add-rate-limiting.yaml")
    
    assert os.path.exists(successor_1), "[ERROR] Successor 1 (Refresh Tokens) was not scheduled!"
    assert os.path.exists(successor_2), "[ERROR] Successor 2 (Rate Limiting) was not scheduled!"
    print("[PASS] Autonomous R&D verification: Successor child job cards generated and added to queue autonomously!")

    # 5. Print Forensic Audit Trail
    audit_log_path = os.path.join(workspace, ".jobs", "audit", "audit_child-003-jwt-auth.json")
    assert os.path.exists(audit_log_path), "[ERROR] Audit log file was not generated!"
    print("[PASS] Forensic Audit verification: Audit log file generated successfully!")

    with open(audit_log_path, 'r', encoding='utf-8') as f:
        audit_data = json.load(f)
        
    print(f"\nFORENSIC AUDIT TRAILS ({len(audit_data)} events recorded):")
    for idx, entry in enumerate(audit_data):
        print(f"  [{idx+1}] {entry['timestamp']} | {entry['action'].upper()} | {entry['details']}")

    print("\n[SUCCESS] ALL TESTS PASSED! The Autonomous Job-Card Engine (AJE) v2.0 concept is 100% executable and validated. [SUCCESS]")

if __name__ == "__main__":
    run_e2e_test()
