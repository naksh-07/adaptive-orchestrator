# Adaptive Orchestrator v5 — Mission Status & Resource Ledger

## 1. Mission Overview
- **Mission ID**: [e.g. mission-12345]
- **Goal**: [Summary of the user's primary objective]
- **State**: [DRAFTING | PLANNING | EXECUTING | AUDITING | COMPLETED | FAILED]
- **Orchestration Mode**: [SOLO | FOCUSED | SMALL | PARALLEL | STAGED | HIERARCHICAL | MAX]

## 2. Resource & Concurrency Ledger (v5 AIMD Model)
```text
SCHEDULER_CAPACITY:    [Current AIMD Capacity: N (Min: 1, Max: M)]
ACTIVE_PHYSICAL_WORKERS: [Currently executing workers]
IDLE_POOLED_WORKERS:   [Warm context workers available for reuse]
WORKER_REUSES:         [Count of tasks assigned to existing workers]
LOGICAL_TASKS_COUNT:   [Total logical DAG tasks (decoupled from worker count)]
CHECKPOINT_TRIGGER:    [Last persistence checkpoint event]
```

## 3. Dynamic Task DAG Status
| Task ID | Title / Domain | Dependencies | Status | Worker ID | Workspace Mode | Retries |
| :--- | :--- | :--- | :--- | :--- | :--- | :---: |
| task-01 | Architecture Recon (general) | None | MERGED | worker-01 | in_place | 0/2 |
| task-02 | Backend Service (backend) | task-01 | RUNNING | worker-02 | branch | 0/2 |
| task-03 | Frontend Client (frontend) | task-01 | READY | [Queued] | branch | 0/2 |

## 4. Reusable Domain Worker Registry
| Worker ID | Primary Domain | Supported Domains | Model Tier | Status | Reuses |
| :--- | :--- | :--- | :--- | :--- | :---: |
| worker-01 | general | general, architecture | FAST | IDLE | 2 |
| worker-02 | backend | backend, test | PRO | BUSY | 0 |

## 5. Verification & Acceptance Criteria
- [ ] Tier 1 (Self-Test): passing unit checks per task.
- [ ] Tier 2 (Independent Verification): independent diff and build validation.
- [ ] Tier 3 (Adversarial Challenge): write-set exclusivity and stress validation.
- [ ] Tier 4 (Victory Audit): whole-mission acceptance and artifact verification.

## 6. Execution Flow & Continuous Dispatch
- [ ] Dispatch READY tasks respecting AIMD capacity.
- [ ] Re-assign IDLE workers with domain affinity.
- [ ] Process verified worktree changes through sequential merge queue.
- [ ] Trigger Tier 4 Victory Audit upon DAG completion.
