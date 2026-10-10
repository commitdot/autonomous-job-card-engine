"""
OllamaAdapter
=============
Connects the Autonomous Job-Card Engine to a locally-running Ollama instance.

Default model: gemma3:27b  (Google Gemma 3 — latest public release on Ollama)
               gemma3:12b  (lighter, still excellent for coding tasks)

Install Ollama:   https://ollama.com/download
Pull the model:   ollama pull gemma3:27b

Install SDK:      pip install ollama

Optional environment variables
--------------------------------
OLLAMA_MODEL  — model tag to use    (default: gemma3:27b)
OLLAMA_HOST   — Ollama base URL     (default: http://localhost:11434)

Note on Gemma versions in Ollama
---------------------------------
As of 2025, Ollama ships Gemma 3 as the "gemma3" family — Google's latest
publicly available release. Use `ollama list` to see locally available models,
or `ollama pull gemma3:27b` to fetch the 27B parameter variant.
"""

import os
import re
import json
import uuid
import textwrap
from typing import Dict, Any, List, Optional

from .base import BaseLLMAdapter
from ..ast_skeleton import ASTSkeletonizer
from ..sandbox import extract_fault_frame

try:
    import ollama as _ollama
    _OLLAMA_AVAILABLE = True
except ImportError:
    _OLLAMA_AVAILABLE = False


DEFAULT_MODEL = "gemma3:27b"


class OllamaAdapter(BaseLLMAdapter):
    """
    Production LLM adapter backed by a local Ollama endpoint.

    Responsibilities
    ----------------
    * Builds structured, self-healing prompts from ChildCard fields.
    * Injects design-system rules (IBM Carbon, Apple HIG, Google Material, etc.)
      into every code-generation prompt when specified in the MotherCard.
    * Calls the Ollama generate API and parses file content from the response.
    * Writes deliverable files to disk inside workspace_root.
    * Sends a real repo file-tree to gap analysis for context-aware successor tasks.
    """

    def __init__(
        self,
        workspace_root: str,
        model: str = None,
        host: str = None,
        mcp_client: Optional[Any] = None,
    ):
        if not _OLLAMA_AVAILABLE:
            raise ImportError(
                "ollama package is not installed. Run: pip install ollama"
            )

        self.workspace_root = workspace_root
        self.model = model or os.getenv("OLLAMA_MODEL", DEFAULT_MODEL)
        self.host  = host  or os.getenv("OLLAMA_HOST",  "http://localhost:11434")
        self._mcp  = mcp_client  # AJEMcpClient instance, or None
        self._skeletonizer = ASTSkeletonizer(workspace_root=self.workspace_root)

        print(f"[OLLAMA] Adapter initialised — model: {self.model} @ {self.host}")
        if self._mcp:
            print("[OLLAMA] MCP self-improvement client attached.")

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
        Calls Ollama (Gemma 3) to generate or self-heal code for each deliverable,
        then writes the results to disk inside workspace_root or execution_root.
        """
        logs: List[str] = []
        created_files: List[str] = []
        target_root = execution_root or self.workspace_root

        for deliv in deliverables:
            rel_path    = deliv.get("path", "")
            description = deliv.get("description", "")
            abs_path    = os.path.join(target_root, rel_path)

            # Read existing content so the model can extend/diff it
            existing_content = ""
            if os.path.exists(abs_path):
                try:
                    with open(abs_path, "r") as f:
                        existing_content = f.read()
                except Exception:
                    pass

            prompt = self._build_code_prompt(
                tactical_objective=tactical_objective,
                rel_path=rel_path,
                description=description,
                iteration=iteration,
                previous_errors=previous_errors,
                existing_content=existing_content,
                design_system=design_system,
            )

            print(f"  [OLLAMA] Generating: {rel_path} (iteration {iteration})...")
            response_text = self._generate(prompt)
            code = self._extract_code_block(response_text, rel_path)

            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w") as f:
                f.write(code)

            created_files.append(abs_path)
            logs.append(
                f"Ollama ({self.model}) wrote {rel_path} ({len(code.splitlines())} lines)."
            )

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
        Returns up to 3 successor ChildCard definitions.
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
            Identify up to 3 missing features, unimplemented modules, or quality gaps.
            For each gap output a JSON object describing a new development task.

            Respond ONLY with a valid JSON array. No explanation, no markdown, no extra text.
            Each item must follow this exact schema:
            {{
                "id": "child-<short-slug>",
                "name": "<short task name>",
                "tactical_objective": "<clear single-sentence instruction for the coding agent>",
                "deliverables": [{{"path": "<relative/file/path>", "description": "<what to create>"}}],
                "test_commands": ["<command to validate>"]
            }}
        """).strip()

        print("  [OLLAMA] Running gap analysis on repo...")
        response_text = self._generate(prompt)
        tasks = self._parse_json_array(response_text)

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
        """Calls the Ollama generate endpoint and returns the raw response string."""
        try:
            client = _ollama.Client(host=self.host)
            response = client.generate(model=self.model, prompt=prompt)
            return response.get("response", "") if isinstance(response, dict) else str(response)
        except Exception as e:
            print(f"  [OLLAMA] API error: {e}")
            return ""

    def _build_code_prompt(
        self,
        tactical_objective: str,
        rel_path: str,
        description: str,
        iteration: int,
        previous_errors: str,
        existing_content: str,
        design_system: str,
    ) -> str:
        """
        Builds a structured coding prompt.
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
                The code you wrote in the previous iteration failed with these root-cause traceback frames.
                You MUST fix ALL of them in your new response:

                ```
                {pruned_errors.strip()}
                ```
            """).strip()

        existing_block = ""
        if existing_content:
            existing_block = textwrap.dedent(f"""
                ## Existing File Content
                The file already exists with the following content. Modify it as needed:

                ```
                {existing_content.strip()}
                ```
            """).strip()

        prompt = textwrap.dedent(f"""
            You are an expert software engineer working autonomously inside a coding agent.

            ## Task Objective
            {tactical_objective}

            ## File to Write
            Path: {rel_path}
            Description: {description}

            {design_block}

            {self_heal_block}

            {existing_block}

            ## Instructions
            - Write complete, production-quality code for the file above.
            - Include all necessary imports.
            - Do NOT include explanations or commentary outside the code block.
            - Wrap the entire output in a single ```python ... ``` code block.
        """).strip()

        return prompt

    def _extract_code_block(self, text: str, rel_path: str) -> str:
        """Extracts the first fenced code block from the model response."""
        for pattern in [r"```python\s*(.*?)```", r"```\s*(.*?)```"]:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                return match.group(1).strip()
        return text.strip()

    def _build_repo_map(self) -> str:
        """Builds a compact file-tree string of the workspace."""
        lines = []
        skip_dirs = {"__pycache__", ".git", ".jobs", "node_modules", ".venv", "venv"}
        skip_exts = {".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".tar"}

        for root, dirs, files in os.walk(self.workspace_root):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            rel_root = os.path.relpath(root, self.workspace_root)
            depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
            indent = "  " * depth
            folder_name = os.path.basename(root) if rel_root != "." else self.workspace_root
            lines.append(f"{indent}{folder_name}/")
            for fname in sorted(files):
                if os.path.splitext(fname)[1].lower() not in skip_exts:
                    lines.append(f"{indent}  {fname}")

        return "\n".join(lines)

    def _parse_json_array(self, text: str) -> list:
        """Extracts a JSON array from model output, tolerating markdown fences."""
        for pattern in [r"```json\s*(.*?)```", r"```\s*(.*?)```"]:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                text = match.group(1).strip()
                break
        array_match = re.search(r"\[.*\]", text, re.DOTALL)
        if array_match:
            try:
                return json.loads(array_match.group(0))
            except json.JSONDecodeError:
                pass
        return []
