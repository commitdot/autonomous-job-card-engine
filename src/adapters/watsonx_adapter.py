"""
WatsonxAdapter
==============
A production-ready LLM adapter that connects the Autonomous Job-Card Engine
to IBM watsonx.ai using the `ibm-watsonx-ai` SDK.

Required environment variables
--------------------------------
WATSONX_API_KEY      — IBM Cloud API key (IAM)
WATSONX_PROJECT_ID   — watsonx.ai project UUID
WATSONX_URL          — Regional endpoint, e.g. https://us-south.ml.cloud.ibm.com

Optional environment variables
--------------------------------
WATSONX_MODEL_ID     — Foundation model to use (default: ibm/granite-34b-code-instruct)

Installation
--------------------------------
pip install ibm-watsonx-ai
"""

import os
import re
import json
import uuid
import textwrap
from typing import Dict, Any, List, Optional, Sequence, Tuple

from .base import BaseLLMAdapter
from .harness_mixin import (
    ComposedHarness,
    HarnessCandidate,
    HarnessConfig,
    HarnessMixin,
)
from .patch_loader import apply_cartridges
from ..ast_skeleton import ASTSkeletonizer
from ..sandbox import extract_fault_frame

# ---------------------------------------------------------------------------
# Lazy import so the rest of AJE still works without the SDK installed
# ---------------------------------------------------------------------------
try:
    from ibm_watsonx_ai import Credentials
    from ibm_watsonx_ai.foundation_models import ModelInference
    from ibm_watsonx_ai.metanames import GenTextParamsMetaNames as GenParams
    _WATSONX_AVAILABLE = True
except ImportError:
    _WATSONX_AVAILABLE = False


# Default model — IBM Granite 34B Code Instruct is optimised for code generation,
# instruction-following, and agentic code-editing tasks.
DEFAULT_MODEL = "ibm/granite-34b-code-instruct"

# Maximum tokens to generate per LLM call
MAX_NEW_TOKENS = 2048


class WatsonxAdapter(BaseLLMAdapter, HarnessMixin):
    """
    Production LLM adapter backed by IBM watsonx.ai.

    Responsibilities
    ----------------
    * Builds structured, self-healing prompts from ChildCard fields.
    * Calls the watsonx.ai ModelInference API.
    * Parses the response to extract file contents and writes them to disk.
    * Sends a real repo file-tree to gap analysis so the model recommends
      context-aware successor tasks — not a hardcoded list.
    """

    def __init__(
        self,
        workspace_root: str,
        api_key: str = None,
        project_id: str = None,
        url: str = None,
        model_id: str = None,
        mcp_client: Optional[Any] = None,
    ):
        if not _WATSONX_AVAILABLE:
            raise ImportError(
                "ibm-watsonx-ai is not installed. Run: pip install ibm-watsonx-ai"
            )

        self.workspace_root = workspace_root
        self.model_id = model_id or os.getenv("WATSONX_MODEL_ID", DEFAULT_MODEL)

        # Resolve credentials — constructor args take priority over env vars
        resolved_api_key = api_key or os.getenv("WATSONX_API_KEY")
        resolved_project_id = project_id or os.getenv("WATSONX_PROJECT_ID")
        resolved_url = url or os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")

        if not resolved_api_key:
            raise ValueError("WATSONX_API_KEY is required. Set it as an env var or pass api_key=.")
        if not resolved_project_id:
            raise ValueError("WATSONX_PROJECT_ID is required. Set it as an env var or pass project_id=.")

        credentials = Credentials(url=resolved_url, api_key=resolved_api_key)

        self._model = ModelInference(
            model_id=self.model_id,
            credentials=credentials,
            project_id=resolved_project_id,
            params={
                GenParams.MAX_NEW_TOKENS: MAX_NEW_TOKENS,
                GenParams.TEMPERATURE: 0.2,      # Low temperature = deterministic code
                GenParams.REPETITION_PENALTY: 1.1,
            },
        )

        self._mcp = mcp_client  # AJEMcpClient instance, or None
        self._skeletonizer = ASTSkeletonizer(workspace_root=self.workspace_root)
        self._mounted_harness: Optional[HarnessConfig] = None

        print(f"[WATSONX] Adapter initialised — model: {self.model_id}")
        if self._mcp:
            print("[WATSONX] MCP self-improvement client attached.")

    # ------------------------------------------------------------------
    # Cartridge hot-swapping
    # ------------------------------------------------------------------

    def mount_cartridges(
        self,
        cartridges: Sequence[Tuple[Any, Optional[Any]]],
    ) -> None:
        """Merge all (cartridge, binding) pairs into a single HarnessConfig."""
        self._mounted_harness = apply_cartridges(HarnessConfig(), list(cartridges))

    def get_mounted_harness_context(self) -> str:
        """Return the rendered harness prompt block for mounted cartridges."""
        if self._mounted_harness is None:
            return ""
        return self._mounted_harness.render_prompt_block()

    # ------------------------------------------------------------------
    # Public interface (implements BaseLLMAdapter)
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
        """
        Calls watsonx.ai to generate or self-heal code for each deliverable,
        then writes the results to disk inside workspace_root or execution_root.
        """
        logs: List[str] = []
        created_files: List[str] = []
        target_root = execution_root or self.workspace_root

        for deliv in deliverables:
            rel_path = deliv.get("path", "")
            description = deliv.get("description", "")
            abs_path = os.path.join(target_root, rel_path)

            # Read existing content if file already exists (gives model context)
            existing_content = ""
            if os.path.exists(abs_path):
                try:
                    with open(abs_path, "r") as f:
                        existing_content = f.read()
                except Exception:
                    pass

            harness_ctx = self.get_mounted_harness_context()
            augmented_objective = (
                f"{harness_ctx}\n\n{tactical_objective}".strip()
                if harness_ctx
                else tactical_objective
            )
            prompt = self._build_code_prompt(
                tactical_objective=augmented_objective,
                rel_path=rel_path,
                description=description,
                iteration=iteration,
                previous_errors=previous_errors,
                existing_content=existing_content,
                design_system=design_system,
            )

            print(f"  [WATSONX] Generating: {rel_path} (iteration {iteration})...")
            response_text = self._generate(prompt)
            code = self._extract_code_block(response_text, rel_path)

            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w") as f:
                f.write(code)

            created_files.append(abs_path)
            logs.append(f"watsonx wrote {rel_path} ({len(code.splitlines())} lines).")

        return {
            "success": True,
            "logs": logs,
            "created_files": created_files,
        }

    def generate_gap_analysis(self, current_repo_state: str) -> List[Dict[str, Any]]:
        """
        Analyses the repository for gaps. When an MCP client is attached,
        collects live signals (issues, failing tests, coverage, CVEs, TODOs, PR
        feedback) and injects them into the prompt for precise gap detection.
        Falls back to file-tree-only analysis when no MCP client is present.
        Returns a list of ChildCard definition dicts.
        """
        repo_map = self._build_repo_map()

        # ------------------------------------------------------------------
        # Live signal collection via MCP (optional)
        # ------------------------------------------------------------------
        live_signals_block = ""
        if self._mcp:
            signals = self._mcp.collect_gap_signals()
            live_signals_block = textwrap.dedent(f"""
                ## Live Repository Signals (from MCP)
                Use these real signals as the PRIMARY source for gap identification.
                Prioritise: security alerts > failing tests > open issues > coverage gaps > TODOs.

                ### Open Issues (bugs & enhancements)
                {json.dumps(signals.get("open_issues", {}), indent=2)}

                ### Failing Tests
                {json.dumps(signals.get("failing_tests", {}), indent=2)}

                ### Coverage Report (modules below threshold)
                {json.dumps(signals.get("coverage", {}), indent=2)}

                ### Security Alerts (CVEs)
                {json.dumps(signals.get("security_alerts", {}), indent=2)}

                ### Outdated Dependencies
                {json.dumps(signals.get("outdated_deps", {}), indent=2)}

                ### TODO / FIXME Comments
                {json.dumps(signals.get("todo_comments", {}), indent=2)}

                ### Open PR Review Feedback
                {json.dumps(signals.get("pr_feedback", {}), indent=2)}
            """).strip()

        prompt = textwrap.dedent(f"""
            You are a senior software architect performing a gap analysis on a codebase.

            ## Current Repository File Tree
            {repo_map}

            ## Current State Summary
            {current_repo_state}

            {live_signals_block}

            ## Your Task
            Identify up to 3 missing features, unimplemented modules, or quality gaps in the
            codebase above. For each gap, output a JSON object describing a new development task.

            Respond ONLY with a valid JSON array. No explanation, no markdown, no extra text.
            Each item must follow this exact schema:
            {{
                "id": "child-<short-slug>",
                "name": "<short task name>",
                "tactical_objective": "<clear single-sentence instruction for the coding agent>",
                "deliverables": [{{"path": "<relative/file/path.py>", "description": "<what to create>"}}],
                "test_commands": ["<pytest or python command to validate>"]
            }}
        """).strip()

        print("  [WATSONX] Running gap analysis on repo...")
        response_text = self._generate(prompt)
        tasks = self._parse_json_array(response_text)

        # Sanitise: ensure required keys exist, assign unique ids if missing
        clean_tasks = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            if "tactical_objective" not in task or "deliverables" not in task:
                continue
            task.setdefault("id", f"child-{uuid.uuid4().hex[:8]}")
            task.setdefault("name", task["id"])
            task.setdefault("test_commands", [])
            clean_tasks.append(task)

        return clean_tasks

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _generate(self, prompt: str) -> str:
        """Calls the watsonx.ai model and returns the raw response string."""
        try:
            response = self._model.generate_text(prompt=prompt)
            return response if isinstance(response, str) else str(response)
        except Exception as e:
            print(f"  [WATSONX] API error: {e}")
            return ""

    def _build_code_prompt(
        self,
        tactical_objective: str,
        rel_path: str,
        description: str,
        iteration: int,
        previous_errors: str,
        existing_content: str,
        design_system: str = "",
    ) -> str:
        """
        Constructs a structured coding prompt.
        - Injects a MANDATORY design-system compliance block when provided.
        - On iteration > 1, injects previous validation errors for self-healing.
        """
        design_block = ""
        if design_system:
            design_block = textwrap.dedent(f"""
                ## Design System Compliance — MANDATORY
                This project strictly follows the **{design_system}** design system.
                Every UI component, token, colour, spacing unit, typography choice,
                and interaction pattern you generate MUST conform to the official
                {design_system} guidelines and specifications.
                Do NOT invent custom styles or deviate from {design_system} standards.
            """).strip()

        self_heal_block = ""
        if iteration > 1 and previous_errors:
            pruned_errors = extract_fault_frame(previous_errors, max_lines=30)
            self_heal_block = textwrap.dedent(f"""
                ## Previous Validation Errors (Self-Healing Required - Fault Localized)
                The code you wrote in the previous iteration failed validation with the following root-cause traceback frames.
                You MUST fix all of these errors in your new response:

                ```
                {pruned_errors.strip()}
                ```
            """).strip()

        existing_block = ""
        if existing_content:
            existing_block = textwrap.dedent(f"""
                ## Existing File Content
                The file already exists with the following content. Modify it as needed:

                ```python
                {existing_content.strip()}
                ```
            """).strip()

        prompt = textwrap.dedent(f"""
            You are an expert Python software engineer working autonomously inside a coding agent.

            ## Task Objective
            {tactical_objective}

            ## File to Write
            Path: {rel_path}
            Description: {description}

            {design_block}

            {self_heal_block}

            {existing_block}

            ## Instructions
            - Write complete, production-quality Python code for the file above.
            - Include all necessary imports.
            - Do NOT include explanations or commentary outside the code.
            - Wrap the entire output in a single ```python ... ``` code block.
        """).strip()

        return prompt

    def _extract_code_block(self, text: str, rel_path: str) -> str:
        """
        Extracts the first ```python ... ``` or ``` ... ``` block from the model response.
        Falls back to returning the raw text if no code fence is found.
        """
        # Try ```python first, then generic ```
        for pattern in [r"```python\s*(.*?)```", r"```\s*(.*?)```"]:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                return match.group(1).strip()

        # Last resort: return everything (model may have skipped fences)
        return text.strip()

    def _build_repo_map(self) -> str:
        """
        Walks the workspace directory and returns a compact file-tree string,
        skipping hidden dirs, __pycache__, and binary files.
        """
        lines = []
        skip_dirs = {"__pycache__", ".git", ".jobs", "node_modules", ".venv", "venv"}
        skip_exts = {".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".tar"}

        for root, dirs, files in os.walk(self.workspace_root):
            # Prune unwanted directories in-place
            dirs[:] = [d for d in dirs if d not in skip_dirs]

            rel_root = os.path.relpath(root, self.workspace_root)
            depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
            indent = "  " * depth
            folder_name = os.path.basename(root) if rel_root != "." else self.workspace_root
            lines.append(f"{indent}{folder_name}/")

            for fname in sorted(files):
                ext = os.path.splitext(fname)[1].lower()
                if ext in skip_exts:
                    continue
                lines.append(f"{indent}  {fname}")

        return "\n".join(lines)

    def _parse_json_array(self, text: str) -> list:
        """
        Extracts a JSON array from model output. Handles both raw JSON and
        JSON embedded inside markdown code fences.
        """
        # Strip markdown fences if present
        for pattern in [r"```json\s*(.*?)```", r"```\s*(.*?)```"]:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                text = match.group(1).strip()
                break

        # Find the first [...] block
        array_match = re.search(r"\[.*\]", text, re.DOTALL)
        if array_match:
            try:
                return json.loads(array_match.group(0))
            except json.JSONDecodeError:
                pass

        return []

    # ------------------------------------------------------------------
    # HarnessMixin — Propose–Probe–Compose
    # ------------------------------------------------------------------

    def propose_harness_candidates(
        self,
        tactical_objective: str,
        deliverables: List[Dict[str, str]],
        n_candidates: int = 3,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[HarnessCandidate]:
        """
        Asks watsonx.ai to generate N diverse 8-component harness configurations.

        Builds a structured prompt describing the 8 components and requests a
        JSON array of harness configs.  Falls back to sensible defaults when the
        model response cannot be parsed.
        """
        context = context or {}
        lessons: List[str] = context.get("lessons", [])
        lessons_block = ""
        if lessons:
            lessons_block = "## Institutional Lessons\n" + "\n".join(f"- {l}" for l in lessons[:5])

        deliv_list = "\n".join(
            f"  - {d.get('path','?')}: {d.get('description','')}" for d in deliverables
        )

        prompt = textwrap.dedent(f"""
            You are a harness architect for a recursive coding agent (Harness² system).
            Your job is to propose {n_candidates} DISTINCT execution harness configurations
            for the task below.  Each harness varies one or more of the 8 components.

            ## Task
            {tactical_objective}

            ## Deliverables
            {deliv_list}

            {lessons_block}

            ## 8 Harness Components
            1. system_prompt   — role and persona (string)
            2. guardrails      — hard rules (list of strings)
            3. memory          — injected context (list of strings)
            4. tool_guidance   — tool usage rules (list of strings)
            5. subagents       — child-agent instructions (list of strings)
            6. skills          — domain skills to activate (list of strings)
            7. scripts         — code templates to inject (list of strings)
            8. plugins         — third-party plugin directives (list of strings)

            Respond ONLY with a valid JSON array of exactly {n_candidates} objects.
            Each object must include only keys from the 8 components above.
            Example:
            [
              {{"system_prompt": "...", "guardrails": ["rule1"], "memory": [], ...}},
              ...
            ]
        """).strip()

        print(f"  [WATSONX] Proposing {n_candidates} harness candidates...")
        response_text = self._generate(prompt)
        raw_list = self._parse_json_array(response_text)

        candidates: List[HarnessCandidate] = []
        for i, raw in enumerate(raw_list[:n_candidates]):
            if isinstance(raw, dict):
                candidates.append(HarnessCandidate(harness=HarnessConfig.from_dict(raw)))

        # Pad with baseline candidates if the model returned fewer than requested
        while len(candidates) < n_candidates:
            i = len(candidates)
            cfg = HarnessConfig(
                system_prompt=f"Expert coding agent — variant {i + 1}. Task: {tactical_objective[:100]}",
                guardrails=["Follow existing project conventions.", "Do not break existing tests."],
            )
            if i >= 1:
                cfg.memory = [f"Objective: {tactical_objective[:80]}"]
            if i >= 2:
                cfg.skills = ["python-best-practices"]
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
        Probes a single harness candidate by injecting its 8-component block
        into the code-generation prompt and executing watsonx.ai.

        The harness block is prepended to the prompt so that the model is
        constrained by the candidate's specific configuration.
        """
        target_root = execution_root or self.workspace_root
        harness_block = candidate.harness.render_prompt_block()
        candidate.deliverables = {}
        fault_frames = []

        for deliv in deliverables:
            rel_path = deliv.get("path", "")
            description = deliv.get("description", "")
            abs_path = os.path.join(target_root, rel_path)

            existing_content = ""
            if os.path.exists(abs_path):
                try:
                    with open(abs_path, "r") as f:
                        existing_content = f.read()
                except Exception:
                    pass

            base_prompt = self._build_code_prompt(
                tactical_objective=tactical_objective,
                rel_path=rel_path,
                description=description,
                iteration=iteration,
                previous_errors="",
                existing_content=existing_content,
                design_system="",
            )

            # Prepend harness block to inject the 8 components
            full_prompt = f"{harness_block}\n\n{base_prompt}" if harness_block else base_prompt

            print(f"  [WATSONX] Probing candidate {candidate.candidate_id}: {rel_path}...")
            response_text = self._generate(full_prompt)
            code = self._extract_code_block(response_text, rel_path)

            os.makedirs(os.path.dirname(abs_path) if os.path.dirname(abs_path) else ".", exist_ok=True)
            with open(abs_path, "w") as f:
                f.write(code)

            candidate.deliverables[rel_path] = code

        candidate.status = "success"
        candidate.fault_frames = fault_frames
        candidate.score = self._score_candidate(candidate)
        candidate.stdout = f"[WATSONX] Probe {candidate.candidate_id} wrote {len(deliverables)} file(s)."
        return candidate

    def compose_harness(
        self,
        candidates: List[HarnessCandidate],
        tactical_objective: str,
        deliverables: List[Dict[str, str]],
    ) -> ComposedHarness:
        """
        Contrasts probed candidates and asks watsonx.ai to synthesise the best result.

        When multiple candidates succeeded, asks the model to select and merge the
        best deliverable content.  Falls back to score-based selection when the
        model response cannot be parsed.
        """
        winner = self._select_best_candidate(candidates)
        rationale = self._build_contrast_rationale(candidates, winner)

        if not candidates or winner is None:
            return ComposedHarness(
                winning_candidate_id=None,
                harness=HarnessConfig(),
                deliverables={},
                synthesis_rationale=rationale,
                candidates_evaluated=len(candidates),
                score=0.0,
            )

        passing = [c for c in candidates if c.status == "success"]
        if len(passing) <= 1:
            return ComposedHarness(
                winning_candidate_id=winner.candidate_id,
                harness=winner.harness,
                deliverables=winner.deliverables,
                synthesis_rationale=rationale,
                candidates_evaluated=len(candidates),
                score=winner.score,
            )

        # Build a synthesis prompt for multi-candidate contrast
        rel_paths = [d.get("path", "") for d in deliverables]
        variants_block = ""
        for c in passing:
            for path in rel_paths:
                snippet = (c.deliverables.get(path, "") or "")[:400]
                variants_block += f"\n### Candidate {c.candidate_id} — {path}\n```python\n{snippet}\n```\n"

        compose_prompt = textwrap.dedent(f"""
            You are reviewing {len(passing)} candidate implementations for:
            {tactical_objective}

            Select or merge the best implementation for each file.
            Return ONLY a JSON object mapping file paths to their final content.
            Example: {{"path/to/file.py": "...full content..."}}

            ## Candidate Implementations
            {variants_block}
        """).strip()

        print(f"  [WATSONX] Composing from {len(passing)} passing candidate(s)...")
        response_text = self._generate(compose_prompt)

        # Try to parse a JSON object from the response
        composed_deliverables = winner.deliverables.copy()
        try:
            import re as _re
            obj_match = _re.search(r"\{.*\}", response_text, _re.DOTALL)
            if obj_match:
                import json as _json
                merged = _json.loads(obj_match.group(0))
                if isinstance(merged, dict):
                    composed_deliverables = merged
        except Exception:
            pass

        return ComposedHarness(
            winning_candidate_id=winner.candidate_id,
            harness=winner.harness,
            deliverables=composed_deliverables,
            synthesis_rationale=rationale,
            candidates_evaluated=len(candidates),
            score=winner.score,
        )
