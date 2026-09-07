# Adaptive Orchestrator v5 — Core Engine Foundation (Developer Guide)

**Module Status**: Phase 1 Core Engine Foundation  
**Specification Authority**: `ADAPTIVE_ORCHESTRATOR_V5_ARCHITECTURE.md` (Sections 5–11)  
**Package Path**: `orchestrator/`  
**Target Python**: $\ge 3.9$ (Pure standard library, zero external runtime dependencies)  

---

## 1. Overview

The **Core Engine Foundation** provides the deterministic, mathematically sound core for Adaptive Orchestrator v5. It strictly answers the question:

> **"WHAT CAN RUN?"**

It deliberately does **not** determine:
- *WHO runs it?* (Deferred to Phase 2: Worker Pool)
- *HOW MANY run?* (Deferred to Phase 3: Adaptive AIMD Scheduler)
- *WHICH MODEL tier is assigned?* (Deferred to Phase 3: Model Router)
- *WHICH WORKTREE or workspace is provisioned?* (Deferred to Phase 4: Workspace Manager)
- *HOW is verification orchestrated?* (Deferred to Phase 5: Verification Pyramid)

By isolating logical readiness from physical execution, the foundation ensures reproducible graph behavior and deterministic scheduling.

```text
Mission
  ↓
Task Model
  ↓
Dependency Graph (DAG)
  ↓
Dependency Resolver (Readiness)
  ↓
State Machine & Mutations
  ↓
Priority Ready Queue
  ↓
Internal Event Emitter
```

---

## 2. Core Entities

### 2.1 Mission (`orchestrator.models.Mission`)
Encapsulates top-level execution state for a user request:
- `mission_id`: Unique string identifier.
- `title` & `description`: Mission objective.
- `state`: Lifecycle state (`DRAFTING`, `PLAN_APPROVED`, `EXECUTING`, `PAUSED`, `COMPLETED`, `FAILED`, `CANCELLED`).
- `metadata`: Arbitrary JSON-serializable dictionary.

### 2.2 Task (`orchestrator.models.Task`)
The canonical unit of work in the Mission DAG:
- `task_id`: Unique task identifier within the mission.
- `mission_id`: Associated mission identifier.
- `title` & `description`: Task specification.
- `domain`: Domain tag (e.g., `"backend"`, `"frontend"`, `"database"`).
- `priority`: Numeric priority score (higher values dequeue first).
- `status`: Discrete task state from `TaskState`.
- `dependencies`: Set of prerequisite task IDs that must complete before this task can run.
- `dependents`: Set of downstream task IDs that wait on this task.
- `result` & `error`: Structured output payloads.
- `retry_count` & `max_retries`: Execution retry accounting.
- `created_at`, `started_at`, `completed_at`: Timestamps.

---

## 3. Task State Machine (`TaskState`)

Tasks transition through nine formal states with strict transition validation:

| State | Classification | Description |
|:---|:---:|:---|
| `PENDING` | Foundation | Task is registered, but one or more prerequisites are not yet satisfied. |
| `BLOCKED` | Foundation | An upstream dependency failed or was cancelled; task cannot execute. |
| `READY` | Foundation | All prerequisites have reached `PASSED`; task is eligible to run. |
| `RUNNING` | Active | Task has started active execution. |
| `VERIFYING`| Active | Task implementation is complete; undergoing local validation. |
| `PASSED` | Success / Terminal | Verification succeeded; task deliverables satisfy downstream prerequisites. |
| `FAILED` | Terminal | Task failed and retries are exhausted. |
| `RETRYING` | Active | Task failed locally and is undergoing an in-context retry/repair attempt. |
| `CANCELLED`| Terminal | Task was cancelled by mission abort or parent intervention. |

### Valid State Transitions
Attempting an invalid state transition (e.g., `PENDING -> RUNNING` or `CANCELLED -> READY`) raises `orchestrator.exceptions.InvalidStateTransitionError`.

```text
PENDING   ──> READY, BLOCKED, CANCELLED
BLOCKED   ──> PENDING, READY, CANCELLED
READY     ──> RUNNING, BLOCKED, CANCELLED
RUNNING   ──> VERIFYING, PASSED, FAILED, RETRYING, CANCELLED
VERIFYING ──> PASSED, FAILED, RETRYING, CANCELLED
RETRYING  ──> RUNNING, FAILED, CANCELLED
PASSED    ──> PENDING (on graph invalidation), CANCELLED
FAILED    ──> RETRYING (on retry), PENDING (on invalidation), CANCELLED
CANCELLED ──> (terminal)
```

---

## 4. Dependency DAG (`orchestrator.graph.DependencyGraph`)

The `DependencyGraph` maintains the structural integrity of the task graph:
- **Strict Acyclicity**: Every `add_dependency()` call checks for cycle formation via DFS 3-coloring. If a cycle would be introduced, the addition is rolled back and `CycleDetectedError` is raised.
- **Self-Dependency Rejection**: Raises `SelfDependencyError` if `dependent_id == prerequisite_id`.
- **Topological Sorting**: Implements Kahn's algorithm with deterministic tie-breaking.
- **Transitive Queries**: Provides `get_ancestors(task_id)` and `get_descendants(task_id)` for transitive closure analysis.

---

## 5. Dependency Resolver & Readiness Semantics (`orchestrator.resolver.DependencyResolver`)

A task is logically **READY** if and only if:
1. It is currently in `PENDING` or `BLOCKED` state.
2. Every task in `task.dependencies` has reached `status == TaskState.PASSED`.
3. It is not currently `RUNNING`, `VERIFYING`, `PASSED`, `FAILED`, or `CANCELLED`.

### Unlock Value Calculation
The resolver computes `calculate_unlock_value(task_id)`: the count of direct downstream dependents that would have *all* their remaining prerequisites satisfied if `task_id` were to pass. This heuristic boosts critical-path tasks in the Ready Queue.

---

## 6. Deterministic Ready Queue (`orchestrator.scheduler.ReadyQueue`)

The `ReadyQueue` holds **only** tasks in `TaskState.READY`. Non-ready tasks are rejected with `TaskNotReadyError`.

### Multi-Factor Priority Ordering
Tasks are ordered deterministically by:
1. `priority` (descending: higher priority runs first)
2. `unlock_value` (descending: tasks unlocking more dependents run first)
3. `entry_seq` (ascending: older tasks run first, FIFO)
4. `task_id` (ascending: alphabetical string tie-breaker)

Duplicate entries are rejected with `DuplicateQueueEntryError`.

---

## 7. Dynamic Graph Mutations (`orchestrator.graph.GraphMutationEngine`)

The mission graph can safely evolve at runtime:
- `add_task(task)`: Injects a new task dynamically.
- `add_dependency(dependent, prereq)`: Injects dependency with instant cycle detection.
- `remove_dependency(dependent, prereq)`: Removes edge safely.
- `invalidate_task(task_id)`: Resets `task_id` and all its transitive descendants back to `PENDING`, clearing their results and removing them from the ready queue in deterministic topological order.

---

## 8. Event System (`orchestrator.models.Event` & `EventType`)

The engine emits compact, structured events with monotonic sequence numbers:
- `MISSION_CREATED`, `MISSION_STATE_CHANGED`
- `TASK_CREATED`, `TASK_READY`, `TASK_STARTED`, `TASK_VERIFYING`, `TASK_COMPLETED`, `TASK_FAILED`, `TASK_RETRYING`, `TASK_CANCELLED`, `TASK_STATE_CHANGED`
- `DEPENDENCY_SATISFIED`, `DEPENDENCY_INVALIDATED`
- `GRAPH_MUTATED`

Subscribers can listen in real time via `engine.subscribe(callback)`.

---

## 9. Top-Level Facade API (`orchestrator.MissionEngine`)

```python
from orchestrator import MissionEngine

# 1. Initialize mission
engine = MissionEngine(mission_id="msn_001", title="Add Retry Mechanism")

# 2. Add tasks
t_db = engine.add_task("db_mig", title="DB Migration", domain="db", priority=10.0)
t_api = engine.add_task("api_retry", title="API Retry", domain="backend", priority=5.0, dependencies=["db_mig"])

# 3. Query ready tasks
# t_db has zero dependencies -> already in ReadyQueue!
ready = engine.get_ready_tasks()
next_task = engine.pop_next_ready_task() # returns t_db

# 4. Progress task execution
engine.mark_task_started("db_mig")
# ... work happens ...
engine.mark_task_verifying("db_mig")
completed, newly_ready = engine.mark_task_completed("db_mig", result={"applied": True})

# 5. Dependents unlock automatically!
# newly_ready == [t_api]
# t_api is now in engine.ready_queue!
next_task_2 = engine.pop_next_ready_task() # returns t_api
```

---

## 10. Phase Boundary & Next Steps

This module represents the complete **Phase 1: Core Engine Foundation**.

Subsequent phases will consume these stable APIs:
- **Phase 2**: Stateful Worker Pool (`orchestrator/workers/pool.py`, `affinity.py`).
- **Phase 3**: Adaptive Concurrency & Backpressure Controller (`orchestrator/scheduler/aimd.py`).
- **Phase 4**: Workspace Ownership & Automated Integration (`orchestrator/workspace/`).
- **Phase 5**: 4-Tier Verification Pyramid & In-Context Local Repair (`orchestrator/verification/`).
