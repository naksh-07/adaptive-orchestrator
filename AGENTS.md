# Adaptive Orchestration Rules (v5 Architecture)

Whenever complex multi-file, multi-domain, or high-risk tasks are requested:
1. Activate the `adaptive-orchestrator` skill.
2. Evaluate the Dual Mandatory Delegation Gates (Phase 1: Pre-Planning & Phase 2: Post-Approval Execution) and output dispatch checklists.
3. Adaptive Capacity & Resource Policy: Dynamic physical concurrency governed by AIMD scheduler policy with decoupled logical DAG width; prefer worker reuse over repeated launches.
4. Drive continuous pipeline flow via Dynamic DAG, AIMD adaptive concurrency, reusable domain workers, and FAST/PRO model routing.
5. Enforce the Fundamental Law: READ PARALLEL — WRITE CONTROLLED (Exclusive branch/worktree isolation and sequential merge queue).
6. Progress through the 4-Tier Verification Pyramid (Tier 1 Self-Test → Tier 2 Independent Verification → Tier 3 Adversarial Challenge → Tier 4 Victory Audit) with in-context local repair loops.
7. Maintain durable state with atomic checkpoints and crash recovery.
8. Complete lifecycle through Tier 4 Victory Audit and graceful workforce finalization upon mission completion.
