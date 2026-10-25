# Open Adapter Cartridge Registry & Hub Integration Plan

## Top-Level Overview
Create an open-source, community-driven **Adapter Cartridge Registry** for the Autonomous Job-Card Engine (AJE). This framework enables developers worldwide to publish, discover, download, and hot-swap specialized 15MB–100MB LoRA weights (via Hugging Face Hub) and zero-weight YAML Harness Patches across AJE's Mother Cards and tactical workers.

The system introduces:
1. **Unified Cartridge Manifest Schema (`cartridge.yaml`):** Standard definition for LoRA adapters, harness patches, and accuracy benchmarks.
2. **Adapter Registry & LoRA Manager (`src/adapters/registry.py` & `src/adapters/lora_manager.py`):** Search, caching, Hugging Face Hub weight retrieval, and in-memory cartridge mounting.
3. **CLI Developer Commands (`aje adapter`):** Commands for `search`, `pull`, `list`, `attach`, `publish`, and `test`.
4. **Technical & Pre-Populated Industry Cartridge Catalog:** Out-of-the-box cartridges for core developer tooling (FastAPI, Pytest, CUDA, Security) plus zero-weight pre-populated compliance packs for major industries (FinTech, Healthcare, Cybersecurity, Automotive, LegalTech, E-Commerce, DevOps/Cloud).

---

## Sub-Tasks

### Sub-Task 1: Cartridge Manifest Schema & Adapter Models
- **Intent:** Define the standardized data models and YAML serialization for Adapter Cartridges (`cartridge.yaml`) and integrate adapter bindings into Mother Cards.
- **Expected Outcomes:**
  - `AdapterCartridge`, `CartridgeMetadata`, `CartridgeSpec`, and `AdapterBinding` dataclasses defined in `src/models.py`.
  - Full YAML serialization and schema validation support for local and remote cartridges.
  - Backward compatibility with existing Mother Card YAML formats.
- **Todo List:**
  1. `[x]` Extend `src/models.py` with `AdapterCartridge` and `AdapterBinding` dataclasses.
  2. `[x]` Implement `from_yaml()` and `to_yaml()` methods with schema validation for cartridge manifests.
  3. `[x]` Add unit tests in `tests/test_cartridge_schema.py` verifying schema parsing and roundtrips.
- **Relevant Context:**
  - `src/models.py` (`MotherCard`, `GovernancePolicies`, `RepositoryBinding`)
  - `src/adapters/harness_mixin.py` (`HarnessConfig`)
- **Status:** `[x] done` (27 new tests; full suite 141 tests OK. `MotherCard.attached_cartridges` added as optional, omitted from YAML when empty.)

---

### Sub-Task 2: Adapter Registry & LoRA Manager Implementation
- **Intent:** Implement the core registry engine for searching local/remote cartridges, downloading LoRA weights from Hugging Face Hub (with caching in `.jobs/adapters/`), and loading YAML harness patches.
- **Expected Outcomes:**
  - `AdapterRegistry` created in `src/adapters/registry.py` providing `search()`, `pull()`, `list_installed()`, and `get_cartridge()`.
  - `LoRAManager` in `src/adapters/lora_manager.py` handling Hugging Face Hub downloading, checksum validation, and local caching.
  - Patch loader in `src/adapters/patch_loader.py` applying cartridge prompt/guardrail patches into AJE's 8-component harness.
- **Todo List:**
  1. `[x]` Create `src/adapters/lora_manager.py` for weight downloading and caching.
  2. `[x]` Create `src/adapters/patch_loader.py` for YAML harness patch merging.
  3. `[x]` Create `src/adapters/registry.py` managing local and community registry indexes.
  4. `[x]` Add unit tests in `tests/test_adapter_registry.py` covering search, pull, and patch application.
- **Relevant Context:**
  - `src/adapters/base.py` (`BaseLLMAdapter`)
  - `src/adapters/harness_mixin.py` (`HarnessMixin`)
  - `.jobs/adapters/` storage path
- **Status:** `[x] done` (23 new unit tests; full suite 164 tests passing with zero regressions. Includes `LoRAManager` SHA-256 verification and cache, `PatchLoader` 8-component merging with binding priority and deduplication, and `AdapterRegistry` catalog indexing/search/pull/uninstall.)

---

### Sub-Task 3: Dynamic Adapter Hot-Swapping in Daemon
- **Intent:** Connect the Adapter Registry into `src/daemon.py` and adapter classes (`OllamaAdapter`, `WatsonxAdapter`, `SimAdapter`) to dynamically mount LoRA weights or harness patches per task.
- **Expected Outcomes:**
  - When a Mother Card specifies `attached_cartridges`, the daemon injects the active cartridge's prompt rules, guardrails, and tool guidance into the execution context.
  - Multi-adapter fallback: if LoRA weights are unavailable, the engine gracefully activates the YAML harness patch.
  - Adapter attachment events are recorded in `audit_ledger.jsonl`.
- **Todo List:**
  1. `[x]` Update `src/daemon.py` to inspect `mother.attached_cartridges` and mount active cartridges.
  2. `[x]` Update `OllamaAdapter`, `WatsonxAdapter`, and `SimAdapter` to support cartridge weight/patch injection.
  3. `[x]` Log adapter mounting events in `audit_ledger.jsonl`.
  4. `[x]` Add unit tests in `tests/test_daemon_cartridge_hotswap.py`.
- **Relevant Context:**
  - `src/daemon.py` (`execute_single_child`, `_mount_cartridges_on_adapter`)
  - `src/adapters/base.py` (`BaseLLMAdapter.mount_cartridges`, `get_mounted_harness_context`)
  - `src/adapters/sim_adapter.py`, `src/adapters/ollama_adapter.py`, `src/adapters/watsonx_adapter.py`
- **Status:** `[x] done` (36 new unit tests; full suite 200 tests passing with zero regressions. `BaseLLMAdapter` extended with no-op `mount_cartridges()` / `get_mounted_harness_context()` defaults. All three adapters override to merge cartridge harness patches into `_mounted_harness` and prepend the rendered block to every `execute_child_card()` prompt. Daemon `_mount_cartridges_on_adapter()` resolves bindings via `AdapterRegistry`, checks `LoRAManager` cache with graceful fallback to harness-patch-only, and records `adapter_cartridge_mounted` / `adapter_cartridge_skipped` events into `audit_ledger.jsonl`.)

---

### Sub-Task 4: CLI Interface (`aje adapter search / pull / list / info / attach / init / test`)
- **Intent:** Provide developers with an intuitive command-line interface to discover, download, inspect, and attach adapter cartridges to their active workspace.
- **Expected Outcomes:**
  - `src/cli/main.py` extended with `aje adapter` subcommands: `search`, `pull`, `list`, `info`, `attach`, `init`, and `test`.
  - Clear terminal formatting with rich status tables and pass-rate badges.
- **Todo List:**
  1. `[x]` Implement `cmd_adapter()` handler in `src/cli/main.py`.
  2. `[x]` Connect CLI commands to `AdapterRegistry`, `LoRAManager` and Mother Card updater.
  3. `[x]` Add unit tests in `tests/test_cli_adapter.py` verifying CLI commands.
- **Relevant Context:**
  - `src/cli/main.py` (`cmd_adapter`, `_adapter_search/pull/list/info/attach/init/test`, `_adapter_checks`)
  - `tests/test_cli_adapter.py`
- **Status:** `[x] done` (82 new unit tests; full suite 282 tests passing with zero regressions.)
  - **Subcommands:** `search [query] --industry --type --tag --archetype`; `pull <name|path> --version --weights --force` (manifest into `.jobs/installed_cartridges/`, LoRA weights into `.jobs/adapters/`); `list` (installed cartridges with pass-rate, weights-cache and attached state); `info <name|path>` (metadata, LoRA details, benchmarks, harness-patch components); `attach <name> --version --mode --priority --disabled` (auto-installs from the catalog, upserts the `attached_cartridges` binding in `.jobs/mother_card.yaml`, warns on incompatible archetype); `init <name> --type --industry --author --description --output --force` (scaffolds `cartridge.yaml`, `README.md`, `test_cartridge.py`; refuses non-empty directories without `--force`); `test [name|path]` (schema validation plus guardrail, patch-merge, secret-scan, weights/checksum and benchmark-range checks reported as PASS/WARN/FAIL).
  - **Formatting:** aligned plain-text tables and colored `[NN% pass]` badges (green >= 90, yellow >= 75, red below); color auto-enabled on a TTY, `NO_COLOR` disables and `FORCE_COLOR` forces it. Errors go to stderr as `Error: ...`.
  - **Exit codes:** `0` success (including empty search/list), `1` command failure (not found, validation error, missing Mother Card, failed `test` checks, download/checksum error), `2` usage error (argparse). `main(argv=None)` now returns the exit code and the module entry point uses `sys.exit(main())`; existing commands are unchanged and still return 0.
  - **Notes:** `publish` (mentioned in the overview) is not implemented. The workspace catalog dirs searched are `registry/cartridges`, `cartridges` and `.jobs/cartridges`, so scaffolds from `init` are immediately discoverable. `pull` of a path with `--force` is needed to refresh an already-installed cartridge from a modified manifest.

---

### Sub-Task 5: Community Starter & Pre-Populated Industry Cartridge Catalog & Docs
- **Intent:** Seed the repository with technical starter cartridges and pre-populated industry compliance cartridges in `registry/cartridges/`, providing instant zero-weight domain guardrails, and document the open community contribution workflow.
- **Expected Outcomes:**
  - **Technical Cartridges (`registry/cartridges/tech/`):**
    - `registry/cartridges/tech/security-shield/cartridge.yaml` (Argon2id, OWASP, strict JWT).
    - `registry/cartridges/tech/pytest-self-healing/cartridge.yaml` (Fault-frame analysis, test synthesis).
    - `registry/cartridges/tech/cuda-kernel-opt/cartridge.yaml` (CUDA C++20, CUTLASS, shared memory tuning).
    - `registry/cartridges/tech/fastapi-backend/cartridge.yaml` (Typed OpenAPI, Pydantic v2, Redis rate limiting).
  - **Pre-Populated Industry Cartridges (`registry/cartridges/industry/`):**
    - `registry/cartridges/industry/fintech-banking/cartridge.yaml` (PCI-DSS v4, Decimal arithmetic enforcement, ISO 20022 schemas, serialization guards).
    - `registry/cartridges/industry/healthcare-hipaa/cartridge.yaml` (HIPAA Safe Harbor PHI de-identification, HL7 FHIR R4 schema enforcement, audit decorators).
    - `registry/cartridges/industry/cybersecurity-defense/cartridge.yaml` (OWASP Top 10 AST blockers, NIST 800-53 controls, zero-trust secrets).
    - `registry/cartridges/industry/automotive-embedded/cartridge.yaml` (MISRA C/C++ AST rules, dynamic memory allocation disallowance, watchdog invariants).
    - `registry/cartridges/industry/legal-compliance/cartridge.yaml` (GDPR right-to-erasure workflows, Merkle contract trails, immutable schemas).
    - `registry/cartridges/industry/ecommerce-retail/cartridge.yaml` (Idempotent payment keys, atomic inventory state machines, HMAC signature verification).
    - `registry/cartridges/industry/devops-cloud-infra/cartridge.yaml` (Terraform least-privilege RBAC, non-root K8s container enforcement, OTel context propagation).
  - **Documentation & Guides:**
    - `README.md` and `VALUE_PROPOSITION.md` updated with the Open Adapter Hub guide, industry matrix, and contribution instructions.
- **Todo List:**
  1. `[x]` Create `registry/cartridges/tech/` directory with the 4 technical starter cartridges.
  2. `[x]` Create `registry/cartridges/industry/` directory with the 7 pre-populated industry cartridges.
  3. `[x]` Add unit tests in `tests/test_cartridge_catalog.py` verifying all 11 manifests load cleanly, pass schema validation, and have valid benchmarks and patches.
  4. `[x]` Update `README.md` and `VALUE_PROPOSITION.md` with the `aje adapter` CLI guide, industry starter catalog, and contribution steps.
  5. `[x]` Run full test suite (`python -m unittest discover -s tests`) to verify zero regressions.
- **Relevant Context:**
  - `docs/mother-cards/`
  - `README.md`
  - `VALUE_PROPOSITION.md`
- **Status:** `[x] done` (29 new unit tests; full suite **311 tests passing** with zero regressions.)
  - **Tech Cartridges (4):** `security-shield` (Argon2id, OWASP, RS256 JWT, 96% pass_rate), `pytest-self-healing` (fault-frame triage, dual-pass flakiness, 94%), `cuda-kernel-opt` (CUTLASS 3.x, Nsight, Ampere/Hopper, 91%), `fastapi-backend` (Pydantic v2, typed OpenAPI 3.1, Redis rate limiting, 93%).
  - **Industry Cartridges (7):** `fintech-banking` (PCI-DSS v4, Decimal, ISO 20022, 95%), `healthcare-hipaa` (HIPAA Safe Harbor PHI, FHIR R4, AES-256, 97%), `cybersecurity-defense` (OWASP Top-10, NIST 800-53, zero-trust, 95%), `automotive-embedded` (MISRA C/C++ 2023, ASIL-B/D, no dynamic alloc, watchdog, 92%), `legal-compliance` (GDPR Art. 17 erasure, Merkle contract trails, 94%), `ecommerce-retail` (idempotent payments, atomic inventory, HMAC webhooks, 93%), `devops-cloud-infra` (Terraform least-priv RBAC, non-root K8s, OTel W3C propagation, 93%).
  - **Tests (`tests/test_cartridge_catalog.py`):** 29 tests across `TestTechCartridges`, `TestIndustryCartridges`, and `TestCatalogCoverage` classes. Aggregate tests verify all 11 manifests are on disk, load cleanly via `from_yaml()`, pass `validate()`, contain non-empty `harness_patch`, include a `pass_rate` benchmark in `[0, 1]`, and survive a `to_dict()` → `from_dict()` round-trip. Per-cartridge tests assert domain-specific invariants (e.g., Argon2id in security-shield guardrails, malloc block in automotive-embedded, FHIR in healthcare-hipaa).
  - **Documentation:** `README.md` updated with the Open Adapter Hub section (Cartridge Architecture Mermaid diagram, tech/industry tables with pass_rate, full `aje adapter` CLI workflow, contribution steps). `VALUE_PROPOSITION.md` updated with Industry Compliance Matrix and Technical Starter Cartridges tables, plus ROI impact note.
