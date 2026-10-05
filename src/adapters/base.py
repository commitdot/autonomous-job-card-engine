from abc import ABC, abstractmethod
from typing import Dict, Any, List


class BaseLLMAdapter(ABC):
    """
    Abstract base class for all LLM adapters used by the Autonomous Job-Card Engine.

    Concrete implementations (WatsonxAdapter, OllamaAdapter, etc.) must implement
    both methods below. The daemon only ever calls these two entry points, keeping
    the orchestration logic fully decoupled from the underlying model provider.
    """

    @abstractmethod
    def execute_child_card(
        self,
        tactical_objective: str,
        deliverables: List[Dict[str, str]],
        iteration: int,
        previous_errors: str = "",
    ) -> Dict[str, Any]:
        """
        Ask the LLM to fulfil a ChildCard's tactical objective.

        Args:
            tactical_objective: Natural-language task description from the ChildCard.
            deliverables:        List of {"path": str, "description": str} dicts.
            iteration:           Current self-healing iteration number (1-based).
            previous_errors:     Stdout/stderr from the last failed validation run,
                                 injected into the prompt so the model can self-heal.

        Returns:
            {
                "success":       bool,
                "logs":          List[str],   # human-readable action log
                "created_files": List[str],   # absolute paths of files written to disk
            }
        """
        ...

    @abstractmethod
    def generate_gap_analysis(self, current_repo_state: str) -> List[Dict[str, Any]]:
        """
        Analyse the current repository state and return a list of successor ChildCard
        definitions that represent logical next development steps.

        Returns a list of dicts matching the ChildCard creation schema used in daemon.py:
            [{"id", "name", "tactical_objective", "deliverables", "test_commands"}, ...]
        """
        ...
