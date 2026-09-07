# Adaptive Orchestrator v5 — Workspace Ownership, Controlled Writes & Integration Reliability

**Authoritative Architecture Reference**: `ADAPTIVE_ORCHESTRATOR_V5_ARCHITECTURE.md` (Phase 4)

---

## Core Principle: READ PARALLEL — WRITE CONTROLLED

Adaptive Orchestrator v5 decouples **worker execution concurrency** from **filesystem write and integration safety**:
- Tasks execute in parallel across domain-specialized warm workers subject to adaptive AIMD concurrency limits.
- Tasks that modify code run in isolated Git worktrees (`workspace_mode="branch"`).
- Any concurrent task whose declared write set overlaps with another actively executing task is held in the `ReadyQueue` until the conflicting workspace is released.
- Code integration into the target branch is strictly serialized through a deterministic `MergeQueue`.

```
                  ┌─────────────────────────────────────────────────────────┐
                  │                   Task in ReadyQueue                    │
                  └────────────────────────────┬────────────────────────────┘
                                               │
                                               ▼
                                  ┌─────────────────────────┐
                                  │   Capacity & Worker?    │
                                  └────────────┬────────────┘
                                               │
                                               ▼
                                  ┌─────────────────────────┐
                                  │ Write-Collision Free?   │
                                  └────────────┬────────────┘
                                               │ (Acquire Exclusive Lock)
                                               ▼
                                  ┌─────────────────────────┐
                                  │ Provision Git Worktree  │
                                  └────────────┬────────────┘
                                               │
                                               ▼
                                  ┌─────────────────────────┐
                                  │    Worker Execution     │ (Parallel Execution)
                                  └────────────┬────────────┘
                                               │
                                               ▼
                                  ┌─────────────────────────┐
                                  │ Task Status: PASSED     │
                                  │ Worker Released to IDLE │ (Worker Reusable)
                                  └────────────┬────────────┘
                                               │
                                               ▼
                                  ┌─────────────────────────┐
                                  │ Controlled Merge Queue  │ (Strictly Serialized)
                                  └────────────┬────────────┘
                                               │
                        ┌──────────────────────┴──────────────────────┐
                        ▼                                             ▼
             [ Clean Automated Merge ]                       [ Merge Conflict ]
                        │                                             │
             - Clean Worktree Directory                      - Retain Worktree Evidence
             - Release Workspace Ownership                   - Release Logical Lock
             - Task Status: MERGED                           - Task Status: FAILED
             - Unlock Downstream Dependents                  - Route for Repair (Phase 5)
```

---

## 1. Task Read/Write Contracts

Tasks declare explicit access sets upon definition:
- `read_set`: Resources/paths inspected during task execution. Multiple concurrent readers are safely permitted.
- `write_set`: Resources/paths modified during task execution. Writers demand exclusive ownership over overlapping paths.
- `workspace_mode`: `"branch"` (default for code writers) or `"inherit"`/`"shared"` (for purely read-only verification/analysis tasks).

### Deterministic Path Normalization
All paths are normalized deterministically via `normalize_path()`:
- Backslashes converted to forward slashes (`/`).
- Trailing slashes stripped.
- Redundant relative segments (`./`, `../`) resolved canonically.
- Lowercase casing applied for case-insensitive filesystem safety.

---

## 2. Collision Detection Policy

The `CollisionDetector` evaluates whether two access sets can safely run concurrently:

```python
OverlapRule:
1. Exact Match:          "src/api/routes.py" vs "src/api/routes.py"     -> CONFLICT
2. Parent / Child:       "src/api"           vs "src/api/routes.py"     -> CONFLICT
3. Directory Prefix:     "src/models/"       vs "src/models/user.py"    -> CONFLICT
4. Disjoint Resources:   "src/auth.py"       vs "src/billing.py"        -> SAFE (Parallel)
```

### Scheduling Reaction to Write Conflicts
- When task $T_{candidate}$ conflicts with an actively held workspace record, the scheduler emits `EventType.WORKSPACE_CONFLICT`.
- The task **remains in `ReadyQueue`** without penalty.
- The task is **NOT marked FAILED**.
- The AIMD capacity is **NOT decreased** (conflicts are scheduling locks, not runtime congestion).
- As soon as the active conflicting task merges or releases its workspace, reactive event `WORKSPACE_RELEASED` triggers immediate re-evaluation and dispatch.

---

## 3. Workspace Registry

Located in `orchestrator/workspace/registry.py`, `WorkspaceRegistry` tracks in-memory allocations:
- `acquire(task, worker, workspace_mode, workspace_path, branch_name)`: Atomically validates collision freedom and records exclusive ownership in `_active_by_task`.
- `release(task_id, state, reason)`: Releases logical ownership, appends to audit history, and enables waiting tasks to proceed.
- `release_worker_lock(worker_id)`: Decouples warm worker reuse from task merge lifecycle. When task execution finishes, worker lock is cleared so the worker can execute other tasks while the completed task awaits merge.
- `release_for_worker(worker_id)`: Unconditionally clears any ownership held by a failed worker.

---

## 4. Worktree Adapter Boundary

Located in `orchestrator/workspace/adapter.py`, `WorktreeAdapter` encapsulates all Git CLI / Antigravity filesystem operations:
- `create_workspace(task, worker, branch_name)`: Provisions an isolated worktree directory (e.g., `.worktrees/<task_id>`) attached to branch `ao/<task_id>`.
- `inspect_status(workspace_path)`: Returns clean/dirty state and modified file list.
- `inspect_diff(workspace_path, base_branch)`: Returns unified diff.
- `finalize_workspace(workspace_path, commit_message)`: Commits changes into the task branch.
- `cleanup_workspace(workspace_path, branch_name)`: Prunes and unlinks the worktree from the host filesystem.

Implementations:
- `NativeWorktreeAdapter`: Wraps native Git subprocess operations (`git worktree add`, `git worktree remove`, etc.).
- `MockWorktreeAdapter`: Fast, deterministic in-memory adapter for hermetic unit testing and simulations.

---

## 5. Controlled Sequential Merge Queue

Located in `orchestrator/integration/queue.py`, `MergeQueue` enforces strictly serialized code integration:

1. **Deterministic Ordering**: Tasks are enqueued on completion (`TaskState.PASSED`). The queue sorts requests by FIFO arrival time and task priority.
2. **Serial Execution**: Only one merge attempt executes at any moment (`active_merge`).
3. **Merge Adapter**: Encapsulates git merge mechanics (`attempt_merge`, `inspect_diff`, `cleanup_branch`).
4. **Lifecycle Outcomess**:
   - **Clean Merge**: Task transitions to `TaskState.MERGED`, worktree is cleaned from disk, workspace ownership is released (`WORKSPACE_RELEASED`), and downstream dependents in the DAG are unblocked.
   - **Merge Conflict**: Task transitions to `TaskState.FAILED`, `MERGE_FAILED` is emitted, logical workspace ownership is released so pending non-conflicting tasks are unblocked, but **physical worktree files are preserved on disk** as forensic evidence for Phase 5 verification and repair.

---

## 6. Worker Failure Recovery

When a worker crashes unexpectedly during task execution (e.g. process death, timeout):
1. Scheduler receives failure via `handle_worker_failure(worker_id, error)`.
2. Worker is marked `WorkerState.FAILED`.
3. Worker's active workspace lock is forcibly invalidated (`release_for_worker`).
4. Task is transitioned to `TaskState.RETRYING` (incrementing `retry_count`).
5. Task is placed back in `ReadyQueue` as `TaskState.READY`.
6. Scheduler reactively wakes up and dispatches the task to a healthy idle worker.
7. No stale locks survive.

---

## 7. Extended Lifecycle States

### Task States
```
PENDING ──► READY ──► ASSIGNED ──► RUNNING ──► PASSED ──► MERGED
              │                      │
              ▼                      ▼
           BLOCKED           RETRYING / FAILED
```

### Structured Events
- `WORKSPACE_ACQUIRED`: Exclusive workspace leased to task/worker.
- `WORKSPACE_RELEASED`: Exclusive workspace freed following merge or invalidation.
- `WORKSPACE_CONFLICT`: Task deferred due to overlapping write set.
- `MERGE_READY`: Task enqueued in sequential merge queue.
- `MERGE_STARTED`: Merge queue began integrating task branch.
- `MERGE_COMPLETED`: Branch cleanly merged into integration target.
- `MERGE_FAILED`: Merge conflict detected; evidence retained.
- `WORKER_FAILED`: Worker unexpectedly failed during task execution.
