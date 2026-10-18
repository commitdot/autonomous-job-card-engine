"""
HarnessMixin — Modular 8-Component Harness Adapter Mixin
=========================================================
Defines the editable 8-component harness data model and the abstract
Propose–Probe–Compose interface that all LLM adapters can implement to
participate in Harness² task-level recursion.

The 8 harness components (from the Harness² paper) are:
  1. system_prompt    — Core role and persona instructions
  2. guardrails       — Hard constraints and safety rules
  3. memory           — Injected context from prior turns / RAG retrieval
  4. tool_guidance    — Tool usage rules and available-tool descriptions
  5. subagents        — Child-agent delegation instructions
  6. skills           — Domain-specific skill modules to activate
  7. scripts          — Executable scripts / code templates to inject
  8. plugins          — Third-party plugin directives

Usage
-----
Concrete adapters inherit from both BaseLLMAdapter and HarnessMixin::

    class OllamaAdapter(BaseLLMAdapter, HarnessMixin):
        def propose_harness_candidates(self, ...): ...
        def probe_execution(self, ...): ...
        def compose_harness(self, ...): ...

When `enable_harness2=False` (or the mixin methods are not called by the
daemon), all behaviour falls back gracefully to the existing single-path
execution — there is zero overhead on the System 1 fast-path.
"""

from __future__ import annotations

import time
import uuid
from abc import abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 8-Component Harness Data Model
# ---------------------------------------------------------------------------

@dataclass
class HarnessConfig:
    """
    Fully editable 8-component harness configuration.
    Each field maps to one of the Harness² harness components.
    An empty string/list means the component is disabled for this candidate.
    """
    # Component 1 — System Prompt
    system_prompt: str = ""
    # Component 2 — Guardrails (hard constraints)
    guardrails: List[str] = field(default_factory=list)
    # Component 3 — Memory (RAG snippets / prior-turn context)
    memory: List[str] = field(default_factory=list)
    # Component 4 — Tool Guidance
    tool_guidance: List[str] = field(default_factory=list)
    # Component 5 — Subagents (delegation instructions)
    subagents: List[str] = field(default_factory=list)
    # Component 6 — Skills (domain-skill module names)
    skills: List[str] = field(default_factory=list)
    # Component 7 — Scripts (code templates / shell snippets)
    scripts: List[str] = field(default_factory=list)
    # Component 8 — Plugins (third-party plugin directives)
    plugins: List[str] = field(default_factory=list)
    # Arbitrary extra metadata — not rendered into prompts
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HarnessConfig":
        valid_fields = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in valid_fields})

    def render_prompt_block(self) -> str:
        """
        Renders the 8 components into a single structured prompt block that
        can be prepended to any task-level prompt.
        """
        parts: List[str] = []

        if self.system_prompt:
            parts.append(f"## System Prompt\n{self.system_prompt}")

        if self.guardrails:
            rules = "\n".join(f"- {r}" for r in self.guardrails)
            parts.append(f"## Guardrails (MANDATORY)\n{rules}")

        if self.memory:
            ctx = "\n".join(f"- {m}" for m in self.memory)
            parts.append(f"## Context Memory\n{ctx}")

        if self.tool_guidance:
            tg = "\n".join(f"- {t}" for t in self.tool_guidance)
            parts.append(f"## Tool Guidance\n{tg}")

        if self.subagents:
            sa = "\n".join(f"- {s}" for s in self.subagents)
            parts.append(f"## Subagent Delegation\n{sa}")

        if self.skills:
            sk = "\n".join(f"- {s}" for s in self.skills)
            parts.append(f"## Active Skills\n{sk}")

        if self.scripts:
            sc = "\n".join(f"```\n{s}\n```" for s in self.scripts)
            parts.append(f"## Injected Scripts\n{sc}")

        if self.plugins:
            pl = "\n".join(f"- {p}" for p in self.plugins)
            parts.append(f"## Plugin Directives\n{pl}")

        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Candidate Harness — wraps a HarnessConfig with probe-result bookkeeping
# ---------------------------------------------------------------------------

@dataclass
class HarnessCandidate:
    """
    A single harness candidate produced by `propose_harness_candidates()`.
    After probing it carries the execution result for contrast scoring.
    """
    candidate_id: str = field(default_factory=lambda: f"hc-{uuid.uuid4().hex[:8]}")
    harness: HarnessConfig = field(default_factory=HarnessConfig)
    # Populated after probe_execution()
    status: str = "pending"           # pending | success | failed | error
    score: float = 0.0                # Higher is better
    stdout: str = ""
    stderr: str = ""
    fault_frames: List[Dict[str, Any]] = field(default_factory=list)
    deliverables: Dict[str, str] = field(default_factory=dict)  # rel_path -> content
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["harness"] = self.harness.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HarnessCandidate":
        raw_harness = data.pop("harness", {})
        valid = cls.__dataclass_fields__.keys()
        obj = cls(**{k: v for k, v in data.items() if k in valid})
        obj.harness = HarnessConfig.from_dict(raw_harness)
        return obj


# ---------------------------------------------------------------------------
# Composition Result
# ---------------------------------------------------------------------------

@dataclass
class ComposedHarness:
    """
    Final harness produced by `compose_harness()` after behavioral contrast.
    Contains the winning/merged deliverables and the rationale for selection.
    """
    winning_candidate_id: Optional[str] = None
    harness: HarnessConfig = field(default_factory=HarnessConfig)
    deliverables: Dict[str, str] = field(default_factory=dict)  # rel_path -> content
    synthesis_rationale: str = ""
    candidates_evaluated: int = 0
    score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["harness"] = self.harness.to_dict()
        return d


# ---------------------------------------------------------------------------
# HarnessMixin — Abstract Propose–Probe–Compose interface
# ---------------------------------------------------------------------------

class HarnessMixin:
    """
    Mixin that adds the Harness² Propose–Probe–Compose interface to any
    LLM adapter.  Concrete adapters override the three abstract methods;
    callers (daemon) should check `supports_harness2()` before invoking them.

    The mixin is deliberately dependency-free — it does NOT import from
    BaseLLMAdapter — so it can be composed freely without MRO conflicts.
    """

    def supports_harness2(self) -> bool:
        """
        Returns True when this adapter has a working Harness² implementation.
        Adapters that inherit the mixin but do NOT override the abstract methods
        should return False so the daemon falls back to single-path execution.
        """
        return True

    @abstractmethod
    def propose_harness_candidates(
        self,
        tactical_objective: str,
        deliverables: List[Dict[str, str]],
        n_candidates: int = 3,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[HarnessCandidate]:
        """
        **Propose** — Generate N diverse harness configurations for the given task.

        Each candidate varies one or more of the 8 components to explore a
        different execution strategy.  The daemon will feed these into
        `probe_execution()` for isolated evaluation.

        Args:
            tactical_objective: Natural-language task description.
            deliverables:        List of {"path", "description"} dicts.
            n_candidates:        How many distinct harness variants to propose.
            context:             Optional dict with extra metadata (e.g. lessons
                                 from RecursionStateManager).

        Returns:
            List of HarnessCandidate objects with `harness` populated and
            `status == "pending"`.
        """
        ...

    @abstractmethod
    def probe_execution(
        self,
        candidate: HarnessCandidate,
        tactical_objective: str,
        deliverables: List[Dict[str, str]],
        iteration: int = 1,
        execution_root: Optional[str] = None,
    ) -> HarnessCandidate:
        """
        **Probe** — Execute the given harness candidate and record the result.

        Runs the candidate in isolation (worktree or temp dir), captures
        stdout/stderr, evaluates test outcomes, and returns the candidate
        with `status`, `score`, `fault_frames`, and `deliverables` filled in.

        Args:
            candidate:           A HarnessCandidate returned by propose step.
            tactical_objective:  Task description.
            deliverables:        Expected output deliverables.
            iteration:           Self-healing iteration number (1-based).
            execution_root:      Directory in which to write deliverables.
                                 Defaults to adapter's workspace_root.

        Returns:
            The same HarnessCandidate instance with probe results filled in.
        """
        ...

    @abstractmethod
    def compose_harness(
        self,
        candidates: List[HarnessCandidate],
        tactical_objective: str,
        deliverables: List[Dict[str, str]],
    ) -> ComposedHarness:
        """
        **Compose** — Contrast probed candidates and synthesise an optimal harness.

        Performs behavioral contrast across the probed candidates: identifies
        fault frames in failing runs, extracts passing code from successful runs,
        and merges/selects deliverables to form the best composite result.

        Args:
            candidates:          List of probed HarnessCandidates (status filled).
            tactical_objective:  Task description.
            deliverables:        Expected output deliverables.

        Returns:
            A ComposedHarness with the winning configuration and merged deliverables.
        """
        ...

    # ------------------------------------------------------------------
    # Shared helpers available to all concrete implementations
    # ------------------------------------------------------------------

    def _score_candidate(self, candidate: HarnessCandidate) -> float:
        """
        Default scoring heuristic: 1.0 for success, 0.0 for failure,
        penalised by fault-frame count.  Adapters may override for
        test-suite-aware scoring.
        """
        if candidate.status == "success":
            penalty = min(len(candidate.fault_frames) * 0.1, 0.5)
            return max(1.0 - penalty, 0.5)
        return 0.0

    def _select_best_candidate(
        self, candidates: List[HarnessCandidate]
    ) -> Optional[HarnessCandidate]:
        """Returns the highest-scoring candidate, or None if the list is empty."""
        probed = [c for c in candidates if c.status != "pending"]
        if not probed:
            return None
        return max(probed, key=lambda c: c.score)

    def _build_contrast_rationale(
        self, candidates: List[HarnessCandidate], winner: Optional[HarnessCandidate]
    ) -> str:
        """
        Produces a human-readable rationale string summarising the contrast.
        """
        total = len(candidates)
        passed = sum(1 for c in candidates if c.status == "success")
        failed = total - passed
        winner_id = winner.candidate_id if winner else "none"

        lines = [
            f"Evaluated {total} candidate(s): {passed} passed, {failed} failed.",
            f"Selected winner: {winner_id}",
        ]
        if winner:
            lines.append(f"Winner score: {winner.score:.2f}")
        for c in candidates:
            if c.status != "success" and c.fault_frames:
                frames = "; ".join(
                    f.get("line", "") for f in c.fault_frames[:2] if isinstance(f, dict)
                )
                lines.append(f"  [{c.candidate_id}] fault frames: {frames}")
        return "\n".join(lines)
