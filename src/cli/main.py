"""
AJE CLI Main Entry Point
========================
Supports:
  aje init      — Interactive project setup wizard & template selector
  aje start     — Run autonomous engine with live rich TUI dashboard
  aje inject    — Manually inject a feature request or bug ticket
  aje status    — Summary of Mother health, active squad, and pending backlog
  aje link      — Configure local and remote GitHub repository bindings
"""

import sys
import os
import argparse
import yaml
import glob
from pathlib import Path

from ..models import MotherCard, ChildCard, RepositoryBinding, GovernancePolicies
from ..daemon import AutonomousJobCardEngine


def cmd_init(args):
    print("=" * 70)
    print("  🤖 AUTONOMOUS JOB-CARD ENGINE (AJE) — PROJECT SETUP WIZARD")
    print("=" * 70)

    workspace = os.path.abspath(args.workspace or ".")
    jobs_dir = os.path.join(workspace, ".jobs")
    os.makedirs(jobs_dir, exist_ok=True)
    os.makedirs(os.path.join(jobs_dir, "audit"), exist_ok=True)
    os.makedirs(os.path.join(jobs_dir, "rag"), exist_ok=True)

    print(f"\n[1/4] Workspace Location: {workspace}")
    project_name = input("  Project Name [Autonomous Project]: ").strip() or "Autonomous Project"
    
    print("\n[2/4] Link Remote GitHub Repository (Optional):")
    remote_slug = input("  GitHub Repo Slug (owner/repo) [none]: ").strip()
    target_branch = input("  Target Base Branch [main]: ").strip() or "main"

    print("\n[3/4] Select Mother Card Archetype:")
    print("  1) 🚀 SaaS Backend & API Modernizer (FastAPI / Carbon Design)")
    print("  2) 🛡️  Enterprise Security & Compliance Guardian (Air-Gapped Local)")
    print("  3) 🌐 Autonomous 24/7 Open-Source Maintainer (Public Repo / Material 3)")
    print("  4) 🎨 Frontend & Design System Enforcer (React / Tailwind)")
    print("  5) 🛠️  Custom Blank Canvas")
    choice = input("  Choose archetype [1]: ").strip() or "1"

    archetype_map = {
        "1": ("saas-backend", "IBM Carbon Design System", "Strict types, TDD-driven, OpenAPI 3.1 contracts."),
        "2": ("enterprise-security", "", "Air-gapped zero data egress, SOC2 compliance, CVE patching."),
        "3": ("opensource-maintainer", "Google Material Design 3", "Public repo maintainer, SemVer 2.0, 85%+ coverage."),
        "4": ("frontend-design", "IBM Carbon Design System", "WCAG 2.1 AA accessible, responsive components."),
        "5": ("custom", "", "Custom autonomous project.")
    }
    arch_type, design_sys, philosophy = archetype_map.get(choice, archetype_map["1"])

    mother = MotherCard(
        id=f"mother-{project_name.lower().replace(' ', '-')[:20]}",
        name=f"{project_name} Mother Card",
        core_philosophy=philosophy,
        archetype=arch_type,
        design_system=design_sys,
        privacy_mode="hybrid",
        repo=RepositoryBinding(
            local_path=".",
            remote_slug=remote_slug,
            target_branch=target_branch
        ),
        rag_knowledge_paths=["docs", "rfc", "schemas"],
        squad_leads=["backend", "security", "qa", "ui"],
        governance=GovernancePolicies(
            enable_repo_regex_scan=True,
            forbidden_patterns=[r"(?i)v2\.0", r"(?i)TODO:\s*urgent", r"(?i)(ghp_|sk-|AKIA)[a-zA-Z0-9]{20,}"],
            enable_ast_skeletonization=True,
            context_budget_chars=16000,
            enable_alignment_cascades=True,
            max_wave_depth=3,
            max_concurrency=2,
            vram_threshold_pct=85.0,
            dual_pass_flakiness_check=True,
            composite_integration_commands=["python -m unittest discover -s tests"]
        )
    )

    mother_path = os.path.join(jobs_dir, "mother_card.yaml")
    mother.to_yaml(mother_path)

    print("\n✔ Successfully created .jobs/mother_card.yaml")
    print("✔ Initialized .jobs/audit/ and .jobs/rag/")
    if remote_slug:
        print(f"✔ Linked to remote GitHub: {remote_slug}")
    print("\n✨ Setup complete! Run 'python -m src.cli.main start' to boot the engine.")


def cmd_status(args):
    workspace = os.path.abspath(args.workspace or ".")
    jobs_dir = os.path.join(workspace, ".jobs")
    mother_path = os.path.join(jobs_dir, "mother_card.yaml")

    if not os.path.exists(mother_path):
        print(f"No active Mother Card found at {mother_path}. Run 'aje init' first.")
        return

    mother = MotherCard.from_yaml(mother_path)
    print("┌" + "─" * 68 + "┐")
    print(f"│  🤖 AJE STATUS — {mother.name[:45]:<45} │")
    print("├" + "─" * 68 + "┤")
    print(f"│  Health: {mother.family_health:<15} Archetype: {mother.archetype:<20} │")
    print(f"│  Privacy Mode: {mother.privacy_mode:<10} Design System: {mother.design_system or 'None':<16} │")
    if mother.repo.remote_slug:
        print(f"│  GitHub Remote: {mother.repo.remote_slug:<49} │")
    print("├" + "─" * 68 + "┤")
    
    # Children cards
    child_files = glob.glob(os.path.join(jobs_dir, "child_*.yaml"))
    print(f"│  Task Backlog & Workers ({len(child_files)} total cards):{' ' * 32} │")
    for cf in child_files:
        try:
            c = ChildCard.from_yaml(cf)
            status_icon = "🟢" if c.phase == "Completed" else ("🟡" if c.phase == "Running" else "⚪")
            print(f"│   {status_icon} [{c.id}] {c.name[:32]:<32} ({c.phase}) │")
        except Exception:
            continue
    print("└" + "─" * 68 + "┘")


def cmd_inject(args):
    workspace = os.path.abspath(args.workspace or ".")
    jobs_dir = os.path.join(workspace, ".jobs")
    os.makedirs(jobs_dir, exist_ok=True)

    name = args.name or input("Task Title: ").strip()
    squad = args.squad or input("Assign to Squad (backend/security/qa/ui) [backend]: ").strip() or "squad-backend"
    objective = args.objective or input("Tactical Objective: ").strip()
    deliv_str = args.deliverables or input("Deliverable paths (comma-separated): ").strip()
    test_str = args.test or input("Validation command (e.g. pytest tests/): ").strip()
    rag_q = args.rag_query or input("RAG query (optional): ").strip() or None
    ticket_id = args.ticket or None
    platform = args.platform or ("ServiceNow" if ticket_id else None)

    deliverables = [{"path": p.strip(), "description": "Injected task deliverable"} for p in deliv_str.split(",") if p.strip()]
    validation_commands = [test_str.strip()] if test_str.strip() else []

    cid = f"child-manual-{name.lower().replace(' ', '-')[:18]}"
    card = ChildCard(
        id=cid,
        parent_mother_id="mother-saas-backend",
        parent_squad_id=squad if squad.startswith("squad-") else f"squad-{squad}",
        name=name,
        tactical_objective=objective,
        deliverables=deliverables,
        validation_commands=validation_commands,
        rag_query=rag_q,
        external_ticket_id=ticket_id,
        source_platform=platform,
        phase="Pending",
        logs=["Manually injected via CLI command 'aje inject'"]
    )
    
    card_path = os.path.join(jobs_dir, f"child_{cid}.yaml")
    card.to_yaml(card_path)
    print(f"\n✔ Successfully queued new Worker Card: {card.name} [ID: {card.id}]")
    print(f"  Saved to: {card_path}")


def cmd_start(args):
    workspace = os.path.abspath(args.workspace or ".")
    mother_path = os.path.join(workspace, ".jobs", "mother_card.yaml")

    if not os.path.exists(mother_path):
        print(f"No Mother Card found at {mother_path}. Initializing default...")
        cmd_init(args)

    engine = AutonomousJobCardEngine(workspace_root=workspace)
    print(f"\n🚀 Launching Autonomous Job-Card Engine on workspace: {workspace}")
    engine.run_one_cycle(mother_path)


def cmd_link(args):
    workspace = os.path.abspath(args.workspace or ".")
    mother_path = os.path.join(workspace, ".jobs", "mother_card.yaml")

    if not os.path.exists(mother_path):
        print(f"No active Mother Card found at {mother_path}. Run 'aje init' first.")
        return

    mother = MotherCard.from_yaml(mother_path)
    remote = args.remote or input(f"GitHub Repo Slug [{mother.repo.remote_slug or 'none'}]: ").strip()
    target_branch = args.branch or input(f"Target Branch [{mother.repo.target_branch}]: ").strip()

    if remote:
        mother.repo.remote_slug = remote
    if target_branch:
        mother.repo.target_branch = target_branch

    mother.to_yaml(mother_path)
    print(f"✔ Updated repository bindings for {mother.name}")
    print(f"  Remote: {mother.repo.remote_slug}")
    print(f"  Branch: {mother.repo.target_branch}")


def main():
    parser = argparse.ArgumentParser(description="Autonomous Job-Card Engine (AJE) CLI")
    parser.add_argument("--workspace", "-w", default=".", help="Workspace root directory")
    subparsers = parser.add_subparsers(dest="command", help="AJE commands")

    # init
    p_init = subparsers.add_parser("init", help="Interactive setup wizard")
    p_init.set_defaults(func=cmd_init)

    # status
    p_status = subparsers.add_parser("status", help="Show system status and task backlog")
    p_status.set_defaults(func=cmd_status)

    # inject
    p_inject = subparsers.add_parser("inject", help="Manually inject a feature or bug task")
    p_inject.add_argument("--name", help="Task name")
    p_inject.add_argument("--squad", help="Target squad (backend, security, qa, ui)")
    p_inject.add_argument("--objective", help="Tactical objective")
    p_inject.add_argument("--deliverables", help="Comma-separated deliverable paths")
    p_inject.add_argument("--test", help="Validation command")
    p_inject.add_argument("--rag-query", help="RAG context query")
    p_inject.add_argument("--ticket", help="External ticket or issue ID (e.g. GH-#14, INC0948201, SF-8492)")
    p_inject.add_argument("--platform", help="Source platform (GitHub, ServiceNow, Salesforce, Jira, Pega, Buganizer)")
    p_inject.set_defaults(func=cmd_inject)

    # start
    p_start = subparsers.add_parser("start", help="Run the autonomous engine loop")
    p_start.set_defaults(func=cmd_start)

    # link
    p_link = subparsers.add_parser("link", help="Configure local and remote GitHub repository bindings")
    p_link.add_argument("--remote", help="GitHub repo slug (owner/repo)")
    p_link.add_argument("--branch", help="Target base branch")
    p_link.set_defaults(func=cmd_link)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
