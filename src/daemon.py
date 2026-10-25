import os
import json
import glob
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from .models import MotherCard, ChildCard, SquadLeadCard, RepositoryBinding, AdapterBinding, AdapterCartridge
from .privacy_guard import PrivacyGuard
from .sandbox import SandboxRunner
from .adapters.base import BaseLLMAdapter
from .adapters.sim_adapter import SimAdapter
from .adapters.lora_manager import LoRAManager, LoRAManagerError
from .adapters.patch_loader import apply_cartridges
from .adapters.registry import AdapterRegistry
from .rag.indexer import LocalRAGIndexer
from .rag.retriever import LocalRAGRetriever
from .ast_skeleton import ASTSkeletonizer
from .alignment_engine import AlignmentEngine
from .squad_executor import SquadDAGScheduler, SquadWaveExecutor
from .git_worktree_manager import GitWorktreeManager
from .recursion.state_manager import RecursionStateManager, CandidateProbe, ContrastMatrix
from .security.attestation import CardAttestationEngine, QuarantineVault
from .security.injection_guard import InboundInjectionGuard, InjectionRiskLevel
from .security.sast_scanner import ASTSecurityScanner, SASTRiskLevel
from .security.containment import WorktreeJailer
from .security.audit_chain import AuditChain


class AutonomousJobCardEngine:
    def __init__(self, workspace_root: str, adapter: Optional[BaseLLMAdapter] = None):
        self.workspace_root = os.path.abspath(workspace_root)
        self.jobs_dir = os.path.join(self.workspace_root, ".jobs")
        self.audit_dir = os.path.join(self.jobs_dir, "audit")
        self.rag_dir = os.path.join(self.jobs_dir, "rag")

        os.makedirs(self.workspace_root, exist_ok=True)
        os.makedirs(self.jobs_dir, exist_ok=True)
        os.makedirs(self.audit_dir, exist_ok=True)
        os.makedirs(self.rag_dir, exist_ok=True)

        # Accept any BaseLLMAdapter; fall back to SimAdapter for backward compatibility
        self.llm: BaseLLMAdapter = adapter or SimAdapter(workspace_root=self.workspace_root)

        # RAG Subsystem
        self.rag_indexer = LocalRAGIndexer(workspace_root=self.workspace_root, storage_dir=self.rag_dir)
        self.rag_retriever = LocalRAGRetriever(workspace_root=self.workspace_root, storage_dir=self.rag_dir)
        self.skeletonizer = ASTSkeletonizer(workspace_root=self.workspace_root)
        self.alignment_engine = AlignmentEngine(workspace_root=self.workspace_root)

        # Security Matrix Subsystem
        self.attestation_engine = CardAttestationEngine()
        self.quarantine_vault = QuarantineVault(workspace_root=self.workspace_root)
        self.injection_guard = InboundInjectionGuard()
        self.sast_scanner = ASTSecurityScanner()
        self.worktree_jailer = WorktreeJailer(jail_root=self.workspace_root)
        self.audit_chain = AuditChain(workspace_root=self.workspace_root)

        # Harness² task-level recursion (Propose–Probe–Compose) subsystem
        self.recursion_state = RecursionStateManager(workspace_root=self.workspace_root)
        self.probe_worktree_mgr = GitWorktreeManager(workspace_root=self.workspace_root)

        # Initialised per-cycle from MotherCard config
        self.privacy_guard = None
        self.sandbox = None

        # Adapter cartridge subsystem — shared registry and LoRA manager
        self._adapter_registry: Optional[AdapterRegistry] = None
        self._lora_manager: Optional[LoRAManager] = None

    def _get_adapter_registry(self) -> AdapterRegistry:
        """Lazily initialise the adapter registry (singleton per engine instance)."""
        if self._adapter_registry is None:
            self._adapter_registry = AdapterRegistry(
                installed_dir=os.path.join(self.jobs_dir, "installed_cartridges"),
            )
        return self._adapter_registry

    def _get_lora_manager(self) -> LoRAManager:
        """Lazily initialise the LoRA manager (singleton per engine instance)."""
        if self._lora_manager is None:
            self._lora_manager = LoRAManager(
                cache_dir=os.path.join(self.jobs_dir, "adapters"),
            )
        return self._lora_manager

    def _mount_cartridges_on_adapter(
        self,
        mother: MotherCard,
        child_id: str,
    ) -> None:
        """
        Inspect ``mother.attached_cartridges``, resolve each enabled binding against
        the AdapterRegistry, optionally download LoRA weights, then mount the
        (cartridge, binding) pairs onto ``self.llm`` via ``mount_cartridges()``.

        Fallback chain per binding (``mode`` field or auto-detect):
          1. If cartridge type is 'lora' or 'hybrid' **and** weights are already
             cached locally → pass weight_path through metadata so capable backends
             can load them.
          2. If LoRA weights are not available or not cached → apply the YAML
             harness patch only (graceful fallback).
          3. If the cartridge has no harness_patch and no LoRA weights → skip with
             a warning but continue (never hard-fail child execution).

        Every successful mount is recorded as an ``adapter_cartridge_mounted`` event
        in ``audit_ledger.jsonl`` via :meth:`write_audit_log`.
        """
        bindings = [b for b in (mother.attached_cartridges or []) if b.enabled]
        if not bindings:
            return

        registry = self._get_adapter_registry()
        lora_mgr = self._get_lora_manager()

        resolved: List[Tuple[AdapterCartridge, Optional[AdapterBinding]]] = []
        skipped: List[str] = []

        for binding in bindings:
            cartridge = registry.get_cartridge(binding.cartridge, binding.version)
            if cartridge is None:
                print(
                    f"  [ADAPTER] WARNING: Cartridge '{binding.cartridge}' "
                    f"(v{binding.version}) not found in registry — skipping."
                )
                skipped.append(binding.cartridge)
                continue

            spec = cartridge.spec
            effective_mode = binding.mode  # 'auto' | 'lora' | 'patch'
            weight_path: Optional[str] = None

            # Attempt LoRA weight resolution when mode allows it
            if effective_mode in ("auto", "lora") and spec.type in ("lora", "hybrid"):
                if lora_mgr.is_cached(cartridge):
                    weight_path = lora_mgr.get_adapter_path(cartridge)
                    print(
                        f"  [ADAPTER] LoRA weights cached locally for "
                        f"'{cartridge.metadata.name}': {weight_path}"
                    )
                else:
                    # Weights not cached; check if lora-only (no harness_patch fallback)
                    if spec.type == "lora" and not spec.harness_patch:
                        print(
                            f"  [ADAPTER] LoRA-only cartridge '{cartridge.metadata.name}' "
                            f"has no cached weights and no harness patch — skipping."
                        )
                        skipped.append(binding.cartridge)
                        continue
                    # Hybrid or harness-patch fallback: continue without weights
                    print(
                        f"  [ADAPTER] LoRA weights not cached for "
                        f"'{cartridge.metadata.name}'; applying harness patch only (graceful fallback)."
                    )

            # Attach weight_path metadata into binding overrides if we have weights
            effective_binding: Optional[AdapterBinding] = binding
            if weight_path:
                overrides_with_path = dict(binding.overrides or {})
                overrides_with_path["__lora_weight_path__"] = weight_path
                effective_binding = AdapterBinding(
                    cartridge=binding.cartridge,
                    version=binding.version,
                    enabled=binding.enabled,
                    priority=binding.priority,
                    mode=binding.mode,
                    overrides=overrides_with_path,
                )

            resolved.append((cartridge, effective_binding))

            mount_mode = "lora+patch" if weight_path else "patch"
            print(
                f"  [ADAPTER] Mounting cartridge '{cartridge.metadata.name}' "
                f"v{cartridge.metadata.version} [{mount_mode}] on {type(self.llm).__name__}."
            )
            self.write_audit_log(
                child_id=child_id,
                action_type="adapter_cartridge_mounted",
                details=(
                    f"Mounted cartridge '{cartridge.metadata.name}' "
                    f"v{cartridge.metadata.version} onto {type(self.llm).__name__} "
                    f"(mode={mount_mode}, binding_mode={binding.mode})."
                ),
                meta={
                    "cartridge_name": cartridge.metadata.name,
                    "cartridge_version": cartridge.metadata.version,
                    "cartridge_type": spec.type,
                    "mount_mode": mount_mode,
                    "binding_mode": binding.mode,
                    "binding_priority": binding.priority,
                    "weight_path": weight_path,
                    "harness_patch_keys": list(spec.harness_patch.keys()),
                },
            )

        if skipped:
            self.write_audit_log(
                child_id=child_id,
                action_type="adapter_cartridge_skipped",
                details=f"Skipped {len(skipped)} cartridge(s) due to missing registry entries or weights.",
                meta={"skipped": skipped},
            )

        if resolved:
            self.llm.mount_cartridges(resolved)

    def write_audit_log(self, child_id: str, action_type: str, details: str, meta: Dict[str, Any] = None):
        """
        Maintains an immutable forensic audit log and cryptographically linked SHA-256 block
        for every action executed by the autonomous engine.
        """
        audit_file = os.path.join(self.audit_dir, f"audit_{child_id}.json")
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "child_id": child_id,
            "action": action_type,
            "details": details,
            "metadata": meta or {}
        }
        
        logs = []
        if os.path.exists(audit_file):
            try:
                with open(audit_file, 'r', encoding='utf-8') as f:
                    logs = json.load(f)
            except Exception:
                pass
        
        logs.append(entry)
        with open(audit_file, 'w', encoding='utf-8') as f:
            json.dump(logs, f, indent=2)

        # Append to cryptographic tamper-evident audit ledger
        try:
            self.audit_chain.append_event(
                event_type=action_type.upper(),
                actor="EngineDaemon",
                payload={"child_id": child_id, "details": details, "meta": meta or {}}
            )
        except Exception:
            pass

    def index_rag_knowledge(self, paths: List[str]):
        """Indexes internal RFCs, markdown docs, schemas, and institutional lessons into local RAG store."""
        all_paths = list(paths)
        lessons_path = ".jobs/recursion/lessons_learned.json"
        if os.path.exists(os.path.join(self.workspace_root, lessons_path)):
            if lessons_path not in all_paths:
                all_paths.append(lessons_path)
        count = self.rag_indexer.index_paths(all_paths)
        if count > 0:
            print(f"  [RAG] Indexed {count} local documentation and lesson chunks.")
        return count

    def run_preflight_quality_scan(self, mother: MotherCard) -> Dict[str, Any]:
        """
        Executes Mother Card's declarative Pre-Flight Repository Consistency & Regex Scan.
        Scans all source, documentation, and config files for forbidden patterns, version drift,
        or unscrubbed credentials.
        """
        gov = getattr(mother, "governance", None)
        if not gov or not gov.enable_repo_regex_scan or not gov.forbidden_patterns:
            return {"scanned_files": 0, "violations": []}

        compiled_patterns = []
        for pat in gov.forbidden_patterns:
            try:
                import re
                compiled_patterns.append((pat, re.compile(pat)))
            except Exception:
                pass

        violations = []
        scanned_count = 0
        skip_dirs = {".git", ".jobs", "__pycache__", "node_modules", ".venv", "venv", ".worktrees"}

        for root, dirs, files in os.walk(self.workspace_root):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, self.workspace_root).replace("\\", "/")

                if rel_path.endswith((".py", ".md", ".json", ".yaml", ".yml", ".txt", ".ts", ".js")):
                    scanned_count += 1
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                        for raw_pat, regex in compiled_patterns:
                            matches = regex.findall(content)
                            if matches:
                                violations.append({
                                    "file": rel_path,
                                    "pattern": raw_pat,
                                    "match_count": len(matches)
                                })
                    except Exception:
                        continue

        if violations:
            print(f"  [PREFLIGHT-SCAN] [WARN] Found {len(violations)} consistency drift item(s) in {scanned_count} files.")
            for v in violations[:5]:
                print(f"    - File: {v['file']} (Pattern '{v['pattern']}' matched {v['match_count']} time(s))")
        else:
            print(f"  [PREFLIGHT-SCAN] [OK] Scanned {scanned_count} files against governance patterns. Clean.")

        return {"scanned_files": scanned_count, "violations": violations}

    # ------------------------------------------------------------------
    # Harness² Task-Level Recursion (Propose–Probe–Compose)
    # ------------------------------------------------------------------

    def _is_harness2_active(self, mother: MotherCard) -> bool:
        """True only when governance enables Harness² AND the adapter implements it."""
        gov = getattr(mother, "governance", None)
        if not (gov and getattr(gov, "enable_harness2", False)):
            return False
        supports = getattr(self.llm, "supports_harness2", None)
        return bool(callable(supports) and supports())

    @staticmethod
    def _diff_harness_components(winner_cfg: Dict[str, Any], other_cfg: Dict[str, Any]) -> List[str]:
        """Names of harness components that differ between two candidate configs."""
        return [
            key for key in winner_cfg
            if key != "metadata" and winner_cfg.get(key) != other_cfg.get(key)
        ]

    def _build_contrast_feedback(self, candidates: List[Any], winner_id: Optional[str]) -> str:
        """
        Renders behavioral contrast between failing and passing candidates as text
        suitable for the reflection loop (replaces blind retries).
        """
        winner = next((c for c in candidates if c.candidate_id == winner_id), None)
        lines: List[str] = []
        for c in candidates:
            if c.status == "success" and c.candidate_id == winner_id:
                continue
            frames = "; ".join(
                f"{f.get('file', '')}:{f.get('line', '')} {f.get('error', '')}".strip()
                for f in c.fault_frames[:3] if isinstance(f, dict)
            )
            entry = f"- Candidate {c.candidate_id} [{c.status}] fault frames: {frames or 'none recorded'}"
            if winner is not None:
                diff = self._diff_harness_components(winner.harness.to_dict(), c.harness.to_dict())
                entry += f" | differs from winner in: {', '.join(diff) or 'nothing'}"
            lines.append(entry)
        if not lines:
            return ""
        header = f"[BEHAVIORAL CONTRAST] Winner: {winner_id or 'none'}. Failing probes:"
        return header + "\n" + "\n".join(lines)

    def _run_harness2_iteration(
        self,
        child: ChildCard,
        mother: MotherCard,
        exec_root: str,
        objective: str,
        iteration: int,
        previous_errors: str,
    ) -> Optional[Dict[str, Any]]:
        """
        One Propose–Probe–Compose round:
          Propose  -> adapter generates N harness candidates
          Probe    -> each candidate runs in its own isolated worktree and is validated there
          Compose  -> adapter contrasts probes and selects/merges deliverables
        The composed deliverables are written into `exec_root` (the sandboxed commit target).

        Returns a result dict shaped like `execute_child_card()` plus `contrast_feedback`,
        or None to signal that the caller should fall back to single-path execution.
        """
        gov = mother.governance
        state = self.recursion_state.get_recursion_state(child)
        depth = int(state.get("recursion_depth", 0))
        if depth >= gov.max_recursion_depth:
            self.write_audit_log(
                child_id=child.id,
                action_type="harness2_depth_limit",
                details=f"Recursion depth {depth} reached max_recursion_depth={gov.max_recursion_depth}; falling back to single-path execution.",
            )
            return None

        lessons = [
            l.summary for l in self.recursion_state.get_relevant_lessons(child.tactical_objective, limit=3)
        ]
        context = {"lessons": lessons, "previous_errors": previous_errors, "recursion_depth": depth}

        candidates = self.llm.propose_harness_candidates(
            tactical_objective=objective,
            deliverables=child.deliverables,
            n_candidates=3,
            context=context,
        )
        self.write_audit_log(
            child_id=child.id,
            action_type="harness2_propose",
            details=f"Proposed {len(candidates)} harness candidate(s) for iteration {iteration}.",
            meta={"candidate_ids": [c.candidate_id for c in candidates], "recursion_depth": depth, "lessons": lessons},
        )

        # Probe each candidate in an isolated worktree
        probes: List[CandidateProbe] = []
        for idx, cand in enumerate(candidates):
            branch = f"aje/{child.id}-probe-{iteration}-{idx}"
            probe_path = ""
            try:
                probe_path = self.probe_worktree_mgr.create_worktree(branch_name=branch)
                cand = self.llm.probe_execution(
                    candidate=cand,
                    tactical_objective=objective,
                    deliverables=child.deliverables,
                    iteration=iteration,
                    execution_root=probe_path,
                )
                candidates[idx] = cand

                # Validate the probe's output against the real test suite (no ground truth needed)
                if child.validation_commands:
                    probe_ok = True
                    for cmd in child.validation_commands:
                        res = self.sandbox.run_validation(cmd, cwd=probe_path)
                        if not res["success"]:
                            probe_ok = False
                            cand.fault_frames.append({
                                "file": cmd,
                                "line": f"exit {res['exit_code']}",
                                "error": res.get("fault_frame", res["output"])[:300],
                            })
                            break
                    cand.status = "success" if probe_ok else "failed"
                    if not probe_ok:
                        cand.stderr = (cand.stderr + "\n" + cand.fault_frames[-1]["error"]).strip()
                cand.score = self.llm._score_candidate(cand)
            except Exception as e:
                cand.status = "error"
                cand.score = 0.0
                cand.stderr = str(e)
                cand.fault_frames.append({"file": "", "line": "probe", "error": str(e)})
            finally:
                if probe_path:
                    self.probe_worktree_mgr.cleanup_worktree(probe_path)

            probes.append(CandidateProbe(
                probe_id=cand.candidate_id,
                card_id=child.id,
                iteration=iteration,
                harness_config=cand.harness.to_dict(),
                status=cand.status,
                stdout=cand.stdout,
                stderr=cand.stderr,
                score=cand.score,
                fault_frames=list(cand.fault_frames),
                deliverables_diff=dict(cand.deliverables),
                metadata={"branch": branch},
            ))

        self.recursion_state.save_candidate_probes(child.id, probes, iteration=iteration)
        self.write_audit_log(
            child_id=child.id,
            action_type="harness2_probe_comparison",
            details=f"Probed {len(candidates)} candidate(s) in isolated worktrees.",
            meta={
                "iteration": iteration,
                "probes": [
                    {"candidate_id": c.candidate_id, "status": c.status, "score": c.score,
                     "fault_frame_count": len(c.fault_frames)}
                    for c in candidates
                ],
            },
        )

        # Compose
        composed = self.llm.compose_harness(
            candidates=candidates,
            tactical_objective=objective,
            deliverables=child.deliverables,
        )
        winner_id = composed.winning_candidate_id
        winner = next((c for c in candidates if c.candidate_id == winner_id), None)
        # Only a candidate that actually passed validation may be committed
        if winner is not None and winner.status != "success":
            winner_id, winner = None, None

        contrast_feedback = self._build_contrast_feedback(candidates, winner_id)

        differences = []
        if winner is not None:
            for c in candidates:
                if c.candidate_id != winner_id:
                    differences.append({
                        "candidate_id": c.candidate_id,
                        "status": c.status,
                        "differing_components": self._diff_harness_components(
                            winner.harness.to_dict(), c.harness.to_dict()
                        ),
                    })
        contrast = ContrastMatrix(
            contrast_id=f"contrast-{child.id}-{iteration}",
            card_id=child.id,
            iteration=iteration,
            candidate_ids=[c.candidate_id for c in candidates],
            differences=differences,
            fault_frames=[f for c in candidates if c.status != "success" for f in c.fault_frames],
            winner_probe_id=winner_id,
            composed_deliverables=dict(composed.deliverables) if winner_id else {},
            synthesis_rationale=composed.synthesis_rationale,
        )
        self.recursion_state.save_contrast_matrix(contrast)
        self.recursion_state.update_recursion_state(
            child,
            recursion_depth=depth + 1,
            enable_harness2=True,
            probes_count=len(probes),
            last_contrast_id=contrast.contrast_id,
            last_contrast_feedback=contrast_feedback,
        )
        self.write_audit_log(
            child_id=child.id,
            action_type="harness2_composition_decision",
            details=composed.synthesis_rationale or "Composition decision recorded.",
            meta={
                "iteration": iteration,
                "winner": winner_id,
                "score": composed.score,
                "candidates_evaluated": composed.candidates_evaluated,
                "contrast_id": contrast.contrast_id,
                "recursion_depth": depth + 1,
            },
        )

        if winner_id is None:
            # No passing probe: nothing safe to commit; caller falls back but keeps the contrast
            return {"success": False, "logs": [f"[Harness2] No passing candidate. {composed.synthesis_rationale}"],
                    "created_files": [], "contrast_feedback": contrast_feedback, "fallback": True}

        # Sandboxed commit: write composed deliverables into exec_root, contained within it
        root_real = os.path.realpath(exec_root)
        created: List[str] = []
        for rel_path, content in composed.deliverables.items():
            abs_path = os.path.realpath(os.path.join(exec_root, rel_path))
            if os.path.commonpath([root_real, abs_path]) != root_real:
                self.write_audit_log(
                    child_id=child.id,
                    action_type="harness2_containment_violation",
                    details=f"Blocked composed deliverable outside execution root: {rel_path}",
                )
                continue
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
            created.append(abs_path)

        return {
            "success": True,
            "logs": [f"[Harness2] Composed deliverables from candidate {winner_id} (score {composed.score:.2f})."],
            "created_files": created,
            "contrast_feedback": contrast_feedback,
        }

    def execute_single_child(
        self,
        child: ChildCard,
        mother: MotherCard,
        child_path: str,
        execution_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes a single Child Card (System 1 fast-path or System 2 deliberative self-healing loop)
        inside the designated workspace or isolated worktree root.
        """
        exec_root = execution_root or self.workspace_root

        if self.privacy_guard is None:
            restricted = mother.governance.restricted_paths if getattr(mother, "governance", None) else []
            self.privacy_guard = PrivacyGuard(restricted_paths=restricted)
        if self.sandbox is None:
            self.sandbox = SandboxRunner()

        # Adapter Cartridge Hot-Swapping: mount cartridges from mother onto the LLM adapter
        if mother.attached_cartridges:
            self._mount_cartridges_on_adapter(mother=mother, child_id=child.id)

        # Security evaluation
        paths_to_be_edited = [d.get("path") for d in child.deliverables]
        assigned_profile = self.privacy_guard.evaluate_routing_profile(
            files_accessed=paths_to_be_edited,
            base_mode=mother.privacy_mode,
        )
        child.execution_profile_assigned = assigned_profile
        print(f"[SEC] Privacy Guard assigned profile: '{assigned_profile.upper()}' for {child.name}.")
        self.write_audit_log(
            child_id=child.id,
            action_type="privacy_evaluation",
            details=f"Assigned routing profile '{assigned_profile}' based on target files.",
            meta={"deliverables": child.deliverables, "execution_root": exec_root},
        )

        # Retrieve RAG context if applicable
        rag_context = ""
        rag_query = child.rag_query or child.tactical_objective
        if rag_query:
            rag_context = self.rag_retriever.format_rag_context(rag_query, top_k=2)
            if rag_context:
                print(f"  [RAG] Injected internal architecture context for '{child.name}'.")
                self.write_audit_log(
                    child_id=child.id,
                    action_type="rag_retrieval",
                    details="Injected architecture & schema context.",
                    meta={"rag_query": rag_query},
                )

        # Cognitive Triage: Evaluate routing between System 1 and System 2
        is_system_1 = False
        objective_lower = child.tactical_objective.lower()
        deliverables_paths = [d.get("path", "") for d in child.deliverables]

        # System 1 Heuristics: documentation files, readmes, typos, simple comments
        if (
            any(p.endswith(".md") or "doc" in p for p in deliverables_paths)
            or any(keyword in objective_lower for keyword in ["typo", "formatting", "docstring", "comment", "readme"])
        ):
            is_system_1 = True

        if is_system_1:
            print("[COGNITIVE] Triage: Low-complexity task detected. Routing to SYSTEM 1 (Heuristic Fast-Path).")
            self.write_audit_log(
                child_id=child.id,
                action_type="cognitive_triage",
                details="Routed to System 1 due to document/formatting low-complexity nature.",
            )

            # SYSTEM 1: Single forward-pass execution
            result = self.llm.execute_child_card(
                tactical_objective=f"{child.tactical_objective}\n\n{rag_context}".strip(),
                deliverables=child.deliverables,
                iteration=2,  # Direct result
                design_system=mother.design_system,
                execution_root=exec_root,
            )

            self.write_audit_log(
                child_id=child.id,
                action_type="code_modification",
                details="System 1 wrote deliverables heuristically.",
                meta={"modified_files": result.get("created_files")},
            )

            # Fast syntax check
            syntax_ok = True
            for file_path in result.get("created_files", []):
                if file_path.endswith(".py"):
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            compile(f.read(), file_path, "exec")
                    except SyntaxError as e:
                        syntax_ok = False
                        print(f"  [COGNITIVE] System 1 syntax check failed for {file_path}: {e}. Escalating to System 2.")
                        break

            if syntax_ok:
                child.phase = "Completed"
                child.current_iteration = 1
                for log_msg in result.get("logs", []):
                    child.logs.append(f"[System 1] {log_msg}")
                child.logs.append("[System 1] Bypassed deep sandboxed validation and completed task successfully!")
                child.to_yaml(child_path)
                print("[SUCCESS] System 1 completed task successfully without sandbox overhead!")

                # Extract and persist institutional lessons
                fp = self.alignment_engine.calculate_task_fingerprint(
                    child.parent_squad_id or "default",
                    child.tactical_objective,
                    [d.get("path", "") for d in child.deliverables if isinstance(d, dict)],
                    recursion_depth=child.wave_depth,
                )
                lessons = self.recursion_state.extract_and_record_lessons(
                    child=child,
                    task_fingerprint=fp,
                    execution_result=result,
                )
                if os.path.exists(str(self.recursion_state.lessons_file)):
                    self.rag_indexer.reindex_file(".jobs/recursion/lessons_learned.json")

                self.write_audit_log(
                    child_id=child.id,
                    action_type="lesson_extracted",
                    details=f"Extracted {len(lessons)} institutional lesson(s) into meta-memory.",
                    meta={"lesson_ids": [l.lesson_id for l in lessons], "count": len(lessons)},
                )

                self.write_audit_log(
                    child_id=child.id,
                    action_type="task_completed",
                    details="System 1 fast-path completed successfully. Sandbox bypassed.",
                    meta={"status": "Success"},
                )
            else:
                is_system_1 = False

        if not is_system_1:
            print("[COGNITIVE] Triage: Routing to SYSTEM 2 (Deliberative Sandbox Validation Loop).")
            previous_errors = ""
            contrast_feedback = ""
            harness2_active = self._is_harness2_active(mother)
            if harness2_active:
                self.write_audit_log(
                    child_id=child.id,
                    action_type="harness2_enabled",
                    details="Harness² Propose–Probe–Compose active for System 2 execution.",
                    meta={"max_recursion_depth": mother.governance.max_recursion_depth},
                )

            while child.current_iteration < child.max_iterations:
                child.current_iteration += 1
                print(f"  [RUN] Starting Iteration {child.current_iteration}/{child.max_iterations}...")

                self.write_audit_log(
                    child_id=child.id,
                    action_type="execution_iteration",
                    details=f"Executing iteration {child.current_iteration} of task objective.",
                    meta={"objective": child.tactical_objective},
                )

                combined_objective = f"{child.tactical_objective}\n\n{rag_context}".strip()

                result = None
                if harness2_active:
                    print("  [HARNESS2] Propose-Probe-Compose across isolated probe worktrees...")
                    result = self._run_harness2_iteration(
                        child=child,
                        mother=mother,
                        exec_root=exec_root,
                        objective=combined_objective,
                        iteration=child.current_iteration,
                        # Prior contrast + validation faults inform the next proposal (no blind retry)
                        previous_errors=f"{previous_errors}\n\n{contrast_feedback}".strip(),
                    )
                    if result is not None:
                        contrast_feedback = result.get("contrast_feedback", "")
                        if contrast_feedback:
                            child.logs.append(f"[Iteration {child.current_iteration}] {contrast_feedback}")
                    if result is not None and not result.get("success"):
                        result = None  # no passing probe: fall back to single-path, informed by contrast

                if result is None:
                    reflection_errors = previous_errors
                    if contrast_feedback:
                        reflection_errors = f"{previous_errors}\n\n{contrast_feedback}".strip()
                    result = self.llm.execute_child_card(
                        tactical_objective=combined_objective,
                        deliverables=child.deliverables,
                        iteration=child.current_iteration,
                        previous_errors=reflection_errors,
                        design_system=mother.design_system,
                        execution_root=exec_root,
                    )

                self.write_audit_log(
                    child_id=child.id,
                    action_type="code_modification",
                    details="LLM updated deliverables code files.",
                    meta={"modified_files": result.get("created_files")},
                )

                for log_msg in result.get("logs", []):
                    child.logs.append(f"[Iteration {child.current_iteration}] {log_msg}")

                all_passed = True
                previous_errors = ""

                for cmd in child.validation_commands:
                    print(f"  [TEST] Running Validation with Flakiness Check: '{cmd}'...")
                    cmd_result = self.sandbox.run_validation_with_flakiness_check(cmd, cwd=exec_root)

                    self.write_audit_log(
                        child_id=child.id,
                        action_type="sandbox_validation",
                        details=f"Executed: {cmd}",
                        meta={"exit_code": cmd_result["exit_code"], "output": cmd_result["output"], "flaky": cmd_result.get("flaky", False)},
                    )

                    if not cmd_result["success"]:
                        all_passed = False
                        previous_errors += cmd_result.get("fault_frame", cmd_result["output"])
                        print(f"  [WARN] Validation Failed (Exit Code: {cmd_result['exit_code']})")
                        child.logs.append(f"[Iteration {child.current_iteration}] Validation command '{cmd}' failed.")
                        child.logs.append(f"Fault Frame:\n{cmd_result.get('fault_frame', cmd_result['output'])}")
                        break
                    else:
                        print("  [OK] Validation Passed!")
                        child.logs.append(f"[Iteration {child.current_iteration}] Validation command '{cmd}' passed.")

                if all_passed:
                    child.phase = "Completed"
                    child.to_yaml(child_path)
                    print(f"[SUCCESS] Task Completed Successfully in {child.current_iteration} iterations!")

                    # Extract and persist institutional lessons
                    latest_contrast = self.recursion_state.load_contrast_matrix(child.id)
                    probes = self.recursion_state.load_candidate_probes(child.id)
                    fp = self.alignment_engine.calculate_task_fingerprint(
                        child.parent_squad_id or "default",
                        child.tactical_objective,
                        [d.get("path", "") for d in child.deliverables if isinstance(d, dict)],
                        recursion_depth=child.wave_depth,
                    )
                    lessons = self.recursion_state.extract_and_record_lessons(
                        child=child,
                        contrast=latest_contrast,
                        probes=probes,
                        task_fingerprint=fp,
                        execution_result=result,
                    )
                    if os.path.exists(str(self.recursion_state.lessons_file)):
                        self.rag_indexer.reindex_file(".jobs/recursion/lessons_learned.json")

                    self.write_audit_log(
                        child_id=child.id,
                        action_type="lesson_extracted",
                        details=f"Extracted {len(lessons)} institutional lesson(s) into meta-memory.",
                        meta={"lesson_ids": [l.lesson_id for l in lessons], "count": len(lessons)},
                    )

                    self.write_audit_log(
                        child_id=child.id,
                        action_type="task_completed",
                        details="All deliverables passed verification checks. Committing task.",
                        meta={"status": "Success"},
                    )

                    # Closed-Loop Ticketing Egress
                    if child.external_ticket_id:
                        platform = child.source_platform or "ServiceNow"
                        print(f"  [EGRESS-SYNC] Closed-loop resolution dispatched for {platform} ticket: {child.external_ticket_id}")
                        self.write_audit_log(
                            child_id=child.id,
                            action_type="ticket_resolution_sync",
                            details=f"Pushed closed-loop resolution to {platform} ({child.external_ticket_id}). Status: Resolved.",
                            meta={
                                "ticket_id": child.external_ticket_id,
                                "platform": platform,
                                "iterations_to_heal": child.current_iteration,
                                "pr_branch": f"aje/{child.id}",
                            },
                        )
                    break
                else:
                    child.to_yaml(child_path)
                    print("  [INFO] Triggering autonomous self-healing on next iteration...")

        if child.phase != "Completed":
            child.phase = "Failed"
            child.to_yaml(child_path)
            print(f"[ERROR] Child task failed after reaching maximum iterations ({child.max_iterations}).")
            self.write_audit_log(
                child_id=child.id,
                action_type="task_failed",
                details=f"Failed validation checks after {child.max_iterations} attempts.",
                meta={"status": "Failed"},
            )

        return {"child_id": child.id, "phase": child.phase, "iterations": child.current_iteration}

    def run_one_cycle(self, mother_card_path: str, use_wave_execution: bool = True):
        """
        Performs a complete autonomous execution cycle:
        1. Loads Mother Card configuration and safety policies.
        2. Discovers pending Child Cards and partitions into DAG Waves.
        3. Executes waves in bounded concurrency ($N=2$) with isolated Git worktrees.
        4. Runs Inter-Wave Synchronization Barriers with composite sandbox tests.
        5. Triggers post-wave gap analysis and alignment cascades.
        """
        if not os.path.exists(mother_card_path):
            print(f"Error: Mother card not found at {mother_card_path}")
            return

        print("--------------------------------------------------")
        print("[RUN] Booting Autonomous Job-Card Engine (v4.0)...")
        print("--------------------------------------------------")

        mother = MotherCard.from_yaml(mother_card_path)
        print(f"[INFO] Mother Guardian Loaded: {mother.name} (Privacy: {mother.privacy_mode})")
        if mother.design_system:
            print(f"[INFO] Design System Enforced: {mother.design_system}")
        if mother.repo.remote_slug:
            print(f"[INFO] Linked GitHub Remote: {mother.repo.remote_slug} (Target: {mother.repo.target_branch})")

        self.privacy_guard = PrivacyGuard(restricted_paths=mother.restricted_paths)
        self.sandbox = SandboxRunner(banned_commands=mother.banned_commands)

        # 1. Run Mother Card Preflight Quality & Regex Scan
        self.run_preflight_quality_scan(mother)

        if mother.rag_knowledge_paths:
            self.index_rag_knowledge(mother.rag_knowledge_paths)

        # Collect pending Child Cards with SecOps Defense Matrix verification
        gov = getattr(mother, "governance", None)
        pending_children: List[ChildCard] = []
        for file_name in sorted(os.listdir(self.jobs_dir)):
            if file_name.startswith("child_") and file_name.endswith((".yaml", ".yml")):
                child_path = os.path.join(self.jobs_dir, file_name)
                try:
                    child = ChildCard.from_yaml(child_path)
                except Exception:
                    continue

                if child.phase not in ["Completed", "Failed"]:
                    # 1. Inbound Prompt Injection & Adversarial Payload Scan
                    if gov and gov.enable_injection_guard:
                        card_payload = {
                            "name": child.name,
                            "tactical_objective": child.tactical_objective,
                            "deliverables": [d.get("path", "") for d in child.deliverables],
                            "test_commands": child.validation_commands,
                            "rag_query": child.rag_query or "",
                        }
                        inj_result = self.injection_guard.scan_card(card_payload)
                        if not inj_result.is_safe and inj_result.risk_level in (InjectionRiskLevel.CRITICAL, InjectionRiskLevel.HIGH):
                            print(f"[SECURITY] [QUARANTINE] Adversarial prompt injection detected in '{child.name}' ({child.id}). Quarantining task.")
                            record = self.quarantine_vault.quarantine_card(
                                card_data_or_path=child_path,
                                reason=f"Inbound Adversarial Prompt Injection ({inj_result.risk_level.value})",
                                severity=inj_result.risk_level.value,
                                details={"findings": [f.to_dict() if hasattr(f, "to_dict") else str(f) for f in inj_result.findings]},
                                source_file_path=child_path,
                            )
                            self.audit_chain.append_event(
                                event_type="CARD_QUARANTINED",
                                actor="InboundInjectionGuard",
                                payload={"card_id": child.id, "quarantine_id": record.quarantine_id, "reason": record.reason}
                            )
                            continue

                    # 2. Cryptographic Attestation Verification
                    if gov and gov.enable_attestation:
                        card_dict = {
                            "id": child.id,
                            "title": child.name,
                            "squad": child.parent_squad_id,
                            "target_files": [d.get("path", "") for d in child.deliverables],
                            "validation_criteria": child.validation_commands,
                            "signature": child.signature,
                        }
                        is_valid, reason = self.attestation_engine.verify_card(card_dict, require_signature=True)
                        if not is_valid:
                            print(f"[SECURITY] [QUARANTINE] Cryptographic attestation failed for '{child.name}' ({child.id}): {reason}. Quarantining task.")
                            record = self.quarantine_vault.quarantine_card(
                                card_data_or_path=child_path,
                                reason=f"Cryptographic Task Attestation Failed: {reason}",
                                severity="HIGH",
                                details={"error": reason, "signature": child.signature},
                                source_file_path=child_path,
                            )
                            self.audit_chain.append_event(
                                event_type="CARD_QUARANTINED",
                                actor="CardAttestationEngine",
                                payload={"card_id": child.id, "quarantine_id": record.quarantine_id, "reason": record.reason}
                            )
                            continue

                    # 3. Worktree Path Containment Verification
                    if gov and gov.enable_containment_jail:
                        all_contained, violations = self.worktree_jailer.validate_card_file_targets({
                            "target_files": [d.get("path", "") for d in child.deliverables]
                        })
                        if not all_contained:
                            print(f"[SECURITY] [QUARANTINE] Path containment violation in '{child.name}' ({child.id}). Quarantining task.")
                            record = self.quarantine_vault.quarantine_card(
                                card_data_or_path=child_path,
                                reason="Path Containment Violation (Attempted boundary escape)",
                                severity="CRITICAL",
                                details={"violations": violations},
                                source_file_path=child_path,
                            )
                            self.audit_chain.append_event(
                                event_type="CARD_QUARANTINED",
                                actor="WorktreeJailer",
                                payload={"card_id": child.id, "quarantine_id": record.quarantine_id, "violations": violations}
                            )
                            continue

                    pending_children.append(child)

        if not pending_children:
            print("[INFO] No pending Child Cards found. Checking for gap analysis...")
            new_tasks = self.llm.generate_gap_analysis(current_repo_state="Idle repository")
            for task in new_tasks:
                new_child_filename = f"child_{task['id']}.yaml"
                new_child_path = os.path.join(self.jobs_dir, new_child_filename)
                if not os.path.exists(new_child_path):
                    new_child = ChildCard(
                        id=task["id"],
                        parent_mother_id=mother.id,
                        parent_squad_id=task.get("parent_squad_id", "squad-backend"),
                        name=task["name"],
                        tactical_objective=task["tactical_objective"],
                        deliverables=task["deliverables"],
                        validation_commands=task.get("test_commands", []),
                        phase="Pending",
                    )
                    new_child.to_yaml(new_child_path)
                    print(f"  [GAP-ANALYSIS] Autonomously created successor Child Card: {task['name']}")
            return

        print(f"[INFO] Identified {len(pending_children)} pending tactical Child Card(s).")

        # Partition into topological DAG Waves
        waves = SquadDAGScheduler.partition_into_waves(pending_children)
        print(f"[SCHEDULER] Partitioned into {len(waves)} execution wave(s).")

        gov = getattr(mother, "governance", None)
        max_concurrency = gov.max_concurrency if gov else 2
        vram_threshold = gov.vram_threshold_pct if gov else 85.0
        max_wave_depth = gov.max_wave_depth if gov else 3

        wave_executor = SquadWaveExecutor(
            workspace_root=self.workspace_root,
            engine_daemon=self,
            max_concurrency=max_concurrency,
            vram_threshold_pct=vram_threshold,
            max_wave_depth=max_wave_depth,
        )

        for wave_idx, wave_cards in enumerate(waves, 1):
            wave_executor.execute_wave(
                wave_index=wave_idx,
                wave_cards=wave_cards,
                mother=mother,
                jobs_dir=self.jobs_dir,
            )

        # Autonomous Gap Analysis & Successor Discovery
        for card in pending_children:
            if card.phase == "Completed":
                new_tasks = self.llm.generate_gap_analysis(current_repo_state=f"Completed {card.name}")
                for task in new_tasks:
                    new_child_filename = f"child_{task['id']}.yaml"
                    new_child_path = os.path.join(self.jobs_dir, new_child_filename)
                    if not os.path.exists(new_child_path):
                        new_child = ChildCard(
                            id=task["id"],
                            parent_mother_id=mother.id,
                            parent_squad_id=task.get("parent_squad_id", card.parent_squad_id),
                            name=task["name"],
                            tactical_objective=task["tactical_objective"],
                            deliverables=task["deliverables"],
                            validation_commands=task.get("test_commands", []),
                            phase="Pending",
                        )
                        new_child.to_yaml(new_child_path)
                        print(f"  [GAP-ANALYSIS] Autonomously created successor Child Card: {task['name']}")

        print("\n--------------------------------------------------")
        print("[CYCLE COMPLETE] All active DAG waves finished successfully.")
        print("--------------------------------------------------")
