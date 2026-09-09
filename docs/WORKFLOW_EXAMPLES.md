# Workflow Examples & Scenario Calibration

Real-world execution walkthroughs demonstrating how Adaptive Orchestrator dynamically sizes the workforce.

---

## Scenario 1: Isolated Bug Fix (Mode: FOCUSED, Workforce = 1)

### Task
"Fix race condition in session token refresh logic in `src/auth/session.ts`."

### Execution Trace
1. **Phase 1 Pre-Planning Gate**: Evaluated. 1 domain, 1 isolated bug $ightarrow$ Mode: **FOCUSED**. Initial workforce = 1 Explorer.
2. **Wave 1**: Dispatched `explorer` to trace token refresh timing and locate mutex lock gaps.
3. **Wave 2**: Parent synthesizes findings, outputs `implementation_plan.md`, terminates Explorer (Active = 0).
4. **Phase 2 Execution Gate**: Single writer assigned to `implementer`.
5. **Wave 3**: `implementer` implements mutex locking in `session.ts`.
6. **Wave 4**: Dispatched `reviewer-verifier` to run `npm test test/auth/session.test.ts`.
7. **Wave 5**: Workforce collapsed to 0. Dynamic AIMD capacity scaled appropriately.

---

## Scenario 2: Multi-Domain Full-Stack Feature (Mode: PARALLEL, Workforce = 2)

### Task
"Add webhook retry mechanism with exponential backoff in backend, and display retry status in React dashboard."

### Execution Trace
1. **Phase 1 Pre-Planning Gate**: 2 independent domains (Backend Go service + Frontend React UI) $ightarrow$ Mode: **PARALLEL**. Dispatched 2 Explorers:
   - Worker 1: Trace backend webhook queue and failure handlers.
   - Worker 2: Inspect React webhook table and state hooks.
2. **Wave 2**: Explorers return Handoff Reports. Parent writes unified plan. Explorers terminated (Active = 0).
3. **Phase 2 Execution Gate**: 2 independent implementation streams. Dispatched 2 Implementers with `Workspace='branch'`:
   - Implementer A: Go retry scheduler (`pkg/webhooks/retry.go`).
   - Implementer B: React status badge (`src/components/WebhookRow.tsx`).
4. **Wave 3**: Implementers return diffs. Parent reconciles branches via Integration Manager.
5. **Wave 4**: Dispatched `reviewer-verifier` for Go tests and React component tests.
6. **Wave 5**: Workforce collapsed to 0. Dynamic AIMD capacity scaled appropriately.
