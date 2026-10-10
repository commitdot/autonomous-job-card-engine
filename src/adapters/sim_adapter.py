from typing import Dict, Any, List

from .base import BaseLLMAdapter
from ..sim_llm import SimLLM


class SimAdapter(BaseLLMAdapter):
    """
    Thin adapter that wraps the existing SimLLM so the daemon remains fully
    backward-compatible when no real LLM credentials are present.

    Use this for local development, CI, and unit testing — it requires no API
    keys and produces deterministic, predictable output.
    """

    def __init__(self, workspace_root: str):
        self._sim = SimLLM(workspace_root=workspace_root)

    def execute_child_card(
        self,
        tactical_objective: str,
        deliverables: List[Dict[str, str]],
        iteration: int,
        previous_errors: str = "",
        design_system: str = "",
        execution_root: str = None,
    ) -> Dict[str, Any]:
        # SimLLM is a deterministic stub and does not use design_system,
        # but we accept the param to satisfy the BaseLLMAdapter interface.
        return self._sim.execute_child_card(
            tactical_objective=tactical_objective,
            deliverables=deliverables,
            iteration=iteration,
            workspace_root=execution_root,
        )

    def generate_gap_analysis(self, current_repo_state: str) -> List[Dict[str, Any]]:
        return self._sim.generate_gap_analysis(current_repo_state=current_repo_state)
