# Adaptive Orchestrator Architecture (v5)

Adaptive Orchestrator v5 is an Antigravity-native high-throughput orchestration engine designed for complex, multi-file, multi-domain engineering tasks. It replaces stop-and-go execution waves and artificial launch caps with a continuous, event-driven pipeline:

$$\text{Dynamic Task DAG} \longrightarrow \text{AIMD Adaptive Concurrency} \longrightarrow \text{Reusable Domain Workers} \longrightarrow \text{Controlled Writes} \longrightarrow \text{4-Tier Verification Pyramid} \longrightarrow \text{Victory Audit}$$

```text
┌───────────────────────────────────────────────────────────────────────────┐
│                           MISSION INITIALIZATION                          │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                         DYNAMIC DEPENDENCY DAG                            │
│            • Continuous Ready Queue (unblocked tasks ready immediately)   │
│            • Decoupled Logical DAG Width (scales independently)           │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│               ADAPTIVE SCHEDULER & AIMD CONCURRENCY                       │
│            • Dynamic physical concurrency C ∈ [C_min, C_max] (e.g. 2–8)   │
│            • Additive Increase on healthy steps; Multiplicative Decrease  │
│            • Intelligent Model Router (FAST Gemini Flash / PRO Gemini Pro)│
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                     REUSABLE DOMAIN WORKER POOL                           │
│            • Warm context preservation across domain-aligned tasks        │
│            • Zero unnecessary spawn/kill churn                            │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                  CONTROLLED WRITES & WORKSPACE ISOLATION                  │
│            • Exclusive write-ownership registry with path normalization   │
│            • Git worktree branch isolation; Serialized integration queue  │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                    4-TIER INCREMENTAL VERIFICATION                        │
│            • Tier 1: Local self-validation in worker workspace            │
│            • Tier 2: Independent verification & AST/lint checks           │
│            • Tier 3: Adversarial challenge & undeclared write detection   │
│            • In-Context Local Repair Loop (stays in same workspace)       │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                     SERIALIZED MERGE & INTEGRATION                        │
│            • Merges queued and processed sequentially                     │
│            • Idempotent state transitions; Atomic durable checkpoints     │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                     TIER 4: WHOLE-MISSION VICTORY AUDIT                   │
│            • Validates complete mission acceptance criteria               │
│            • Confirms required artifacts and zero unresolved failures     │
│            • Transitions mission to COMPLETED                             │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Dynamic Task DAG & Continuous Dispatch

Unlike rigid execution waves that enforce artificial global barriers, v5 models work as a directed acyclic graph (DAG):
- Tasks become **READY** as soon as all upstream dependencies are satisfied.
- The **Ready Queue** prioritizes unblocked tasks dynamically using unlock value and priority weighting.
- As soon as a worker finishes a task, it immediately picks up newly unblocked work from the queue without waiting for unrelated tasks.

---

## 2. Decoupled Concurrency: Logical Width vs. Physical Capacity

The v5 architecture strictly distinguishes between:
1. **Logical DAG Width**: The number of concurrently ready tasks defined by the mission graph. Scalable without artificial mission limits.
2. **Physical Worker Capacity ($C$)**: The maximum number of concurrently active workers governed dynamically by the **AIMD (Additive Increase / Multiplicative Decrease)** policy.
3. **Total Worker Creation Count**: Minimal footprint achieved by reusing warm domain workers across tasks.

```text
Logical DAG Width (e.g. 16 tasks ready)
       │
       ▼
[Ready Queue: Backpressure & Priority Ordering]
       │
       ▼
AIMD Capacity Window C ∈ [2, 8] (governs physical dispatch)
       │
       ▼
Reusable Domain Workers (reused across tasks)
```

---

## 3. Intelligent Model Routing

Each task is routed to an optimal model tier based on risk, complexity, and retry history:
- **FAST Tier (e.g., Gemini Flash)**: High-volume implementation, routine test execution, and boilerplate tasks.
- **PRO Tier (e.g., Gemini Pro)**: Architecture design, complex schema definitions, security-critical modules, and escalated repair loops.

---

## 4. Controlled Writes & Workspace Isolation

Concurrency safety is maintained by strict write-set contracts:
- **Write Ownership Registry**: Before any task executes, its declared `write_set` is locked exclusively in the `WorkspaceRegistry`.
- **Collision Detection**: Conflicting tasks attempting concurrent writes to overlapping paths are blocked until the lock is released.
- **Git Worktree Isolation**: Branch-based tasks execute in dedicated worktrees (`ao/<task_id>`).
- **Serialized Integration**: The `IntegrationManager` serializes all merges through a deterministic merge queue, preventing race conditions.

---

## 5. 4-Tier Verification Pyramid & Local Repair Loop

Verification is continuous, incremental, and evidence-grounded:
1. **Tier 1: Self-Test**: The implementing worker runs local checks before reporting completion.
2. **Tier 2: Independent Verification**: Independent reviewer verifies AST, linting, and unit test suites.
3. **Tier 3: Adversarial Challenge**: Actively stress-tests edge cases and detects undeclared file modifications.
4. **Local Repair Loop**: When verification identifies repairable defects, a compact `RepairPayload` is sent back to the same worker in the same workspace.
5. **Tier 4: Victory Audit**: Evaluates whole-mission acceptance criteria, required artifact presence, and regression status before final acceptance.

---

## 6. Durable State & Crash Recovery

- **Atomic Checkpoints**: Engine state is serialized to `.tmp` files and atomically renamed to prevent corruption.
- **Crash Recovery**: On restart, interrupted `RUNNING` or `VERIFYING` tasks revert safely to `READY` with incremented retry counts; stale worker assignments and locks are purged cleanly.
- **Idempotency**: Task completion, merges, verification, and recovery are safe to execute repeatedly without duplicating side-effects.
