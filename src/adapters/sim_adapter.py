import os
import textwrap
from typing import Any, Dict, List, Optional

from .base import BaseLLMAdapter
from .harness_mixin import (
    ComposedHarness,
    HarnessCandidate,
    HarnessConfig,
    HarnessMixin,
)
from ..sim_llm import SimLLM


class SimAdapter(BaseLLMAdapter, HarnessMixin):
    """
    Thin adapter that wraps the existing SimLLM so the daemon remains fully
    backward-compatible when no real LLM credentials are present.

    Use this for local development, CI, and unit testing — it requires no API
    keys and produces deterministic, predictable output.

    Implements the full Harness² Propose–Probe–Compose interface with
    deterministic, offline behaviour suitable for unit testing without any
    external dependencies.
    """

    def __init__(self, workspace_root: str):
        self._sim = SimLLM(workspace_root=workspace_root)
        self.workspace_root = workspace_root

    # ------------------------------------------------------------------
    # BaseLLMAdapter interface
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # HarnessMixin — Propose–Probe–Compose (deterministic sim)
    # ------------------------------------------------------------------

    def propose_harness_candidates(
        self,
        tactical_objective: str,
        deliverables: List[Dict[str, str]],
        n_candidates: int = 3,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[HarnessCandidate]:
        """
        Generates N deterministic harness candidates for offline testing.

        Each candidate varies a different harness component so that behavioral
        contrast has meaningful differences to evaluate.  The variations are:
          - Candidate 0: minimal (system_prompt only)
          - Candidate 1: adds guardrails and memory from context
          - Candidate 2: adds tool_guidance and skills
          - Candidate k (k >= 3): cycle through single-component additions
        """
        context = context or {}
        lessons: List[str] = context.get("lessons", [])
        candidates: List[HarnessCandidate] = []

        for i in range(n_candidates):
            cfg = HarnessConfig(
                system_prompt=(
                    f"You are a deterministic coding agent executing task iteration {i + 1}. "
                    f"Objective: {tactical_objective[:120]}"
                ),
            )

            # Candidate 1+: inject guardrails and memory from context lessons
            if i >= 1 and lessons:
                cfg.guardrails = [f"Apply lesson: {l}" for l in lessons[:3]]
                cfg.memory = [f"Prior context: {l}" for l in lessons[:2]]
            elif i >= 1:
                cfg.guardrails = [
                    "Do not introduce regressions.",
                    "Follow existing code style.",
                ]
                cfg.memory = [f"Objective: {tactical_objective[:80]}"]

            # Candidate 2+: add tool guidance and skills
            if i >= 2:
                cfg.tool_guidance = [
                    "Prefer stdlib over third-party where possible.",
                    "Validate output with assertions.",
                ]
                cfg.skills = ["python-best-practices", "test-driven-development"]

            candidates.append(HarnessCandidate(harness=cfg))

        return candidates

    def probe_execution(
        self,
        candidate: HarnessCandidate,
        tactical_objective: str,
        deliverables: List[Dict[str, str]],
        iteration: int = 1,
        execution_root: Optional[str] = None,
    ) -> HarnessCandidate:
        """
        Simulates probe execution deterministically.

        Generates stub file content for each deliverable using the harness
        config's rendered prompt block as a header comment, then writes the
        files to execution_root (or workspace_root).  Always succeeds for
        odd-numbered candidates (0, 2, …) and fails for even-indexed ones
        (1, 3, …) when the harness has no guardrails — purely to give the
        compose step meaningful contrast material.
        """
        target_root = execution_root or self.workspace_root
        harness_block = candidate.harness.render_prompt_block()

        # Determine simulated success: candidates with guardrails pass,
        # those without (minimal candidate 0 clone) simulate a fault.
        has_guardrails = bool(candidate.harness.guardrails)
        simulated_success = has_guardrails or bool(candidate.harness.skills)

        candidate.deliverables = {}
        fault_frames = []

        for deliv in deliverables:
            rel_path = deliv.get("path", "output.py")
            description = deliv.get("description", "")
            abs_path = os.path.join(target_root, rel_path)

            stub_content = textwrap.dedent(f"""\
                # === Harness² Sim Probe ===
                # Candidate: {candidate.candidate_id}
                # Objective: {tactical_objective[:80]}
                # Deliverable: {description[:80]}
                # Harness components active:
                #   system_prompt: {'yes' if candidate.harness.system_prompt else 'no'}
                #   guardrails:    {len(candidate.harness.guardrails)} rule(s)
                #   memory:        {len(candidate.harness.memory)} item(s)
                #   tool_guidance: {len(candidate.harness.tool_guidance)} item(s)
                #   skills:        {len(candidate.harness.skills)} skill(s)
                #   scripts:       {len(candidate.harness.scripts)} script(s)
                #   plugins:       {len(candidate.harness.plugins)} plugin(s)
                #
                # Harness prompt block:
                # {harness_block.replace(chr(10), chr(10) + '# ')}
                # === end of harness block ===

                # Stub implementation for: {rel_path}
                def stub():
                    \"\"\"Auto-generated stub by SimAdapter (Harness² probe).\"\"\"
                    pass
            """)

            os.makedirs(os.path.dirname(abs_path) if os.path.dirname(abs_path) else ".", exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(stub_content)

            candidate.deliverables[rel_path] = stub_content

            if not simulated_success:
                fault_frames.append({
                    "file": rel_path,
                    "line": "stub():1",
                    "error": "SimulatedFaultError: minimal harness produced no guardrails",
                })

        candidate.status = "success" if simulated_success else "failed"
        candidate.fault_frames = fault_frames
        candidate.score = self._score_candidate(candidate)

        if simulated_success:
            candidate.stdout = f"[SIM] Probe {candidate.candidate_id} OK — {len(deliverables)} deliverable(s) written."
            candidate.stderr = ""
        else:
            candidate.stdout = ""
            candidate.stderr = f"[SIM] Probe {candidate.candidate_id} FAILED — minimal harness, no guardrails."

        return candidate

    def compose_harness(
        self,
        candidates: List[HarnessCandidate],
        tactical_objective: str,
        deliverables: List[Dict[str, str]],
    ) -> ComposedHarness:
        """
        Contrasts probed candidates and selects the best-scoring one.

        For the sim adapter the composition is purely score-based: pick the
        winner and copy its deliverables.  No merging or LLM synthesis occurs —
        that is left to the real adapters.
        """
        winner = self._select_best_candidate(candidates)
        rationale = self._build_contrast_rationale(candidates, winner)

        if winner is None:
            # All probes failed — return empty composition with first candidate's harness
            first_harness = candidates[0].harness if candidates else HarnessConfig()
            return ComposedHarness(
                winning_candidate_id=None,
                harness=first_harness,
                deliverables={},
                synthesis_rationale=rationale,
                candidates_evaluated=len(candidates),
                score=0.0,
            )

        return ComposedHarness(
            winning_candidate_id=winner.candidate_id,
            harness=winner.harness,
            deliverables=winner.deliverables,
            synthesis_rationale=rationale,
            candidates_evaluated=len(candidates),
            score=winner.score,
        )
