import os
import shutil
import concurrent.futures
from typing import List, Dict, Any, Optional, Set

from .models import ChildCard, MotherCard
from .git_worktree_manager import GitWorktreeManager
from .sandbox import SandboxRunner
from .privacy_guard import PrivacyGuard
from .alignment_engine import AlignmentEngine
from .rag.indexer import LocalRAGIndexer
from .rag.retriever import LocalRAGRetriever
from .ast_skeleton import ASTSkeletonizer


class VRAMHardwareSentinel:
    """
    Monitors host memory and VRAM to prevent thrashing and OOM faults.
    Dynamically throttles concurrent LLM tasks when memory pressure is high.
    """

    def __init__(self, memory_threshold_pct: float = 85.0):
        self.memory_threshold_pct = memory_threshold_pct

    def get_memory_utilization_pct(self) -> float:
        try:
            import psutil
            return psutil.virtual_memory().percent
        except ImportError:
            # Fallback estimation if psutil is not installed
            return 40.0

    def should_throttle_to_serial(self) -> bool:
        """Returns True if local memory/VRAM saturation exceeds safe operating ceiling."""
        util = self.get_memory_utilization_pct()
        return util >= self.memory_threshold_pct


class SquadDAGScheduler:
    """
    Topological wave scheduler that partitions tasks into executable DAG tiers.
    Tasks in the same wave are mutually independent and can execute concurrently.
    """

    @staticmethod
    def partition_into_waves(cards: List[ChildCard]) -> List[List[ChildCard]]:
        """
        Organizes a list of ChildCard objects into sequential execution waves based on `depends_on`.
        """
        if not cards:
            return []

        pending_cards = {c.id: c for c in cards}
        completed_ids: Set[str] = {c.id for c in cards if c.phase == "Completed"}
        waves: List[List[ChildCard]] = []

        unprocessed = [c for c in cards if c.phase not in ["Completed", "Failed"]]

        while unprocessed:
            current_wave: List[ChildCard] = []
            for card in unprocessed:
                deps = card.depends_on or []
                # A card is ready if all its dependencies are already completed or scheduled in earlier waves
                if all(dep in completed_ids for dep in deps):
                    current_wave.append(card)

            if not current_wave:
                # Cycle or unfulfilled dependency detected: break cycle by taking all remaining
                current_wave = unprocessed[:]

            waves.append(current_wave)
            for card in current_wave:
                completed_ids.add(card.id)
                if card in unprocessed:
                    unprocessed.remove(card)

        return waves


class SquadWaveExecutor:
    """
    Executes DAG waves in bounded concurrency ($N=2$) with isolated Git worktrees,
    hardware sentinel monitoring, and inter-wave synchronization barriers.
    """

    def __init__(
        self,
        workspace_root: str,
        engine_daemon: Any,
        max_concurrency: int = 2,
        vram_threshold_pct: float = 85.0,
        max_wave_depth: int = 3,
    ):
        self.workspace_root = os.path.abspath(workspace_root)
        self.daemon = engine_daemon
        self.max_concurrency = max_concurrency
        self.sentinel = VRAMHardwareSentinel(memory_threshold_pct=vram_threshold_pct)
        self.worktree_mgr = GitWorktreeManager(workspace_root=self.workspace_root)
        self.alignment_engine = AlignmentEngine(workspace_root=self.workspace_root, max_wave_depth=max_wave_depth)
        self.skeletonizer = ASTSkeletonizer(workspace_root=self.workspace_root)

    def execute_card_in_worktree(
        self, child: ChildCard, mother: MotherCard, child_yaml_path: str
    ) -> Dict[str, Any]:
        """
        Executes a single ChildCard inside an isolated Git worktree directory.
        """
        branch_name = f"aje/{child.id}"
        worktree_path = self.worktree_mgr.create_worktree(branch_name=branch_name)
        child.worktree_path = worktree_path
        child.git_branch = branch_name
        child.phase = "Running"
        child.to_yaml(child_yaml_path)

        print(f"  [WORKTREE] Task '{child.name}' [{child.id}] allocated isolated worktree at: {os.path.basename(worktree_path)}")

        try:
            # Execute child inside the worktree workspace
            exec_result = self.daemon.execute_single_child(
                child=child,
                mother=mother,
                child_path=child_yaml_path,
                execution_root=worktree_path,
            )
            return exec_result
        finally:
            # Sync any deliverables back to workspace and teardown worktree
            for d in child.deliverables:
                rel = d.get("path", "")
                src_file = os.path.join(worktree_path, rel)
                dst_file = os.path.join(self.workspace_root, rel)
                if os.path.exists(src_file):
                    os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                    shutil.copy2(src_file, dst_file)

            self.worktree_mgr.cleanup_worktree(worktree_path)

    def execute_wave(
        self, wave_index: int, wave_cards: List[ChildCard], mother: MotherCard, jobs_dir: str
    ) -> List[Dict[str, Any]]:
        """
        Executes all tasks in a wave concurrently (bounded by max_concurrency $N=2$).
        """
        effective_concurrency = 1 if self.sentinel.should_throttle_to_serial() else self.max_concurrency
        print(f"\n=======================================================")
        print(f"[WAVE {wave_index}] Executing {len(wave_cards)} task(s) (Concurrency Pool: {effective_concurrency} workers)")
        print(f"=======================================================")

        results: List[Dict[str, Any]] = []

        if effective_concurrency == 1 or len(wave_cards) == 1:
            for card in wave_cards:
                c_path = getattr(card, "_source_path", "") or os.path.join(jobs_dir, f"child_{card.id}.yaml")
                res = self.execute_card_in_worktree(child=card, mother=mother, child_yaml_path=c_path)
                results.append(res)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=effective_concurrency) as pool:
                future_to_card = {
                    pool.submit(
                        self.execute_card_in_worktree,
                        card,
                        mother,
                        getattr(card, "_source_path", "") or os.path.join(jobs_dir, f"child_{card.id}.yaml"),
                    ): card
                    for card in wave_cards
                }
                for future in concurrent.futures.as_completed(future_to_card):
                    c = future_to_card[future]
                    try:
                        res = future.result()
                        results.append(res)
                    except Exception as e:
                        print(f"  [ERROR] Worker failed for {c.id}: {e}")
                        results.append({"child_id": c.id, "success": False, "error": str(e)})

        # Inter-Wave Synchronization Barrier
        self.inter_wave_barrier(wave_index=wave_index, wave_cards=wave_cards, mother=mother)
        return results

    def inter_wave_barrier(
        self, wave_index: int, wave_cards: List[ChildCard], mother: MotherCard
    ) -> None:
        """
        Inter-Wave Synchronization Barrier:
        1. Runs composite integration testing gate in sandbox.
        2. Automatically reindexes local RAG and invalidates AST skeleton caches.
        3. Invokes continuous alignment cascades to generate downstream tasks.
        """
        print(f"\n[BARRIER] Wave {wave_index} complete. Running Inter-Wave Synchronization Barrier...")

        # 1. Composite Integration Gate
        composite_test_cmds = []
        if getattr(mother, "governance", None) and mother.governance.composite_integration_commands:
            composite_test_cmds.extend(mother.governance.composite_integration_commands)

        for c in wave_cards:
            if c.phase == "Completed":
                composite_test_cmds.extend(c.validation_commands)

        composite_test_cmds = list(dict.fromkeys(composite_test_cmds))

        if composite_test_cmds:
            print(f"  [BARRIER] Running Composite Sandbox Gate ({len(composite_test_cmds)} check(s))...")
            gate_res = self.daemon.sandbox.run_composite_integration_gate(
                commands=list(set(composite_test_cmds)),
                cwd=self.workspace_root,
            )
            if gate_res["passed"]:
                print("  [BARRIER] Composite Sandbox Gate Passed! Interface invariant preserved.")
            else:
                print(f"  [BARRIER-WARN] Composite Gate encountered issue: {gate_res['failed_command']}")

        # 2. Re-index RAG & Refresh AST
        if mother.rag_knowledge_paths:
            self.daemon.index_rag_knowledge(mother.rag_knowledge_paths)

        # 3. Alignment Cascades
        for c in wave_cards:
            if c.phase == "Completed":
                mod_files = [d.get("path") for d in c.deliverables if d.get("path")]
                align_tasks = self.alignment_engine.generate_alignment_cards(
                    completed_child_id=c.id,
                    completed_child_name=c.name,
                    parent_squad_id=c.parent_squad_id,
                    current_wave_depth=c.wave_depth,
                    modified_files=mod_files,
                )
                for t in align_tasks:
                    new_card_path = os.path.join(self.daemon.jobs_dir, f"child_{t['id']}.yaml")
                    if not os.path.exists(new_card_path):
                        align_card = ChildCard(
                            id=t["id"],
                            parent_mother_id=mother.id,
                            name=t["name"],
                            parent_squad_id=t["parent_squad_id"],
                            tactical_objective=t["tactical_objective"],
                            deliverables=t["deliverables"],
                            validation_commands=t.get("validation_commands", []),
                            depends_on=t.get("depends_on", []),
                            wave_depth=t.get("wave_depth", c.wave_depth + 1),
                            phase="Pending",
                        )
                        align_card.to_yaml(new_card_path)
                        print(f"  [ALIGNMENT-CASCADE] Dispatched downstream {t['parent_squad_id']} alignment card: {t['name']}")
