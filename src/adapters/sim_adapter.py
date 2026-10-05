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
    ) -> Dict[str, Any]:
        return self._sim.execute_child_card(
            tactical_objective=tactical_objective,
            deliverables=deliverables,
            iteration=iteration,
        )

    def generate_gap_analysis(self, current_repo_state: str) -> List[Dict[str, Any]]:
        return self._sim.generate_gap_analysis(current_repo_state=current_repo_state)
