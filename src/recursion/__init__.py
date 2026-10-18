"""
Recursion Package
=================
Implements Harness² Dual-Level Recursive Agent Harnessing:
- Task-Level Recursion: Propose–Probe–Compose in isolated worktrees.
- Harnessing-Level Recursion: Meta-learning memory & alignment cascades.
"""

from .state_manager import (
    RecursionStateManager,
    CandidateProbe,
    ContrastMatrix,
    LessonLearned,
)

__all__ = [
    "RecursionStateManager",
    "CandidateProbe",
    "ContrastMatrix",
    "LessonLearned",
]
