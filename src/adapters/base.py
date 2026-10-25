from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Sequence, Tuple, Union


class BaseLLMAdapter(ABC):
    """
    Abstract base class for all LLM adapters used by the Autonomous Job-Card Engine.

    Concrete implementations (WatsonxAdapter, OllamaAdapter, etc.) must implement
    both methods below. The daemon only ever calls these two entry points, keeping
    the orchestration logic fully decoupled from the underlying model provider.

    Cartridge Hot-Swapping
    ----------------------
    Adapters may optionally support cartridge mounting via ``mount_cartridges()``.
    The daemon calls this before each child-card execution when the MotherCard has
    ``attached_cartridges``.  The default implementation is a no-op so existing
    adapter subclasses remain fully backward-compatible without any changes.
    """

    # ------------------------------------------------------------------
    # Cartridge mounting (optional — no-op default)
    # ------------------------------------------------------------------

    def mount_cartridges(
        self,
        cartridges: Sequence[Tuple[Any, Optional[Any]]],
    ) -> None:
        """
        Mount a sequence of (AdapterCartridge, Optional[AdapterBinding]) tuples
        onto this adapter so their harness patches are injected into subsequent
        ``execute_child_card()`` calls.

        The base implementation is intentionally a no-op.  Concrete adapters that
        support cartridge hot-swapping override this to merge the patches into a
        ``_mounted_harness`` field and surface the result through
        ``get_mounted_harness_context()``.

        Args:
            cartridges: Ordered list of (cartridge, binding) pairs, sorted by
                        priority (ascending) so higher-priority patches win.
        """

    def get_mounted_harness_context(self) -> str:
        """
        Return the rendered harness prompt block for all currently mounted
        cartridges, or an empty string when no cartridges are mounted.

        This is injected into every ``execute_child_card()`` prompt by
        concrete adapters that override ``mount_cartridges()``.
        """
        return ""

    @abstractmethod
    def execute_child_card(
        self,
        tactical_objective: str,
        deliverables: List[Dict[str, str]],
        iteration: int,
        previous_errors: str = "",
        design_system: str = "",
        execution_root: str = None,
    ) -> Dict[str, Any]:
        """
        Ask the LLM to fulfil a ChildCard's tactical objective.

        Args:
            tactical_objective: Natural-language task description from the ChildCard.
            deliverables:        List of {"path": str, "description": str} dicts.
            iteration:           Current self-healing iteration number (1-based).
            previous_errors:     Stdout/stderr from the last failed validation run,
                                 injected into the prompt so the model can self-heal.
            design_system:       Design system name from the MotherCard (e.g.
                                 "IBM Carbon Design System"). Injected as a MANDATORY
                                 compliance block in every prompt when non-empty.

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
