# Adaptive Orchestrator v5 — Delegation & Verification Gate Matrix

## Gate 1: Phase 1 Pre-Planning Dispatch Gate
- [ ] Task complexity evaluated against reconnaissance thresholds (3+ domains or 2+ independent lanes).
- [ ] Pre-planning checklist emitted before file modifications or tool calls.
- [ ] Read-only explorers/researchers dispatched with strict non-writing scope if triggered.
- [ ] Recon findings reconciled into structured implementation plan.

## Gate 2: Phase 2 Post-Approval Execution Dispatch Gate
- [ ] Implementation plan approved by user (or auto-proceed triggered).
- [ ] Dynamic task DAG constructed with explicit dependencies, priorities, and write sets.
- [ ] Reusable domain workers assigned or pooled workers reused from IDLE.
- [ ] Workspace isolation configured (`in_place` for non-colliding, `branch` worktree for parallel writes).

## Gate 3: Incremental Task Verification Gate
- [ ] Tier 1 Self-Test executed by assigned worker (unit test / local check).
- [ ] Tier 2 Independent Verification passed (clean diffs, linters, external test execution).
- [ ] Tier 3 Adversarial Challenge passed (write-set boundary check, stress tests if required by policy).
- [ ] Local in-context repair triggered if repairable; exhausted retries cleanly failed.

## Gate 4: Worktree Integration & Merge Gate
- [ ] Verified branch worktree changes submitted to sequential merge queue (`MERGE_READY`).
- [ ] Merge applied cleanly onto integration target without workspace corruption.
- [ ] Worktree resources and branch workspace released cleanly.
- [ ] Dependent downstream DAG tasks unlocked in ReadyQueue.

## Gate 5: Tier 4 Mission Victory Audit Gate
- [ ] All tasks completed in valid terminal states (PASSED / MERGED).
- [ ] Zero unresolved failures or unhandled blockers.
- [ ] Verification evidence and history complete across deliverables.
- [ ] All required deliverable artifacts confirmed present on filesystem.
- [ ] Prompt acceptance criteria completely verified by victory auditor.
- [ ] Workforce returned to IDLE / finalized cleanly.
