import yaml
import os
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field, asdict

@dataclass
class MotherCard:
    id: str
    name: str
    core_philosophy: str
    privacy_mode: str  # local | cloud | hybrid
    restricted_paths: List[str] = field(default_factory=list)
    banned_commands: List[str] = field(default_factory=list)
    global_context: Dict[str, Any] = field(default_factory=dict)

    # Design system every child card must comply with.
    # Examples: "IBM Carbon Design System", "Google Material Design 3",
    #           "Apple Human Interface Guidelines", "Microsoft Fluent 2"
    # Leave empty string to disable design enforcement.
    design_system: str = ""

    # Live engine status
    family_health: str = "Healthy"
    total_spend_usd: float = 0.0
    active_children: List[str] = field(default_factory=list)
    completed_children: List[str] = field(default_factory=list)
    backlog_queue: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str) -> "MotherCard":
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
        spec = data.get("spec", {})
        metadata = data.get("metadata", {})
        status = data.get("status", {})
        return cls(
            id=metadata.get("id"),
            name=metadata.get("name"),
            core_philosophy=spec.get("core_philosophy", ""),
            privacy_mode=spec.get("safety_policies", {}).get("privacy_mode", "hybrid"),
            restricted_paths=spec.get("safety_policies", {}).get("restricted_paths", []),
            banned_commands=spec.get("safety_policies", {}).get("banned_commands", []),
            global_context=spec.get("global_context", {}),
            design_system=spec.get("design_system", ""),
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
                "core_philosophy": self.core_philosophy,
                "design_system": self.design_system,
                "safety_policies": {
                    "privacy_mode": self.privacy_mode,
                    "restricted_paths": self.restricted_paths,
                    "banned_commands": self.banned_commands
                },
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
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            yaml.safe_dump(data, f, default_flow_style=False)


@dataclass
class ChildCard:
    id: str
    parent_mother_id: str
    name: str
    tactical_objective: str
    deliverables: List[Dict[str, str]] = field(default_factory=list) # e.g. [{"path": "...", "description": "..."}]
    validation_commands: List[str] = field(default_factory=list)
    
    # Engine status
    phase: str = "Pending" # Pending, Running, Validating, Completed, Failed
    current_iteration: int = 0
    max_iterations: int = 5
    execution_profile_assigned: str = "local" # local | cloud
    logs: List[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str) -> "ChildCard":
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
        spec = data.get("spec", {})
        metadata = data.get("metadata", {})
        status = data.get("status", {})
        return cls(
            id=metadata.get("id"),
            parent_mother_id=metadata.get("parent_mother_id"),
            name=metadata.get("name"),
            tactical_objective=spec.get("tactical_objective", ""),
            deliverables=spec.get("deliverables", []),
            validation_commands=spec.get("validation", {}).get("test_commands", []),
            phase=status.get("phase", "Pending"),
            current_iteration=status.get("current_iteration", 0),
            max_iterations=spec.get("max_iterations", 5),
            execution_profile_assigned=status.get("execution_profile_assigned", "local"),
            logs=status.get("logs", [])
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
                "tactical_objective": self.tactical_objective,
                "deliverables": self.deliverables,
                "max_iterations": self.max_iterations,
                "validation": {
                    "test_commands": self.validation_commands
                }
            },
            "status": {
                "phase": self.phase,
                "current_iteration": self.current_iteration,
                "execution_profile_assigned": self.execution_profile_assigned,
                "logs": self.logs
            }
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            yaml.safe_dump(data, f, default_flow_style=False)
