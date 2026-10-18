# Harness² Recursive Agent Harnessing Integration Plan

## Top-Level Overview
Integrate Google Research's **Harness²** (*Recursive Agent Harnessing for an Open World*, 2026) framework into the Autonomous Job-Card Engine (AJE). This upgrade enables two levels of recursion while keeping underlying model weights fixed:
1. **Task-Level Recursion (Propose–Probe–Compose):** Dynamically proposes candidate execution harnesses, probes their effects across isolated Git worktrees against test suites, and composes an optimal execution plan via behavioral contrast without requiring ground truth.
2. **Harnessing-Level Recursion (Meta-Learning Memory):** Extracts evidence-supported lessons from completed tasks and persists them into `.jobs/recursion/` and `.jobs/rag/` to continuously guide future child cards and Mother Card governance.

All existing core strengths—3-tier hierarchy, declarative YAML, worktree containment jails, cognitive triage (System 1 vs. System 2), and Merkle forensic audit ledgers—remain strictly intact and backward compatible.

---

## Sub-Tasks

### Sub-Task 1: Persistent Recursion State Infrastructure & Storage
- **Intent:** Establish the persistent storage engine and runtime data structures needed to record task-level probes, behavioral contrasts, and cross-task institutional memory in `.jobs/recursion/`.
- **Expected Outcomes:**
  - Runtime state manager created in `src/recursion/state_manager.py` to persist probe candidates, contrast logs, and cross-iteration lessons.
  - `GovernancePolicies` in `src/models.py` extended with optional flags (`enable_harness2: bool`, `max_recursion_depth: int = 3`).
  - Runtime non-serialized `_recursion_state` attached to `ChildCard` without altering or breaking existing YAML serialization formats.
- **Todo List:**
  1. `[x]` Create `src/recursion/__init__.py` and `src/recursion/state_manager.py`.
  2. `[x]` Implement `RecursionStateManager` with methods to save/load candidate probes, contrast matrices, and accumulated institutional lessons to `.jobs/recursion/`.
  3. `[x]` Extend `GovernancePolicies` and `ChildCard` dataclasses in `src/models.py` with runtime recursion state attributes.
  4. `[x]` Add unit tests in `tests/test_recursion_state.py` validating state persistence and memory roundtrips.
- **Relevant Context:**
  - `src/models.py` (`GovernancePolicies`, `ChildCard`, `MotherCard`)
  - `test_workspace/.jobs/` storage layout
- **Status:** `[x] completed`

---

### Sub-Task 2: Modular 8-Component Harness Adapter Mixin
- **Intent:** Define the modular 8-component editable harness interface (System Prompt, Guardrails, Memory, Tool Guidance, Subagents, Skills, Scripts, Plugins) and implement Propose–Probe–Compose methods on LLM adapters.
- **Expected Outcomes:**
  - `HarnessMixin` defined in `src/adapters/harness_mixin.py` providing `propose_harness_candidates()`, `probe_execution()`, and `compose_harness()`.
  - Concrete adapters (`WatsonxAdapter`, `OllamaAdapter`, `SimAdapter`) implement the mixin with graceful fallback if disabled.
- **Todo List:**
  1. `[x]` Create `src/adapters/harness_mixin.py` with the 8-component harness data model and abstract Propose–Probe–Compose methods.
  2. `[x]` Implement Propose–Probe–Compose capabilities in `src/adapters/sim_adapter.py` for deterministic offline testing.
  3. `[x]` Update `src/adapters/ollama_adapter.py` and `src/adapters/watsonx_adapter.py` with dynamic 8-component prompt generation and candidate composition.
  4. `[x]` Add unit tests in `tests/test_harness_mixin.py` asserting candidate generation and composition behavior.
- **Relevant Context:**
  - `src/adapters/base.py` (`BaseLLMAdapter`)
  - `src/adapters/sim_adapter.py`, `src/adapters/ollama_adapter.py`, `src/adapters/watsonx_adapter.py`
- **Status:** `[x] completed`

---

### Sub-Task 3: Task-Level Recursion in Daemon Execution Loop
- **Intent:** Upgrade the execution loop in `src/daemon.py` to support candidate probing in isolated worktrees, behavioral contrast evaluation, and composition before sandboxed commit.
- **Expected Outcomes:**
  - When `enable_harness2` is active and task complexity warrants System 2 recursion, the daemon executes Propose–Probe–Compose across parallel or sequential probe branches.
  - Failing candidates are contrasted to isolate fault frames and synthesize passing deliverables.
  - System 1 (fast-path) remains completely untouched for zero-overhead doc/readme generation.
- **Todo List:**
  1. `[x]` Update `execute_single_child()` in `src/daemon.py` to check `governance.enable_harness2`.
  2. `[x]` Implement worktree probe branching and result collation during iteration cycles.
  3. `[x]` Feed behavioral contrasts into the reflection loop to eliminate blind retries.
  4. `[x]` Log all probe comparisons and composition decisions to `audit_ledger.jsonl`.
  5. `[x]` Add unit tests in `tests/test_daemon_harness2.py`.
- **Relevant Context:**
  - `src/daemon.py` (`execute_single_child`, `run_one_cycle`)
  - `src/git_worktree_manager.py` (`create_worktree`, `cleanup_worktree`)
  - `src/squad_executor.py` (`execute_card_in_worktree`)
- **Status:** `[x] completed`

---

### Sub-Task 4: Harnessing-Level Meta-Memory & Alignment Cascades
- **Intent:** Extract evidence-supported lessons from completed tasks, persist them into institutional fleet memory, and index them into local RAG for subsequent child cards.
- **Expected Outcomes:**
  - Upon task completion, key lessons (e.g. library quirks, compiler flags, error resolutions) are automatically synthesized and stored in `.jobs/recursion/lessons_learned.json`.
  - Local RAG indexer updates its index incrementally with newly learned lessons.
  - `AlignmentEngine` incorporates contrast metadata into task fingerprints to prevent duplicate alignment cascades.
- **Todo List:**
  1. `[x]` Implement lesson extraction and deduplication in `src/recursion/state_manager.py`.
  2. `[x]` Connect post-task lesson persistence to `squad_executor.py` and `daemon.py`.
  3. `[x]` Update `src/rag/indexer.py` to index `.jobs/recursion/lessons_learned.json`.
  4. `[x]` Ensure `AlignmentEngine` uses recursion depth in task deduplication fingerprints.
- **Relevant Context:**
  - `src/alignment_engine.py` (`generate_alignment_cards`, `calculate_task_fingerprint`)
  - `src/rag/indexer.py` (`LocalRAGIndexer`, `reindex_file`)
- **Status:** `[x] completed`

---

### Sub-Task 5: End-to-End Verification & Benchmark Suite
- **Intent:** Validate the entire integrated system with comprehensive unit and end-to-end integration tests, ensuring all 20 Mother Cards and test suites pass without regression.
- **Expected Outcomes:**
  - Full test suite (`python -m unittest discover -s tests`) passes cleanly.
  - End-to-end simulation (`python test_aje_e2e.py`) demonstrates successful Propose–Probe–Compose execution and cross-task memory accumulation.
  - Documentation and `README.md` updated with the Harness² recursive architecture.
- **Todo List:**
  1. `[x]` Create `tests/test_harness2_e2e.py` testing multi-depth Propose–Probe–Compose and memory transfer.
  2. `[x]` Run full test suite across all 20 Mother Cards.
  3. `[x]` Update `README.md` architecture section with the Dual-Level Recursion flow.
- **Relevant Context:**
  - `test_aje_e2e.py`
  - `tests/test_squad_executor.py`
  - `README.md`
- **Status:** `[x] completed`
