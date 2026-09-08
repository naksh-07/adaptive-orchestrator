# Changelog

All notable changes to **Adaptive Orchestrator** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [5.0.0] - 2026-09-08

### Added
- **Dynamic Task DAG & Continuous Dispatch**: Replaced rigid 5-wave synchronization barriers with continuous event-driven ready queue.
- **AIMD Adaptive Concurrency Controller**: Replaced artificial 4-worker/10-launch limits with dynamic capacity scaling (C ∈ [2, 8]) driven by real-time execution feedback.
- **Decoupled Logical DAG Width**: Scalable logical task parallelism independently decoupled from physical worker capacity.
- **Reusable Domain Worker Pool**: Warm context retention across tasks within domain affinity, eliminating spawn/kill churn.
- **Intelligent Model Router**: Deterministic routing to FAST (Gemini Flash) or PRO (Gemini Pro) tiers based on complexity, risk, and failure history.
- **Workspace Isolation & Write Ownership Registry**: Strict path-normalized write set locking with collision detection and Git worktree branching.
- **Serialized Integration Queue**: Deterministic sequential merge processing preventing workspace corruption.
- **4-Tier Verification Pyramid**: Pipelined verification architecture:
  - Tier 1: Local worker self-validation.
  - Tier 2: Independent verification (syntax, AST, test execution).
  - Tier 3: Adversarial challenger (undeclared write detection and edge-case stress testing).
  - Tier 4: Whole-mission Victory Audit confirming global acceptance criteria.
- **In-Context Local Repair Loop**: Compact, focused `RepairPayload` directing targeted fixes within the same worker workspace.
- **Atomic Checkpoint Persistence & Crash Recovery**: Crash-resilient recovery converting interrupted active tasks back to ready while purging stale locks.
- **Authoritative Telemetry Engine**: Event-driven metrics tracking concurrency, queue wait times, worker reuses, and model tier distributions.

### Changed
- Removed artificial v4 caps (`MAX_CONCURRENT=4`, `MAX_TOTAL_LAUNCHES=10`, 5-wave execution barriers).
- Upgraded all manifests, skills, schemas, and templates to v5.0.0.

## [4.0.0] - 2026-08-24

### Added
- **Dual Mandatory Delegation Gates**: Explicit Phase 1 (Pre-Planning) and Phase 2 (Execution Dispatch) gates preventing premature uncoordinated edits or solo wanderings.
- **Hard Resource Limits**: Global mission cap of max 4 concurrent subagents and max 10 total launches across the entire hierarchical tree.
- **Tree-Aware Mission Ledger**: Real-time tracking of active workers, launch quotas, remaining budget, and hierarchical coordinator allocations.
- **5-Wave Progression Model**: Structured transition pipeline (Reconnaissance -> Synthesis & Plan -> Controlled Implementation -> Independent Verification -> Workforce Collapse).
- **Specialist Subagent Suite**:
  - `explorer-researcher`: Read-only, fact-driven codebase reconnaissance specialist.
  - `implementer`: Controlled, scoped single-writer implementer with clean diff generation.
  - `reviewer-verifier`: Independent verification, linting, and automated test execution specialist.
  - `challenger-auditor`: Adversarial fuzzing and Victory-style final completion auditor.
- **Standardized Handoff Protocol**: Structured, evidence-based handoff blocks requiring objective, observations, logic chain, evidence, caveats, conclusion, and verification method.
- **Dead-End Memory Log**: Append-only falsification log (`dead-ends.md`) preventing recursive failure loops.
- **Progressive Coordination Depth (L0 - L3)**: Adaptive state footprint matching task complexity without workspace clutter.
- **CLI Tools & Diagnostics**:
  - `scripts/doctor.py`: Health-check and self-diagnostic engine.
  - `scripts/budget_ledger.py`: Concurrency and 10-launch budget calculator.
  - `scripts/install.py`: 1-command installer into Antigravity/Gemini configuration.
  - `scripts/validate_skill.py`: Manifest and schema integrity validator.
- **Full Test Suite & GitHub Actions CI**: Automated manifest, schema, and budget ledger regression testing.
