import yaml
import os
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field, asdict

@dataclass
class RepositoryBinding:
    local_path: str = "."
    remote_slug: str = ""  # e.g., "owner/repo"
    target_branch: str = "main"
    branch_prefix: str = "aje/"
    auth_token_env: str = "GITHUB_TOKEN"
    auto_create_pr: bool = True
    auto_merge_pr: bool = False


@dataclass
class GovernancePolicies:
    """
    Fleet Governance & Quality Policies enforced autonomously by the Mother Card.
    """
    enable_repo_regex_scan: bool = True
    forbidden_patterns: List[str] = field(default_factory=lambda: [r"(?i)v2\.0", r"(?i)TODO:\s*urgent", r"(?i)(ghp_|sk-|AKIA)[a-zA-Z0-9]{20,}"])
    enable_ast_skeletonization: bool = True
    context_budget_chars: int = 16000
    enable_alignment_cascades: bool = True
    max_wave_depth: int = 3
    max_concurrency: int = 2
    vram_threshold_pct: float = 85.0
    dual_pass_flakiness_check: bool = True
    composite_integration_commands: List[str] = field(default_factory=list)
    enable_attestation: bool = False
    enable_injection_guard: bool = True
    enable_sast_scan: bool = True
    enable_containment_jail: bool = True
    enable_audit_chain: bool = True
    enable_harness2: bool = False
    max_recursion_depth: int = 3


# ---------------------------------------------------------------------------
# Adapter Cartridge models (cartridge.yaml)
# ---------------------------------------------------------------------------

CARTRIDGE_API_VERSION = "agent.autonomous.io/v1alpha1"
CARTRIDGE_KIND = "AdapterCartridge"
CARTRIDGE_TYPES = ("harness-patch", "lora", "hybrid")
BINDING_MODES = ("auto", "lora", "patch")
HARNESS_PATCH_KEYS = (
    "system_prompt", "guardrails", "memory", "tool_guidance",
    "subagents", "skills", "scripts", "plugins", "metadata",
)

_NAME_RE = re.compile(r"^[a-z0-9]+(?:[a-z0-9._-]*[a-z0-9])?$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class CartridgeValidationError(ValueError):
    """Raised when a cartridge manifest or adapter binding fails schema validation."""


@dataclass
class CartridgeMetadata:
    name: str
    version: str = "0.1.0"
    author: str = ""
    description: str = ""
    license: str = ""
    domain: str = ""
    tags: List[str] = field(default_factory=list)


@dataclass
class CartridgeSpec:
    type: str = "harness-patch"  # harness-patch | lora | hybrid
    base_models: List[str] = field(default_factory=list)
    compatible_archetypes: List[str] = field(default_factory=list)
    # LoRA weights (Hugging Face Hub)
    lora_repo_id: str = ""
    lora_filename: str = ""
    lora_sha256: str = ""
    lora_rank: int = 0
    # Zero-weight YAML harness patch (keys mirror HarnessConfig fields)
    harness_patch: Dict[str, Any] = field(default_factory=dict)
    # Accuracy benchmarks, e.g. {"pass_rate": 0.92}
    benchmarks: Dict[str, float] = field(default_factory=dict)


@dataclass
class AdapterBinding:
    """Reference from a Mother Card to an adapter cartridge."""
    cartridge: str
    version: str = "latest"
    enabled: bool = True
    priority: int = 0
    mode: str = "auto"  # auto | lora | patch
    overrides: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not isinstance(self.cartridge, str) or not _NAME_RE.match(self.cartridge):
            raise CartridgeValidationError(f"Invalid cartridge name in binding: {self.cartridge!r}")
        if self.version != "latest" and not _SEMVER_RE.match(str(self.version)):
            raise CartridgeValidationError(f"Invalid binding version: {self.version!r}")
        if self.mode not in BINDING_MODES:
            raise CartridgeValidationError(f"Invalid binding mode: {self.mode!r} (expected one of {BINDING_MODES})")
        if not isinstance(self.priority, int) or isinstance(self.priority, bool):
            raise CartridgeValidationError("Binding priority must be an integer")

    @classmethod
    def from_dict(cls, data: Any) -> "AdapterBinding":
        if isinstance(data, str):
            data = {"cartridge": data}
        if not isinstance(data, dict) or "cartridge" not in data:
            raise CartridgeValidationError("Adapter binding must be a string or a mapping with 'cartridge'")
        binding = cls(
            cartridge=data["cartridge"],
            version=str(data.get("version", "latest")),
            enabled=data.get("enabled", True),
            priority=data.get("priority", 0),
            mode=data.get("mode", "auto"),
            overrides=data.get("overrides") or {},
        )
        binding.validate()
        return binding

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AdapterCartridge:
    metadata: CartridgeMetadata
    spec: CartridgeSpec = field(default_factory=CartridgeSpec)

    def validate(self) -> None:
        md, sp = self.metadata, self.spec
        if not isinstance(md.name, str) or not _NAME_RE.match(md.name):
            raise CartridgeValidationError(f"Invalid cartridge name: {md.name!r}")
        if not _SEMVER_RE.match(str(md.version)):
            raise CartridgeValidationError(f"Invalid cartridge version: {md.version!r} (expected semver)")
        if sp.type not in CARTRIDGE_TYPES:
            raise CartridgeValidationError(f"Invalid cartridge type: {sp.type!r} (expected one of {CARTRIDGE_TYPES})")
        if sp.type in ("lora", "hybrid"):
            if not sp.lora_repo_id or not sp.lora_filename:
                raise CartridgeValidationError(f"Cartridge type '{sp.type}' requires lora_repo_id and lora_filename")
        if sp.lora_sha256 and not _SHA256_RE.match(sp.lora_sha256):
            raise CartridgeValidationError("lora_sha256 must be a 64-character hex digest")
        if sp.type in ("harness-patch", "hybrid") and not sp.harness_patch:
            raise CartridgeValidationError(f"Cartridge type '{sp.type}' requires a non-empty harness_patch")
        unknown = set(sp.harness_patch) - set(HARNESS_PATCH_KEYS)
        if unknown:
            raise CartridgeValidationError(f"Unknown harness_patch keys: {sorted(unknown)}")
        for key, value in sp.benchmarks.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CartridgeValidationError(f"Benchmark '{key}' must be numeric")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AdapterCartridge":
        if not isinstance(data, dict):
            raise CartridgeValidationError("Cartridge manifest must be a mapping")
        if data.get("kind", CARTRIDGE_KIND) != CARTRIDGE_KIND:
            raise CartridgeValidationError(f"Unexpected kind: {data.get('kind')!r} (expected {CARTRIDGE_KIND})")
        md_data = data.get("metadata") or {}
        if not md_data.get("name"):
            raise CartridgeValidationError("metadata.name is required")
        sp_data = data.get("spec") or {}
        cartridge = cls(
            metadata=CartridgeMetadata(
                name=md_data["name"],
                version=str(md_data.get("version", "0.1.0")),
                author=md_data.get("author", ""),
                description=md_data.get("description", ""),
                license=md_data.get("license", ""),
                domain=md_data.get("domain", ""),
                tags=list(md_data.get("tags") or []),
            ),
            spec=CartridgeSpec(
                type=sp_data.get("type", "harness-patch"),
                base_models=list(sp_data.get("base_models") or []),
                compatible_archetypes=list(sp_data.get("compatible_archetypes") or []),
                lora_repo_id=sp_data.get("lora_repo_id", ""),
                lora_filename=sp_data.get("lora_filename", ""),
                lora_sha256=sp_data.get("lora_sha256", ""),
                lora_rank=sp_data.get("lora_rank", 0),
                harness_patch=dict(sp_data.get("harness_patch") or {}),
                benchmarks=dict(sp_data.get("benchmarks") or {}),
            ),
        )
        cartridge.validate()
        return cartridge

    def to_dict(self) -> Dict[str, Any]:
        return {
            "apiVersion": CARTRIDGE_API_VERSION,
            "kind": CARTRIDGE_KIND,
            "metadata": asdict(self.metadata),
            "spec": asdict(self.spec),
        }

    @classmethod
    def from_yaml(cls, path: str) -> "AdapterCartridge":
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    def to_yaml(self, path: str):
        self.validate()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(self.to_dict(), f, default_flow_style=False)


@dataclass
class MotherCard:
    id: str
    name: str
    core_philosophy: str
    privacy_mode: str = "hybrid"  # local | cloud | hybrid
    restricted_paths: List[str] = field(default_factory=list)
    banned_commands: List[str] = field(default_factory=list)
    global_context: Dict[str, Any] = field(default_factory=dict)
    design_system: str = ""
    archetype: str = "saas-backend"  # saas-backend | enterprise-security | opensource-maintainer | frontend-design | custom

    # Repository & VCS integration
    repo: RepositoryBinding = field(default_factory=RepositoryBinding)

    # RAG knowledge paths
    rag_knowledge_paths: List[str] = field(default_factory=lambda: ["docs", "rfc", "schemas"])

    # 3-Tier Hierarchy: Registered Department Squads
    squad_leads: List[str] = field(default_factory=lambda: ["backend", "security", "qa", "ui"])

    # Quality & Fleet Governance Policies
    governance: GovernancePolicies = field(default_factory=GovernancePolicies)

    # Live engine status
    family_health: str = "Healthy"
    total_spend_usd: float = 0.0
    active_children: List[str] = field(default_factory=list)
    completed_children: List[str] = field(default_factory=list)
    backlog_queue: List[Dict[str, Any]] = field(default_factory=list)

    # Adapter cartridges attached to this Mother Card (optional; omitted from YAML when empty)
    attached_cartridges: List[AdapterBinding] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str) -> "MotherCard":
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        spec = data.get("spec", {})
        metadata = data.get("metadata", {})
        status = data.get("status", {})
        repo_data = spec.get("repository", {})
        repo_binding = RepositoryBinding(
            local_path=repo_data.get("local_path", "."),
            remote_slug=repo_data.get("remote_slug", ""),
            target_branch=repo_data.get("target_branch", "main"),
            branch_prefix=repo_data.get("branch_prefix", "aje/"),
            auth_token_env=repo_data.get("auth_token_env", "GITHUB_TOKEN"),
            auto_create_pr=repo_data.get("auto_create_pr", True),
            auto_merge_pr=repo_data.get("auto_merge_pr", False),
        )

        gov_data = spec.get("governance_policies", {})
        governance = GovernancePolicies(
            enable_repo_regex_scan=gov_data.get("enable_repo_regex_scan", True),
            forbidden_patterns=gov_data.get("forbidden_patterns", [r"(?i)v2\.0", r"(?i)TODO:\s*urgent", r"(?i)(ghp_|sk-|AKIA)[a-zA-Z0-9]{20,}"]),
            enable_ast_skeletonization=gov_data.get("enable_ast_skeletonization", True),
            context_budget_chars=gov_data.get("context_budget_chars", 16000),
            enable_alignment_cascades=gov_data.get("enable_alignment_cascades", True),
            max_wave_depth=gov_data.get("max_wave_depth", 3),
            max_concurrency=gov_data.get("max_concurrency", 2),
            vram_threshold_pct=float(gov_data.get("vram_threshold_pct", 85.0)),
            dual_pass_flakiness_check=gov_data.get("dual_pass_flakiness_check", True),
            composite_integration_commands=gov_data.get("composite_integration_commands", []),
            enable_attestation=gov_data.get("enable_attestation", False),
            enable_injection_guard=gov_data.get("enable_injection_guard", True),
            enable_sast_scan=gov_data.get("enable_sast_scan", True),
            enable_containment_jail=gov_data.get("enable_containment_jail", True),
            enable_audit_chain=gov_data.get("enable_audit_chain", True),
            enable_harness2=gov_data.get("enable_harness2", False),
            max_recursion_depth=gov_data.get("max_recursion_depth", 3),
        )

        return cls(
            id=metadata.get("id", "mother-default"),
            name=metadata.get("name", "Autonomous Mother Guardian"),
            core_philosophy=spec.get("core_philosophy", ""),
            privacy_mode=spec.get("safety_policies", {}).get("privacy_mode", "hybrid"),
            restricted_paths=spec.get("safety_policies", {}).get("restricted_paths", []),
            banned_commands=spec.get("safety_policies", {}).get("banned_commands", []),
            global_context=spec.get("global_context", {}),
            design_system=spec.get("design_system", ""),
            archetype=spec.get("archetype", "saas-backend"),
            repo=repo_binding,
            rag_knowledge_paths=spec.get("rag_knowledge_paths", ["docs", "rfc", "schemas"]),
            squad_leads=spec.get("squad_leads", ["backend", "security", "qa", "ui"]),
            governance=governance,
            family_health=status.get("family_health", "Healthy"),
            total_spend_usd=status.get("total_spend_usd", 0.0),
            active_children=status.get("active_children", []),
            completed_children=status.get("completed_children", []),
            backlog_queue=status.get("backlog_queue", []),
            attached_cartridges=[AdapterBinding.from_dict(b) for b in (spec.get("attached_cartridges") or [])],
        )

    def to_yaml(self, path: str):
        data = {
            "apiVersion": "agent.autonomous.io/v1alpha1",
            "kind": "MotherCard",
            "metadata": {
                "id": self.id,
                "name": self.name
            },
            "spec": {
                "archetype": self.archetype,
                "core_philosophy": self.core_philosophy,
                "design_system": self.design_system,
                "safety_policies": {
                    "privacy_mode": self.privacy_mode,
                    "restricted_paths": self.restricted_paths,
                    "banned_commands": self.banned_commands
                },
                "governance_policies": asdict(self.governance),
                "repository": asdict(self.repo),
                "rag_knowledge_paths": self.rag_knowledge_paths,
                "squad_leads": self.squad_leads,
                "global_context": self.global_context
            },
            "status": {
                "family_health": self.family_health,
                "total_spend_usd": self.total_spend_usd,
                "active_children": self.active_children,
                "completed_children": self.completed_children,
                "backlog_queue": self.backlog_queue
            }
        }
        if self.attached_cartridges:
            data["spec"]["attached_cartridges"] = [b.to_dict() for b in self.attached_cartridges]

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(data, f, default_flow_style=False)


@dataclass
class SquadLeadCard:
    """
    Level 2: Department Lead Card (Squad Lead)
    Coordinates domain-specific tactical workers and policies.
    """
    id: str
    parent_mother_id: str
    name: str
    domain: str  # backend | security | qa | ui | devops
    domain_mission: str
    assigned_workers: List[str] = field(default_factory=list)
    completed_workers: List[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str) -> "SquadLeadCard":
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        metadata = data.get("metadata", {})
        spec = data.get("spec", {})
        status = data.get("status", {})
        return cls(
            id=metadata.get("id"),
            parent_mother_id=metadata.get("parent_mother_id"),
            name=metadata.get("name"),
            domain=spec.get("domain", "backend"),
            domain_mission=spec.get("domain_mission", ""),
            assigned_workers=status.get("assigned_workers", []),
            completed_workers=status.get("completed_workers", [])
        )

    def to_yaml(self, path: str):
        data = {
            "apiVersion": "agent.autonomous.io/v1alpha1",
            "kind": "SquadLeadCard",
            "metadata": {
                "id": self.id,
                "parent_mother_id": self.parent_mother_id,
                "name": self.name
            },
            "spec": {
                "domain": self.domain,
                "domain_mission": self.domain_mission
            },
            "status": {
                "assigned_workers": self.assigned_workers,
                "completed_workers": self.completed_workers
            }
        }
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(data, f, default_flow_style=False)


@dataclass
class ChildCard:
    """
    Level 3: Tactical Worker Card (Sub-Child / IC)
    Executes scoped tasks in the self-healing sandbox.
    """
    id: str
    parent_mother_id: str
    name: str
    tactical_objective: str
    parent_squad_id: str = "squad-backend"
    deliverables: List[Dict[str, str]] = field(default_factory=list)
    validation_commands: List[str] = field(default_factory=list)
    rag_query: Optional[str] = None
    external_ticket_id: Optional[str] = None
    source_platform: Optional[str] = None
    depends_on: List[str] = field(default_factory=list)
    wave_depth: int = 1
    task_fingerprint: str = ""
    worktree_path: str = ""
    signature: Optional[Dict[str, Any]] = None
    
    # Engine status
    phase: str = "Pending"  # Pending, Running, Validating, Completed, Failed
    current_iteration: int = 0
    max_iterations: int = 5
    execution_profile_assigned: str = "local"  # local | cloud
    logs: List[str] = field(default_factory=list)
    git_branch: str = ""

    _source_path: str = ""
    _recursion_state: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_yaml(cls, path: str) -> "ChildCard":
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        spec = data.get("spec", {})
        metadata = data.get("metadata", {})
        status = data.get("status", {})
        card = cls(
            id=metadata.get("id"),
            parent_mother_id=metadata.get("parent_mother_id"),
            name=metadata.get("name"),
            parent_squad_id=spec.get("parent_squad_id", "squad-backend"),
            tactical_objective=spec.get("tactical_objective", ""),
            deliverables=spec.get("deliverables", []),
            validation_commands=spec.get("validation", {}).get("test_commands", []),
            rag_query=spec.get("rag_query"),
            external_ticket_id=spec.get("external_ticket_id"),
            source_platform=spec.get("source_platform"),
            depends_on=spec.get("depends_on", []),
            wave_depth=spec.get("wave_depth", 1),
            task_fingerprint=metadata.get("task_fingerprint", ""),
            worktree_path=status.get("worktree_path", ""),
            signature=metadata.get("signature"),
            phase=status.get("phase", "Pending"),
            current_iteration=status.get("current_iteration", 0),
            max_iterations=spec.get("max_iterations", 5),
            execution_profile_assigned=status.get("execution_profile_assigned", "local"),
            logs=status.get("logs", []),
            git_branch=status.get("git_branch", "")
        )
        card._source_path = os.path.abspath(path)
        return card

    def to_yaml(self, path: str = None):
        target_path = path or self._source_path
        data = {
            "apiVersion": "agent.autonomous.io/v1alpha1",
            "kind": "ChildCard",
            "metadata": {
                "id": self.id,
                "parent_mother_id": self.parent_mother_id,
                "name": self.name,
                "task_fingerprint": self.task_fingerprint,
                "signature": self.signature,
            },
            "spec": {
                "parent_squad_id": self.parent_squad_id,
                "tactical_objective": self.tactical_objective,
                "deliverables": self.deliverables,
                "max_iterations": self.max_iterations,
                "rag_query": self.rag_query,
                "external_ticket_id": self.external_ticket_id,
                "source_platform": self.source_platform,
                "depends_on": self.depends_on,
                "wave_depth": self.wave_depth,
                "validation": {
                    "test_commands": self.validation_commands
                }
            },
            "status": {
                "phase": self.phase,
                "current_iteration": self.current_iteration,
                "execution_profile_assigned": self.execution_profile_assigned,
                "logs": self.logs,
                "git_branch": self.git_branch,
                "worktree_path": self.worktree_path,
            }
        }
        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
        with open(target_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(data, f, default_flow_style=False)
