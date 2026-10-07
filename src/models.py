import yaml
import os
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

    # Live engine status
    family_health: str = "Healthy"
    total_spend_usd: float = 0.0
    active_children: List[str] = field(default_factory=list)
    completed_children: List[str] = field(default_factory=list)
    backlog_queue: List[Dict[str, Any]] = field(default_factory=list)

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
            family_health=status.get("family_health", "Healthy"),
            total_spend_usd=status.get("total_spend_usd", 0.0),
            active_children=status.get("active_children", []),
            completed_children=status.get("completed_children", []),
            backlog_queue=status.get("backlog_queue", [])
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
    
    # Engine status
    phase: str = "Pending"  # Pending, Running, Validating, Completed, Failed
    current_iteration: int = 0
    max_iterations: int = 5
    execution_profile_assigned: str = "local"  # local | cloud
    logs: List[str] = field(default_factory=list)
    git_branch: str = ""

    @classmethod
    def from_yaml(cls, path: str) -> "ChildCard":
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        spec = data.get("spec", {})
        metadata = data.get("metadata", {})
        status = data.get("status", {})
        return cls(
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
            phase=status.get("phase", "Pending"),
            current_iteration=status.get("current_iteration", 0),
            max_iterations=spec.get("max_iterations", 5),
            execution_profile_assigned=status.get("execution_profile_assigned", "local"),
            logs=status.get("logs", []),
            git_branch=status.get("git_branch", "")
        )

    def to_yaml(self, path: str):
        data = {
            "apiVersion": "agent.autonomous.io/v1alpha1",
            "kind": "ChildCard",
            "metadata": {
                "id": self.id,
                "parent_mother_id": self.parent_mother_id,
                "name": self.name
            },
            "spec": {
                "parent_squad_id": self.parent_squad_id,
                "tactical_objective": self.tactical_objective,
                "deliverables": self.deliverables,
                "max_iterations": self.max_iterations,
                "rag_query": self.rag_query,
                "external_ticket_id": self.external_ticket_id,
                "source_platform": self.source_platform,
                "validation": {
                    "test_commands": self.validation_commands
                }
            },
            "status": {
                "phase": self.phase,
                "current_iteration": self.current_iteration,
                "execution_profile_assigned": self.execution_profile_assigned,
                "logs": self.logs,
                "git_branch": self.git_branch
            }
        }
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(data, f, default_flow_style=False)
