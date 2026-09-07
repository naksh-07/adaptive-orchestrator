# Adaptive Orchestrator v5 — Incremental Verification, In-Context Repair & Evidence-Gated Execution

## 1. Fundamental Principle: Execution Completion ≠ Verified Completion

In naive autonomous agent systems, a task is assumed complete the moment an LLM finishes its generation or tool call. Adaptive Orchestrator v5 rejects this assumption:

$$\text{Task Executed} \neq \text{Task Completed}$$

$$\text{Task Completed} = \text{Implementation Executed} + \text{Evidence Collected} + \text{Independently Verified} + \text{Repaired if Defective}$$

Under Phase 5, execution output is treated merely as a **candidate deliverable**. A task cannot transition to `PASSED` and cannot enter the sequential merge queue (`MERGE_READY`) until its candidate diff satisfies all required verification tiers and produces structured, deterministic evidence.

---

## 2. The Verification Pyramid

Adaptive Orchestrator v5 organizes verification into an incremental, multi-tiered pyramid:

```text
               ┌───────────────────────────────┐
               │           TIER 4              │
               │     Final Victory Audit       │  Pre-Delivery: Independent
               │   (Pro Model / Anti-Mocking)  │  Adversarial Acceptance (Deferred)
               ├───────────────────────────────┤
               │           TIER 3              │
               │   Adversarial Challenger      │  Milestone Integration:
               │   (Edge-Case Boundary Fuzz)   │  Hostile Probing (Deferred)
               ├───────────────────────────────┤
               │           TIER 2              │
               │  Independent Component Check  │  Post-Implementation:
               │   (Automated Contract Test)   │  Blocks Branch Merge (Operational)
               ├───────────────────────────────┤
               │           TIER 1              │
               │    Worker Self-Validation     │  In-Context: Local Lint,
               │  (Local Unit Test in Branch)  │  Compile, Fast Checks (Operational)
               └───────────────────────────────┘
```

### Operational Tiers in Phase 5:
- **Tier 1 (Worker Self-Validation)**:
  - **Executor**: The implementing worker within its assigned worktree or workspace.
  - **Scope**: Task-local, targeted unit tests, linters, compile checks, and fast assertions.
  - **Purpose**: Fast feedback before handoff; catches obvious implementation errors early.
  - **Constraint**: Fast and focused; does NOT run repository-wide test suites for simple component edits.
- **Tier 2 (Independent Verification)**:
  - **Executor**: Independent verifier adapter boundary (`IndependentVerifier`).
  - **Independence Invariant**: Tier 2 **never blindly trusts Tier 1 results**. It inspects candidate deliverables, validates cross-boundary contracts, and evaluates deliverables through an isolated verification boundary.
  - **Merge Gating**: Required for high-risk, security-sensitive, or contract-critical tasks.
- **Tiers 3 & 4 (Extensible Base Stubs)**:
  - `Tier3AdversarialVerifier` and `Tier4VictoryAuditVerifier` interfaces are defined in `verifier.py` but explicitly deferred to future phases.

---

## 3. Structured Verification Evidence Model

Every verification decision is backed by a structured `VerificationEvidence` record. No decisions rely on unstructured "trust me" logs.

### Evidence Fields:
- `task_id`: Identifier of the evaluated task.
- `verification_id`: Deterministic unique identifier (`verif_<hex>`).
- `tier`: `VerificationTier` (`TIER_1_SELF_TEST`, `TIER_2_INDEPENDENT`, etc.).
- `status`: `VerificationStatus` (`PASSED`, `FAILED`, `SKIPPED`, `ERROR`).
- `command`: Terminal check or tool invoked (if applicable).
- `exit_code`: Numeric process exit code.
- `stdout_summary`: Deterministically truncated stdout (`<= 500` characters).
- `stderr_summary`: Deterministically truncated stderr (`<= 500` characters).
- `evidence_paths`: References to created test artifacts or outputs.
- `duration`: Execution time in seconds.
- `timestamp`: Epoch timestamp of evaluation.
- `verifier_identity`: Identity of the checking entity.
- `failure_classification`: Diagnostic categorization of any detected defect.
- `details`: Metadata dictionary with check-specific context.

### Log Compactness Invariant
Raw stderr and stdout streams are capped to compact summaries. Gigabyte-scale console dumps are strictly forbidden from in-memory event and task ledgers.

---

## 4. Failure Classification

When verification fails, the failure is categorized by `classify_failure`:

| Classification | Meaning | Examples | Local Repair Eligible? |
|:---|:---|:---|:---:|
| `REPAIRABLE` | Code or test implementation defect | `AssertionError`, `TypeError`, `SyntaxError`, lint failure | **YES** |
| `ENVIRONMENTAL` | Host or external dependency issue | `ModuleNotFoundError`, connection refused, DNS error | NO |
| `INFRASTRUCTURE` | Worker process or adapter crash | OOM killed, SIGKILL, adapter crash, disk full | NO |
| `NON_REPAIRABLE`| Impossible contract / requirement | Incompatible spec, missing external requirements | NO |
| `UNKNOWN` | Unclassified error | Unrecognized log output | NO |

Only failures classified as `REPAIRABLE` enter the automated in-context repair loop. Environmental and infrastructure issues are surfaced to scheduler recovery or parent escalation.

---

## 5. In-Context Local Repair Loop

When a repairable failure is detected and `task.retry_count < task.max_retries`, the orchestrator executes an **in-context local repair**:

```text
VERIFYING (Failure Detected)
       ↓
Classify Failure: REPAIRABLE
       ↓
Check Retry Ceiling: retry_count < max_retries
       ↓
REPAIR_REQUESTED (Emit event with focused RepairPayload)
       ↓
RETRYING (Task retry_count incremented)
       ↓
RUNNING (Same Worker + Same Workspace executes repair)
       ↓
REPAIR_COMPLETED (Worker fixes code and runs local self-test)
       ↓
VERIFYING (Re-run verification)
       ↓
PASSED → MERGE_READY
```

### Key Repair Rules:
1. **Worker & Workspace Reuse**: The warm worker and its isolated worktree branch are preserved. No new worker is spawned; no clean-slate context reconstruction occurs.
2. **Focused Repair Payload**: The worker receives a concise `RepairPayload` containing only:
   - Failed tier and command
   - Compact stderr/stdout summary
   - Affected files from `task.write_set`
   - Suggested remediation
   It does **not** receive full mission prompt history or dump logs.
3. **Strict Retry Boundedness**: Every repair attempt transitions through `TaskState.RETRYING`, incrementing `task.retry_count`. When `task.retry_count >= task.max_retries`, the task transitions to `TaskState.FAILED` and halts downstream dependents without infinite cycling.

---

## 6. Evidence-Gated Merge Queue

Integration into the main codebase via the sequential `MergeQueue` is strictly evidence-gated:

$$\text{MERGE\_READY} = \text{Implementation Completed} \wedge \text{Required Verification PASSED}$$

- **Routine Tasks**: Can be configured with `allow_merge_after_tier1 = True`. Once Tier 1 self-validation passes, the task transitions to `PASSED` and submits to the merge queue.
- **High-Risk Tasks**: Flagged with `risk='high'` or `require_tier2=True`. Even if Tier 1 passes, the task cannot transition to `PASSED` and cannot emit `MERGE_READY` until Tier 2 independent verification succeeds.
- **Failed Tasks**: Tasks with failing verification are **never** enqueued for merge. If retries are exhausted, the task transitions to `FAILED`, releases its workspace ownership with `WorkspaceReleaseState.FAILED`, and blocks dependent tasks.

---

## 7. Verification Event Taxonomy

Phase 5 extends the orchestrator's event model with deterministic verification lifecycle events:

| Event | Payload | Description |
|:---|:---|:---|
| `VERIFICATION_STARTED` | `task_id`, `tier1_enabled`, `tier2_enabled`, `retry_count` | Verification engine begins evaluation of candidate deliverable. |
| `VERIFICATION_PASSED` | `task_id`, `tier`, `evidence_count`, `summary` | All required tiers passed successfully; deliverable accepted. |
| `VERIFICATION_FAILED` | `task_id`, `tier`, `failure_classification`, `summary` | Verification failed at specified tier; diagnostic recorded. |
| `VERIFICATION_RETRYING`| `task_id`, `retry_count`, `error` | Task scheduled for retry attempt. |
| `REPAIR_REQUESTED` | `task_id`, `repair_attempt`, `failed_tier`, `error_summary` | Focused repair payload dispatched to warm worker. |
| `REPAIR_COMPLETED` | `task_id`, `retry_count`, `worker_id` | Worker repaired code in-context; triggers re-verification. |
| `REPAIR_FAILED` | `task_id`, `error` | Worker failed to repair defect; leads to task failure. |

---

## 8. Incremental Parallelism (No Global Barriers)

Verification in Adaptive Orchestrator v5 is **task-local and continuous**:
- When Task A completes execution, it immediately enters `VERIFYING` and validates.
- Task B and Task C continue running uninterrupted in their own workers and worktrees.
- The system never forces an all-worker synchronization barrier before verifying completed work.
