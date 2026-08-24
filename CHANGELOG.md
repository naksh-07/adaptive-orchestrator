# Changelog

All notable changes to **Adaptive Orchestrator** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
