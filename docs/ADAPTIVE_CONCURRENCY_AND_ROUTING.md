# Adaptive Concurrency & Intelligent Model Routing

**Adaptive Orchestrator v5 — Phase 3 Specification & Developer Guide**

---

## 1. Overview

Phase 3 introduces the adaptive execution-control layer for Adaptive Orchestrator v5:

$$\text{Ready Work} + \text{Available Workers} + \text{Runtime Feedback} \longrightarrow \text{Adaptive Capacity} + \text{Model Selection} \longrightarrow \text{Dispatch}$$

Prior orchestration systems (such as v4) imposed rigid instructional caps (e.g., `MAX_CONCURRENT = 4`, `MAX_LAUNCHES = 10`) and stop-and-go wave barriers. Phase 3 eliminates arbitrary fixed limits while protecting platform resources through a deterministic, feedback-driven Additive Increase / Multiplicative Decrease (AIMD) concurrency controller and an intelligent model router (`FAST` vs `PRO`).

---

## 2. Decoupling Logical Breadth from Physical Concurrency

A core invariant of the v5 architecture is the strict decoupling of logical DAG breadth from physical execution slots:

```text
┌─────────────────────────────────────────────────────────────────────────────────┐
│ LOGICAL CONCURRENCY (Graph Breadth)                                             │
│ - Topological DAG width: 16 to 32+ parallel ready tasks in Mission DAG.         │
│ - Expresses the true mathematical parallelism of decoupled code components.     │
│ - Managed by: DependencyResolver & ReadyQueue.                                  │
└────────────────────────────────────────┬────────────────────────────────────────┘
                                         │ Dequeued in priority order
                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ PHYSICAL CAPACITY GATE (AIMD Controller)                                        │
│ - Physical active concurrency limit: C_cur in [C_min, C_max] (default 2 to 8).  │
│ - Additively scales up on healthy throughput; multiplicatively backs off on 429.│
│ - Managed by: AIMDController & FeedbackCollector.                               │
└────────────────────────────────────────┬────────────────────────────────────────┘
                                         │ Gated & Routed
                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ REUSABLE DOMAIN WORKER POOL                                                     │
│ - Bounded set of domain specialists (Backend, Frontend, DevOps, Testing).       │
│ - In-memory AST & conversation retention across tasks via native IDLE wake.     │
│ - Managed by: WorkerRegistry & DomainAffinityPolicy.                            │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Distinction Table

| Concept | Definition | Governed By | Typical Range |
|:---|:---|:---|:---:|
| **Logical DAG Width** | Total number of tasks whose dependencies are satisfied | `ReadyQueue` & `DependencyResolver` | $1 \text{ to } 32+$ |
| **Physical Capacity** | Maximum active concurrent tasks allowed by policy | `AIMDController` ($C_{\text{cur}}$) | $2 \text{ to } 8$ |
| **Worker Pool Size** | Total registered worker agents | `WorkerRegistry` | $2 \text{ to } 8$ |
| **Active Concurrency** | Number of workers currently executing tasks | `WorkerRegistry.busy_count` | $\le C_{\text{cur}}$ |

---

## 3. The AIMD Concurrency Controller

### Mathematical Model

The controller maintains a dynamic capacity $C_{\text{cur}} \in [C_{\text{min}}, C_{\text{max}}]$ governed by runtime feedback signals:

1. **Additive Increase (Scale Up)**:
   When execution is healthy (no rate limits or latency spikes) for $N$ consecutive tasks ($N = \text{healthy\_threshold}$):
   $$C_{\text{cur}} \leftarrow \min(C_{\text{cur}} + \Delta_{\text{inc}}, C_{\text{max}})$$
   *Default: $\Delta_{\text{inc}} = 1$, $N = 3$, $C_{\text{max}} = 8$.*

2. **Multiplicative Decrease (Backpressure Throttle)**:
   When an API rate limit (`HTTP 429 RESOURCE_EXHAUSTED`), severe latency spike, or repeated task failure streak occurs:
   $$C_{\text{cur}} \leftarrow \max(\lfloor C_{\text{cur}} \times \gamma_{\text{dec}} \rfloor, C_{\text{min}})$$
   *Default: $\gamma_{\text{dec}} = 0.5$, $C_{\text{min}} = 2$.*

3. **Cooldown Hold**:
   Following a multiplicative decrease, capacity scaling is frozen for a configured cooldown period ($\ge 1$ step) to prevent oscillatory thrashing.

### Configuration (`AIMDConfig`)

```python
@dataclass(frozen=True)
class AIMDConfig:
    min_capacity: int = 2          # Absolute floor; guarantees progress without deadlock
    max_capacity: int = 8          # Ceiling; protects host memory and rolling quota
    initial_capacity: int = 4      # Conservative startup baseline
    increase_step: int = 1         # Additive step size
    decrease_factor: float = 0.5   # Multiplicative backoff factor
    healthy_threshold: int = 3     # Consecutive successes required for increase
    cooldown_steps: int = 1        # Evaluation steps to hold capacity after decrease
```

---

## 4. Runtime Feedback Signals

Execution feedback is modeled as lightweight, typed dataclass instances recorded into a sliding-window `FeedbackCollector`:

### Signal Types (`FeedbackSignalType`)
- `TASK_STARTED`: Worker assigned and beginning execution.
- `TASK_COMPLETED`: Task passed; execution latency and worker ID recorded.
- `TASK_FAILED`: Task failed; error message and failure category recorded.
- `RATE_LIMIT_ERROR`: Explicit HTTP 429 or quota exhaustion; triggers instant multiplicative decrease.
- `LATENCY_SPIKE`: Severe execution latency spike indicating upstream host/network congestion.
- `WORKER_FAILED`: Process termination or unhandled tool crash.
- `QUEUE_STARVATION`: Idle capacity exists with empty ready queue.
- `BACKLOG_SATURATION`: Ready queue depth significantly exceeds active capacity.

---

## 5. Intelligent Model Routing

The `ModelRouter` selects an `ExecutionProfile` for each task prior to dispatch. It does **not** invoke LLMs; it produces a deterministic execution profile passed to the `ExecutionAdapter`.

### Model Tiers

1. **Fast Tier (`FAST`)**:
   - Optimized for routine code implementation, AST navigation, regex search, syntax linting, and standard unit tests.
   - Configured identifier default: `"model: flash"`.
2. **Frontier Tier (`PRO`)**:
   - Reserved for complex architectural reasoning, global planning, security audits, adversarial reviews, and escalated repair loops.
   - Configured identifier default: `"model: pro"`.

### Decision Hierarchy

```text
TASK ENTERS ROUTER
       │
       ▼
1. Explicit Override? (task.metadata['model_tier'] or result['force_tier']) ──► Honor Override
       │ No
       ▼
2. Prior Failures? (task.retry_count >= max_retries_before_escalation) ──────► Escalate to PRO
       │ No
       ▼
3. Strategic Domain? (domain in {'architecture', 'audit', 'security'}) ───────► Route to PRO
       │ No
       ▼
4. Strategic Task Type / Verification? (adversarial_review == True) ──────────► Route to PRO
       │ No
       ▼
5. High Priority? (task.priority >= high_priority_threshold) ─────────────────► Route to PRO
       │ No
       ▼
6. Default Baseline ──────────────────────────────────────────────────────────► Route to FAST
```

---

## 6. Execution Flow

The complete Phase 3 execution path:

```text
[Task Becomes READY]
       │
       ▼
[ReadyQueue.push(task)]
       │
       ▼ (Event: TASK_READY / WORKER_IDLE / CAPACITY_CHANGED)
[EventDrivenScheduler.evaluate()]
       │
       ├─► 1. Check AIMD Capacity Gate: active_tasks < C_cur?
       │      └─► If NO: Break & hold tasks safely in ReadyQueue (Backpressure).
       │
       ├─► 2. Query Idle Workers in WorkerRegistry.
       │      └─► If NONE: Wait for next WORKER_IDLE event.
       │
       ├─► 3. Match Task with Worker via DomainAffinityPolicy.
       │
       ├─► 4. Generate ExecutionProfile via ModelRouter.route(task, worker).
       │
       ├─► 5. Dequeue Task & Assign Worker (Worker State -> BUSY).
       │
       ├─► 6. Emit Events: TASK_ASSIGNED, WORKER_BUSY (with model tier metadata).
       │
       ├─► 7. Dispatch via ExecutionAdapter.dispatch(worker, task, profile).
       │
       └─► 8. On Completion / Failure:
              ├─► Record Feedback in FeedbackCollector.
              ├─► Process Signal in AIMDController (Update C_cur).
              ├─► Emit CAPACITY_CHANGED if capacity mutated.
              ├─► Release Worker to IDLE (Preserving in-context memory).
              ├─► Resolve Newly Satisfied Dependents in DependencyResolver.
              └─► Immediately Trigger Next evaluate() Step (No wave barriers).
```

---

## 7. Synthetic Benchmark Results

The synthetic benchmark (`tests/test_v5_benchmark.py`) compares Fixed Capacity ($C = 2$) against Adaptive AIMD ($C \in [2, 6]$) over an identical 16-task multi-domain DAG:

| Metric | Fixed Capacity ($C=2$) | Adaptive AIMD ($C \in [2, 6]$) | Relative Impact |
|:---|:---:|:---:|:---|
| **Total Simulated Steps** | 20 steps | 18 steps | **10.0% faster completion** |
| **Peak Active Concurrency** | 2 workers | 3 workers | **50.0% higher peak utilization** |
| **Average Active Concurrency** | 1.70 | 1.89 | **11.2% higher throughput density** |
| **Queue Wait Steps** | 39 steps | 31 steps | **20.5% reduction in wait time** |
| **Worker Reuse Count** | 13 | 13 | **100% warm-worker retention** |
| **Capacity Adjustments** | 0 inc / 0 dec | 7 inc / 1 dec | **Responsive to simulated 429** |
| **FAST Model Executions** | 12 (70.6%) | 12 (70.6%) | Cost-effective routine work |
| **PRO Model Executions** | 5 (29.4%) | 5 (29.4%) | High-priority & retry escalation |
| **Tasks Completed** | 16 / 16 | 16 / 16 | 100% deterministic success |

*(Note: Measured in a deterministic discrete-step test environment; synthetic only).*

---

## 8. Preserved Invariants & Safeguards

1. **No Fixed Concurrency Ceiling**: Concurrency adapts dynamically based on live health signals rather than static constants.
2. **Warm Worker Reuse Intact**: Workers transition `IDLE -> BUSY -> IDLE` without process teardown or clean-slate context duplication.
3. **Deterministic Safety**: No busy polling loops; all transitions and adjustments are driven strictly by discrete events.
4. **Scope Containment**: Git worktree branching, automated merge scripts, verification pyramids, and telemetry persistence are strictly deferred to subsequent phases.
