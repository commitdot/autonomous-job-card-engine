"""
AJE CLI Main Entry Point
========================
Supports:
  aje init      — Interactive project setup wizard & template selector
  aje start     — Run autonomous engine with live rich TUI dashboard
  aje inject    — Manually inject a feature request or bug ticket
  aje status    — Summary of Mother health, active squad, and pending backlog
  aje link      — Configure local and remote GitHub repository bindings
  aje adapter   — Adapter cartridge hub: search / pull / list / info / attach / init / test

Exit codes: 0 = success, 1 = command failed (not found, validation error, failed checks),
2 = usage error (argparse).
"""

import sys
import os
import re
import json
import argparse
import yaml
import glob
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..models import (
    MotherCard, ChildCard, RepositoryBinding, GovernancePolicies,
    AdapterBinding, AdapterCartridge, CartridgeValidationError, BINDING_MODES, CARTRIDGE_TYPES,
)
from ..adapters.harness_mixin import HarnessConfig
from ..adapters.lora_manager import LoRAManager, LoRAManagerError
from ..adapters.patch_loader import merge_harness_patch, LIST_COMPONENT_KEYS
from ..adapters.registry import AdapterRegistry, CartridgeNotFoundError, RegistryError
from ..daemon import AutonomousJobCardEngine
from ..security.attestation import QuarantineVault, CardAttestationEngine
from ..security.sast_scanner import ASTSecurityScanner
from ..security.audit_chain import AuditChain


def cmd_init(args):
    print("=" * 70)
    print("  [AJE] AUTONOMOUS JOB-CARD ENGINE (AJE) — PROJECT SETUP WIZARD")
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
    print("  1) SaaS Backend & API Modernizer (FastAPI / Carbon Design)")
    print("  2) Enterprise Security & Compliance Guardian (Air-Gapped Local)")
    print("  3) Autonomous 24/7 Open-Source Maintainer (Public Repo / Material 3)")
    print("  4) Frontend & Design System Enforcer (React / Tailwind)")
    print("  5) Custom Blank Canvas")
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
    print(f"\n[OK] Successfully queued new Worker Card: {card.name} [ID: {card.id}]")
    print(f"  Saved to: {card_path}")


def cmd_start(args):
    workspace = os.path.abspath(args.workspace or ".")
    mother_path = os.path.join(workspace, ".jobs", "mother_card.yaml")

    if not os.path.exists(mother_path):
        print(f"No Mother Card found at {mother_path}. Initializing default...")
        cmd_init(args)

    engine = AutonomousJobCardEngine(workspace_root=workspace)
    print(f"\n[RUN] Launching Autonomous Job-Card Engine on workspace: {workspace}")
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
    print(f"[OK] Updated repository bindings for {mother.name}")
    print(f"  Remote: {mother.repo.remote_slug}")
    print(f"  Branch: {mother.repo.target_branch}")


def cmd_quarantine(args):
    workspace = os.path.abspath(args.workspace or ".")
    vault = QuarantineVault(workspace_root=workspace)
    action = args.action or "list"

    if action == "list":
        records = vault.list_quarantined_cards()
        print("=" * 70)
        print("  [VAULT] AJE SAFE QUARANTINE VAULT - ISOLATED CARDS")
        print("=" * 70)
        if not records:
            print("  No quarantined cards found. All task cards are clean and authorized.")
            return

        print(f"  Found {len(records)} quarantined task card(s):\n")
        for r in records:
            print(f"  * ID: [{r.quarantine_id}] | Card: {r.card_id} | Severity: {r.severity}")
            print(f"    Reason: {r.reason}")
            print(f"    Timestamp: {r.timestamp} | Payload SHA-256: {r.sha256_payload_hash[:16]}...")
            print(f"    Meta: {r.meta_path}")
            print("-" * 70)

    elif action == "inspect":
        target_id = args.target_id
        if not target_id:
            print("Error: Specify card/quarantine ID to inspect: aje quarantine inspect <id>")
            return
        res = vault.inspect_quarantined_card(target_id)
        if not res:
            print(f"No quarantined card found matching '{target_id}'.")
            return
        payload, record = res
        print(f"Quarantine Record: {record.quarantine_id}")
        print(f"Card ID: {record.card_id}")
        print(f"Reason: {record.reason}")
        print(f"Severity: {record.severity}")
        print(f"Timestamp: {record.timestamp}")
        print(f"Remediation: {record.remediation}")
        print("\nPayload:")
        import json
        print(json.dumps(payload, indent=2))

    elif action == "release":
        target_id = args.target_id
        if not target_id:
            print("Error: Specify quarantine ID to release: aje quarantine release <id>")
            return
        released_path = vault.release_card(target_id, target_dir=".jobs")
        if released_path:
            print(f"[OK] Card successfully released and re-signed into active queue: {released_path}")
        else:
            print(f"Failed to release quarantined card '{target_id}'.")

    elif action == "purge":
        target_id = args.target_id
        if not target_id:
            print("Error: Specify quarantine ID to purge: aje quarantine purge <id>")
            return
        if vault.purge_quarantine(target_id):
            print(f"[OK] Quarantined record '{target_id}' permanently purged.")
        else:
            print(f"Failed to purge record '{target_id}'.")


def cmd_audit(args):
    workspace = os.path.abspath(args.workspace or ".")
    chain = AuditChain(workspace_root=workspace)
    action = args.action or "verify"

    if action == "verify":
        is_valid, err = chain.verify_integrity()
        print("=" * 70)
        print("  [AUDIT] AJE CRYPTOGRAPHIC AUDIT CHAIN INTEGRITY CHECK")
        print("=" * 70)
        if is_valid:
            latest = chain.get_latest_block()
            block_count = (latest.index + 1) if latest else 0
            print(f"  [OK] Audit Ledger Integrity Verified! Total cryptographically linked blocks: {block_count}")
            print(f"  Tip Hash: {latest.block_hash if latest else 'N/A'}")
        else:
            print(f"  [ERROR] Audit Ledger Cryptographic Tamper Detected: {err}")

    elif action == "list":
        blocks = chain.get_all_blocks()
        print(f"Total Audit Blocks: {len(blocks)}\n")
        for b in blocks[-10:]:
            print(f"Block #{b.index} [{b.event_type}] by {b.actor}")
            print(f"  Hash: {b.block_hash[:20]}... | Prev: {b.prev_hash[:20]}...")
            print(f"  Payload: {b.payload}")
            print()


def cmd_scan(args):
    workspace = os.path.abspath(args.workspace or ".")
    target_dir = os.path.abspath(args.path or workspace)
    print("=" * 70)
    print("  [SAST] AJE AST SAST SECURITY & SUPPLY-CHAIN SCANNER")
    print("=" * 70)
    print(f"Scanning target: {target_dir}...")
    scanner = ASTSecurityScanner()
    res = scanner.scan_directory(target_dir)

    print(f"Scanned files: {res.scanned_files_count}")
    print(f"Violations detected: {res.violations_count}")
    print(f"Highest Risk: {res.highest_risk.value}")
    print(f"Security Gate Passed: {'PASSED' if res.passed else 'FAILED'}")

    if res.findings:
        print("\nFindings:")
        for f in res.findings:
            print(f"  [{f.risk_level.value}] {f.rule_id} ({f.cwe}) in {f.file_path}:{f.line_number}")
            print(f"    {f.message}")
            if f.snippet:
                print(f"    Snippet: {f.snippet}")


# ---------------------------------------------------------------------------
# aje adapter — Adapter Cartridge Hub commands
# ---------------------------------------------------------------------------

_ANSI_CODES = {"green": "32", "yellow": "33", "red": "31", "cyan": "36", "bold": "1", "dim": "2"}
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_SECRET_RE = re.compile(r"(?i)(ghp_|sk-|AKIA)[a-zA-Z0-9]{20,}")
_ADAPTER_ERRORS = (RegistryError, LoRAManagerError, ValueError, OSError, yaml.YAMLError)


def _use_color() -> bool:
    """Color is enabled on a TTY, forced by FORCE_COLOR, and disabled by NO_COLOR."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


def _c(text: str, color: str) -> str:
    if not _use_color():
        return text
    return f"\x1b[{_ANSI_CODES[color]}m{text}\x1b[0m"


def _badge(text: str, color: str) -> str:
    return _c(f"[{text}]", color)


def _pass_rate(cart: AdapterCartridge) -> Optional[float]:
    """Return the cartridge pass rate as a 0..100 percentage, or None when not declared."""
    value = cart.spec.benchmarks.get("pass_rate")
    if value is None:
        return None
    return float(value) * 100.0 if value <= 1 else float(value)


def _pass_rate_badge(cart: AdapterCartridge) -> str:
    pct = _pass_rate(cart)
    if pct is None:
        return _c("n/a", "dim")
    color = "green" if pct >= 90 else ("yellow" if pct >= 75 else "red")
    return _badge(f"{pct:.0f}% pass", color)


def _render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Render a plain-text table; column widths ignore ANSI color sequences."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(_ANSI_RE.sub("", cell)))

    def fmt(cells: Sequence[str]) -> str:
        padded = [cell + " " * (widths[i] - len(_ANSI_RE.sub("", cell))) for i, cell in enumerate(cells)]
        return "  " + "  ".join(padded).rstrip()

    lines = [_c(fmt(headers), "bold"), "  " + "  ".join("-" * w for w in widths)]
    lines.extend(fmt(r) for r in rows)
    return "\n".join(lines)


def _err(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)


def _adapter_registry(workspace: str) -> AdapterRegistry:
    """Build a registry rooted at the workspace (same layout the daemon uses)."""
    jobs_dir = os.path.join(workspace, ".jobs")
    return AdapterRegistry(
        registry_dirs=[os.path.join(workspace, d) for d in AdapterRegistry.DEFAULT_REGISTRY_DIRS],
        installed_dir=os.path.join(jobs_dir, "installed_cartridges"),
        lora_manager=LoRAManager(cache_dir=os.path.join(jobs_dir, "adapters")),
    )


def _load_cartridge(target: str, registry: AdapterRegistry, version: str = "latest") -> AdapterCartridge:
    """Resolve a cartridge from a manifest path, a directory containing one, or a registry name."""
    if os.path.isfile(target):
        return AdapterCartridge.from_yaml(target)
    if os.path.isdir(target) and os.path.isfile(os.path.join(target, "cartridge.yaml")):
        return AdapterCartridge.from_yaml(os.path.join(target, "cartridge.yaml"))
    cart = registry.get_cartridge(target, version)
    if cart is None:
        raise CartridgeNotFoundError(f"Cartridge '{target}' (version: {version}) was not found.")
    return cart


def _weights_status(registry: AdapterRegistry, cart: AdapterCartridge) -> str:
    if cart.spec.type == "harness-patch":
        return _c("n/a", "dim")
    if registry.lora_manager.is_cached(cart):
        return _c("cached", "green")
    if os.path.isfile(registry.lora_manager.get_adapter_path(cart)):
        return _c("invalid", "red")
    return _c("missing", "yellow")


def _truncate(text: str, limit: int = 100) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _adapter_search(args, registry: AdapterRegistry) -> int:
    results = registry.search(
        query=args.query or "",
        domain=args.industry,
        tag=args.tag,
        cartridge_type=args.cartridge_type,
        archetype=args.archetype,
    )
    if not results:
        print("No cartridges matched your search.")
        return 0

    installed = {c.metadata.name for c in registry.list_installed()}
    rows = [
        [
            c.metadata.name,
            str(c.metadata.version),
            c.spec.type,
            c.metadata.domain or "-",
            _pass_rate_badge(c),
            _c("installed", "green") if c.metadata.name in installed else _c("available", "dim"),
        ]
        for c in results
    ]
    print(_render_table(["NAME", "VERSION", "TYPE", "INDUSTRY", "PASS RATE", "STATUS"], rows))
    print(f"\n{len(results)} cartridge(s) found. Use 'aje adapter pull <name>' to install.")
    return 0


def _adapter_pull(args, registry: AdapterRegistry) -> int:
    cart = registry.pull(
        args.target,
        version=args.version,
        download_weights=args.weights,
        force=args.force,
    )
    md = cart.metadata
    dest = os.path.join(registry.installed_dir, md.name, "cartridge.yaml")
    print(f"{_badge('OK', 'green')} Installed {md.name}@{md.version} ({cart.spec.type})")
    print(f"  Manifest: {dest}")

    if args.weights:
        if cart.spec.type == "harness-patch":
            print("  Weights:  none (zero-weight harness patch)")
        else:
            print(f"  Weights:  {registry.lora_manager.get_adapter_path(cart)}")
    elif cart.spec.type in ("lora", "hybrid"):
        print(f"  Weights:  not downloaded (run 'aje adapter pull {md.name} --weights')")
    return 0


def _adapter_list(args, registry: AdapterRegistry, workspace: str) -> int:
    installed = registry.list_installed()
    if not installed:
        print("No cartridges installed. Use 'aje adapter pull <name>' to install one.")
        return 0

    attached = {}
    mother_path = os.path.join(workspace, ".jobs", "mother_card.yaml")
    if os.path.isfile(mother_path):
        attached = {b.cartridge: b for b in MotherCard.from_yaml(mother_path).attached_cartridges}

    rows = []
    for c in installed:
        binding = attached.get(c.metadata.name)
        if binding is None:
            att = _c("no", "dim")
        else:
            att = _c("yes", "green") if binding.enabled else _c("disabled", "yellow")
        rows.append([
            c.metadata.name,
            str(c.metadata.version),
            c.spec.type,
            c.metadata.domain or "-",
            _pass_rate_badge(c),
            _weights_status(registry, c),
            att,
        ])
    print(_render_table(["NAME", "VERSION", "TYPE", "INDUSTRY", "PASS RATE", "WEIGHTS", "ATTACHED"], rows))
    print(f"\n{len(installed)} cartridge(s) installed in {registry.installed_dir}")
    return 0


def _adapter_info(args, registry: AdapterRegistry) -> int:
    cart = _load_cartridge(args.target, registry, args.version)
    md, sp = cart.metadata, cart.spec
    installed = registry.is_installed(md.name)

    print(f"{_c(md.name, 'bold')} {md.version}  {_badge(sp.type, 'cyan')}  {_pass_rate_badge(cart)}")
    if md.description:
        print(f"  {md.description}")
    print()
    print(f"  Author:      {md.author or '-'}")
    print(f"  License:     {md.license or '-'}")
    print(f"  Industry:    {md.domain or '-'}")
    print(f"  Tags:        {', '.join(md.tags) or '-'}")
    print(f"  Base models: {', '.join(sp.base_models) or '-'}")
    print(f"  Archetypes:  {', '.join(sp.compatible_archetypes) or 'any'}")
    print(f"  Status:      {_c('installed', 'green') if installed else _c('available (not installed)', 'dim')}")

    if sp.type in ("lora", "hybrid"):
        print("\nLoRA weights")
        print(f"  Repo:     {sp.lora_repo_id}")
        print(f"  File:     {sp.lora_filename}")
        print(f"  Rank:     {sp.lora_rank or '-'}")
        print(f"  SHA-256:  {sp.lora_sha256 or '-'}")
        print(f"  Cache:    {_weights_status(registry, cart)}")

    print("\nBenchmarks")
    if sp.benchmarks:
        rows = [[k, f"{v:g}"] for k, v in sorted(sp.benchmarks.items())]
        print(_render_table(["METRIC", "VALUE"], rows))
    else:
        print("  none declared")

    print("\nHarness patch")
    patch = sp.harness_patch
    if not patch:
        print("  none (LoRA-only cartridge)")
        return 0
    if patch.get("system_prompt"):
        print(f"  system_prompt ({len(patch['system_prompt'])} chars): {_truncate(patch['system_prompt'])}")
    for key in LIST_COMPONENT_KEYS:
        items = patch.get(key)
        if not items:
            continue
        items = [items] if isinstance(items, str) else list(items)
        print(f"  {key} ({len(items)}):")
        for item in items:
            print(f"    - {_truncate(item)}")
    return 0


def _adapter_attach(args, registry: AdapterRegistry, workspace: str) -> int:
    mother_path = os.path.join(workspace, ".jobs", "mother_card.yaml")
    if not os.path.isfile(mother_path):
        _err(f"No active Mother Card found at {mother_path}. Run 'aje init' first.")
        return 1
    mother = MotherCard.from_yaml(mother_path)

    existing = next((b for b in mother.attached_cartridges if b.cartridge == args.name), None)
    version = args.version or (existing.version if existing else "latest")

    cart = registry.get_cartridge(args.name, version)
    if cart is None:
        raise CartridgeNotFoundError(f"Cartridge '{args.name}' (version: {version}) was not found.")
    name = cart.metadata.name

    if not registry.is_installed(name):
        registry.pull(name, version)
        print(f"  Installed {name}@{cart.metadata.version} from catalog")

    if existing is not None:
        existing.version = version
        existing.enabled = not args.disabled
        if args.priority is not None:
            existing.priority = args.priority
        if args.mode is not None:
            existing.mode = args.mode
        binding, verb = existing, "Updated"
    else:
        binding = AdapterBinding(
            cartridge=name,
            version=version,
            enabled=not args.disabled,
            priority=args.priority or 0,
            mode=args.mode or "auto",
        )
        mother.attached_cartridges.append(binding)
        verb = "Attached"
    binding.validate()

    mother.to_yaml(mother_path)
    state = "enabled" if binding.enabled else "disabled"
    print(f"{_badge('OK', 'green')} {verb} {name}@{version} on {mother.name} "
          f"(mode={binding.mode}, priority={binding.priority}, {state})")

    compat = cart.spec.compatible_archetypes
    if compat and mother.archetype not in compat:
        print(f"{_badge('WARN', 'yellow')} Mother archetype '{mother.archetype}' is not in this "
              f"cartridge's compatible archetypes: {', '.join(compat)}")

    rows = [
        [b.cartridge, b.version, b.mode, str(b.priority), "yes" if b.enabled else "no"]
        for b in mother.attached_cartridges
    ]
    print("\nAttached cartridges:")
    print(_render_table(["CARTRIDGE", "VERSION", "MODE", "PRIORITY", "ENABLED"], rows))
    return 0


_CARTRIDGE_README = """# __NAME__

Starter AJE adapter cartridge (`__TYPE__`).

## Files

- `cartridge.yaml` - manifest: metadata, harness patch, LoRA reference, benchmarks
- `test_cartridge.py` - starter unit tests for the manifest

## Workflow

```
aje adapter test .                 # validate manifest, patch and guardrails
aje adapter pull .                 # install into .jobs/installed_cartridges/
aje adapter attach __NAME__        # attach to the active Mother Card
```

Edit `cartridge.yaml`, replace the placeholder guardrails with your domain rules and
record real benchmark results under `spec.benchmarks` before publishing.
"""

_CARTRIDGE_TEST = '''"""Starter tests for the __NAME__ cartridge. Run: python -m unittest test_cartridge"""
import os
import unittest

import yaml

MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cartridge.yaml")


class TestCartridgeManifest(unittest.TestCase):
    def setUp(self):
        with open(MANIFEST, encoding="utf-8") as f:
            self.data = yaml.safe_load(f)

    def test_kind_and_name(self):
        self.assertEqual(self.data["kind"], "AdapterCartridge")
        self.assertEqual(self.data["metadata"]["name"], "__NAME__")

    def test_guardrails_are_non_empty_strings(self):
        patch = self.data["spec"].get("harness_patch") or {}
        for rule in patch.get("guardrails", []):
            self.assertIsInstance(rule, str)
            self.assertTrue(rule.strip())

    def test_validates_with_aje(self):
        try:
            from src.models import AdapterCartridge
        except ImportError:
            self.skipTest("AJE is not importable; run 'aje adapter test .' instead")
        AdapterCartridge.from_yaml(MANIFEST).validate()


if __name__ == "__main__":
    unittest.main()
'''


def _adapter_init(args, workspace: str) -> int:
    from ..models import CartridgeMetadata, CartridgeSpec

    spec = CartridgeSpec(type=args.cartridge_type, benchmarks={"pass_rate": 0.0})
    if args.cartridge_type in ("lora", "hybrid"):
        spec.lora_repo_id = "your-org/your-lora-repo"
        spec.lora_filename = "adapter.safetensors"
        spec.lora_rank = 16
    if args.cartridge_type in ("harness-patch", "hybrid"):
        spec.harness_patch = {
            "system_prompt": f"You are applying the {args.name} domain rules.",
            "guardrails": ["Replace this placeholder with a hard domain constraint."],
            "tool_guidance": ["Replace this placeholder with guidance on preferred tools."],
        }
    cart = AdapterCartridge(
        metadata=CartridgeMetadata(
            name=args.name,
            version="0.1.0",
            author=args.author,
            description=args.description or f"Starter cartridge for {args.name}.",
            license="Apache-2.0",
            domain=args.industry or "",
            tags=[],
        ),
        spec=spec,
    )
    cart.validate()  # fail before touching the disk

    out_dir = os.path.abspath(args.output or os.path.join(workspace, "cartridges", args.name))
    if os.path.isdir(out_dir) and os.listdir(out_dir) and not args.force:
        _err(f"Directory {out_dir} already exists and is not empty (use --force to overwrite).")
        return 1
    os.makedirs(out_dir, exist_ok=True)

    cart.to_yaml(os.path.join(out_dir, "cartridge.yaml"))
    files = {
        "README.md": _CARTRIDGE_README,
        "test_cartridge.py": _CARTRIDGE_TEST,
    }
    for filename, template in files.items():
        content = template.replace("__NAME__", args.name).replace("__TYPE__", args.cartridge_type)
        with open(os.path.join(out_dir, filename), "w", encoding="utf-8") as f:
            f.write(content)

    print(f"{_badge('OK', 'green')} Scaffolded cartridge '{args.name}' ({args.cartridge_type}) in {out_dir}")
    for filename in ("cartridge.yaml", *files):
        print(f"  - {filename}")
    print(f"\nNext: edit the manifest, then run 'aje adapter test {out_dir}'")
    return 0


def _adapter_checks(cart: AdapterCartridge, registry: AdapterRegistry) -> List[Tuple[str, str, str]]:
    """Run semantic checks on a schema-valid cartridge. Returns (status, check, detail) rows."""
    sp = cart.spec
    checks: List[Tuple[str, str, str]] = [("PASS", "manifest", "schema valid")]
    has_patch = sp.type in ("harness-patch", "hybrid")
    patch = sp.harness_patch

    # Patch & guardrails
    if has_patch:
        raw = patch.get("guardrails") or []
        guardrails = [raw] if isinstance(raw, str) else list(raw)
        blank = [g for g in guardrails if not isinstance(g, str) or not g.strip()]
        if not guardrails:
            checks.append(("WARN", "guardrails", "no guardrails declared"))
        elif blank:
            checks.append(("FAIL", "guardrails", f"{len(blank)} empty or non-string guardrail(s)"))
        elif len(set(guardrails)) != len(guardrails):
            checks.append(("WARN", "guardrails", "duplicate guardrails declared"))
        else:
            checks.append(("PASS", "guardrails", f"{len(guardrails)} guardrail(s)"))
        if not (patch.get("system_prompt") or "").strip():
            checks.append(("WARN", "system_prompt", "empty system_prompt"))

        try:
            block = merge_harness_patch(HarnessConfig(), cart).render_prompt_block()
            if block.strip():
                checks.append(("PASS", "patch-merge", f"merges into harness ({len(block)} chars rendered)"))
            else:
                checks.append(("FAIL", "patch-merge", "patch renders an empty prompt block"))
        except Exception as e:  # any merge failure is a cartridge defect
            checks.append(("FAIL", "patch-merge", str(e)))

        if _SECRET_RE.search(json.dumps(patch, default=str)):
            checks.append(("FAIL", "secrets", "patch contains a token-like secret"))
        else:
            checks.append(("PASS", "secrets", "no secrets detected in patch"))
    else:
        checks.append(("PASS", "patch", "n/a (lora-only cartridge)"))

    # Weights
    if sp.type in ("lora", "hybrid"):
        lora = registry.lora_manager
        path = lora.get_adapter_path(cart)
        if not os.path.isfile(path):
            fallback = " (harness patch fallback applies)" if has_patch else ""
            checks.append(("WARN", "weights", f"not cached{fallback}; run 'aje adapter pull {cart.metadata.name} --weights'"))
        elif sp.lora_sha256 and not lora.is_cached(cart):
            checks.append(("FAIL", "weights", "cached file does not match lora_sha256"))
        else:
            checks.append(("PASS", "weights", "cached"))
        if sp.lora_sha256:
            checks.append(("PASS", "integrity", "lora_sha256 declared"))
        else:
            checks.append(("WARN", "integrity", "lora_sha256 not declared; downloads are unverified"))

    # Benchmarks
    if not sp.benchmarks:
        checks.append(("WARN", "benchmarks", "none declared"))
    else:
        bad = [k for k, v in sp.benchmarks.items() if v < 0 or (k == "pass_rate" and v > 100)]
        if bad:
            checks.append(("FAIL", "benchmarks", f"out-of-range value(s): {', '.join(sorted(bad))}"))
        else:
            checks.append(("PASS", "benchmarks", ", ".join(f"{k}={v:g}" for k, v in sorted(sp.benchmarks.items()))))
    return checks


def _adapter_test(args, registry: AdapterRegistry) -> int:
    try:
        cart = _load_cartridge(args.target, registry, args.version)
    except CartridgeNotFoundError:
        raise
    except (CartridgeValidationError, yaml.YAMLError, OSError) as e:
        print(f"{_badge('FAIL', 'red')} manifest: {e}")
        print(f"\nResult: {_c('FAILED', 'red')} (1 failed)")
        return 1

    colors = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}
    checks = _adapter_checks(cart, registry)
    print(f"Testing {cart.metadata.name}@{cart.metadata.version} ({cart.spec.type})\n")
    for status, check, detail in checks:
        print(f"  {_badge(status, colors[status])} {check:<14} {detail}")

    failed = sum(1 for s, _, _ in checks if s == "FAIL")
    warned = sum(1 for s, _, _ in checks if s == "WARN")
    passed = sum(1 for s, _, _ in checks if s == "PASS")
    summary = f"{passed} passed, {warned} warning(s), {failed} failed"
    if failed:
        print(f"\nResult: {_c('FAILED', 'red')} ({summary})")
        return 1
    print(f"\nResult: {_c('OK', 'green')} ({summary})")
    return 0


def cmd_adapter(args) -> int:
    """Dispatch 'aje adapter <subcommand>'. Returns a process exit code (0 ok, 1 failure)."""
    workspace = os.path.abspath(args.workspace or ".")
    sub = args.adapter_command
    try:
        if sub == "init":  # scaffolding needs no registry
            return _adapter_init(args, workspace)
        registry = _adapter_registry(workspace)
        if sub == "search":
            return _adapter_search(args, registry)
        if sub == "pull":
            return _adapter_pull(args, registry)
        if sub == "list":
            return _adapter_list(args, registry, workspace)
        if sub == "info":
            return _adapter_info(args, registry)
        if sub == "attach":
            return _adapter_attach(args, registry, workspace)
        if sub == "test":
            return _adapter_test(args, registry)
    except _ADAPTER_ERRORS as e:
        _err(str(e))
        return 1
    _err(f"Unknown adapter command: {sub}")
    return 1



def main(argv=None) -> int:
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

    # quarantine
    p_quarantine = subparsers.add_parser("quarantine", help="Manage isolated and quarantined task cards")
    p_quarantine.add_argument("action", nargs="?", default="list", choices=["list", "inspect", "release", "purge"])
    p_quarantine.add_argument("target_id", nargs="?", default=None, help="Quarantine ID or Card ID")
    p_quarantine.set_defaults(func=cmd_quarantine)

    # audit
    p_audit = subparsers.add_parser("audit", help="Verify or inspect cryptographic audit chain ledger")
    p_audit.add_argument("action", nargs="?", default="verify", choices=["verify", "list"])
    p_audit.set_defaults(func=cmd_audit)

    # scan
    p_scan = subparsers.add_parser("scan", help="Run AST SAST and supply-chain security scan")
    p_scan.add_argument("path", nargs="?", default=None, help="Target directory to scan")
    p_scan.set_defaults(func=cmd_scan)

    # adapter
    p_adapter = subparsers.add_parser("adapter", help="Discover, install and attach adapter cartridges")
    adapter_sub = p_adapter.add_subparsers(dest="adapter_command", required=True, metavar="SUBCOMMAND")
    p_adapter.set_defaults(func=cmd_adapter)

    a_search = adapter_sub.add_parser("search", help="Search cartridges by query, industry, type, tag")
    a_search.add_argument("query", nargs="?", default="", help="Free-text query (name, description, author, tags)")
    a_search.add_argument("--industry", help="Filter by industry/domain (e.g. fintech)")
    a_search.add_argument("--type", dest="cartridge_type", choices=CARTRIDGE_TYPES, help="Filter by cartridge type")
    a_search.add_argument("--tag", help="Filter by tag")
    a_search.add_argument("--archetype", help="Filter by compatible Mother Card archetype")

    a_pull = adapter_sub.add_parser("pull", help="Install a cartridge (and optionally its weights)")
    a_pull.add_argument("target", help="Cartridge name, or path to a cartridge.yaml / directory")
    a_pull.add_argument("--version", default="latest", help="Cartridge version (default: latest)")
    a_pull.add_argument("--weights", action="store_true", help="Also download LoRA weights into .jobs/adapters/")
    a_pull.add_argument("--force", action="store_true", help="Overwrite an existing install / cached weights")

    adapter_sub.add_parser("list", help="List installed cartridges")

    a_info = adapter_sub.add_parser("info", help="Show metadata, benchmarks and patch details")
    a_info.add_argument("target", help="Cartridge name, or path to a cartridge.yaml / directory")
    a_info.add_argument("--version", default="latest", help="Cartridge version (default: latest)")

    a_attach = adapter_sub.add_parser("attach", help="Attach a cartridge to the active Mother Card")
    a_attach.add_argument("name", help="Cartridge name")
    a_attach.add_argument("--version", default=None, help="Pin a version (default: latest)")
    a_attach.add_argument("--mode", choices=BINDING_MODES, default=None, help="Binding mode (default: auto)")
    a_attach.add_argument("--priority", type=int, default=None, help="Merge priority; higher applies later")
    a_attach.add_argument("--disabled", action="store_true", help="Attach but leave the binding disabled")

    a_init = adapter_sub.add_parser("init", help="Scaffold a starter cartridge (manifest, README, tests)")
    a_init.add_argument("name", help="Cartridge name (lowercase letters, digits, '.', '_', '-')")
    a_init.add_argument("--type", dest="cartridge_type", choices=CARTRIDGE_TYPES, default="harness-patch")
    a_init.add_argument("--industry", help="Industry/domain label")
    a_init.add_argument("--author", default="", help="Author name")
    a_init.add_argument("--description", default="", help="One-line description")
    a_init.add_argument("--output", help="Output directory (default: cartridges/<name>)")
    a_init.add_argument("--force", action="store_true", help="Write into a non-empty directory")

    a_test = adapter_sub.add_parser("test", help="Validate a cartridge manifest, patch and guardrails")
    a_test.add_argument("target", nargs="?", default=".", help="Cartridge name, or path to cartridge.yaml / directory (default: .)")
    a_test.add_argument("--version", default="latest", help="Cartridge version (default: latest)")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
