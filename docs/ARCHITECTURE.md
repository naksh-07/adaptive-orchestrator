# Adaptive Orchestrator Architecture (v4 Foundation)

Adaptive Orchestrator is an Antigravity-native multi-agent coordination engine built on a foundational philosophy: **Maximize useful parallel progress, verification rigor, and correctness per credit/token—not the number of spawned agents.**

```text
┌───────────────────────────────────────────────────────────────────────────┐
│                      MANUAL ORCHESTRATOR ACTIVATION                       │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│              PHASE 1: PRE-PLANNING DISPATCH GATE (Recon Triggers)          │
│            • Evaluate: Multidomain (3+), Independent Lanes (2+)           │
│            • Output Pre-Planning Checklist BEFORE local exploration       │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                        WAVE 1: RECON & DISCOVERY                          │
│            • Read-only Explorer/Researcher subagents dispatched           │
│            • Parallel symbol tracing, AST analysis, log inspections       │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                     WAVE 2: SYNTHESIS & ARCH PLAN                         │
│            • Parent orchestrator reconciles explorer handoffs             │
│            • Writes implementation_plan.md                                │
│            • COLLAPSE: Terminate Wave 1 Explorers (Active = 0)            │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │ (User Approval / Auto-Proceed)
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│             PHASE 2: POST-APPROVAL EXECUTION DISPATCH GATE                │
│            • Re-evaluate approved plan for independent write streams      │
│            • Output Execution Dispatch Checklist BEFORE code edits        │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                    WAVE 3: CONTROLLED IMPLEMENTATION                      │
│            • Single Writer Default (or Disjoint Worktree Branches)        │
│            • Scoped diffs, zero collateral edits, self-checks             │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│               WAVE 4: INDEPENDENT VERIFICATION & AUDIT                    │
│            • Reviewer / Verifier: Automated test execution, linters       │
│            • Challenger / Auditor: Adversarial fuzzing, Victory check     │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│               WAVE 5: FINAL DELIVERY & WORKFORCE COLLAPSE                 │
│            • Terminate ALL active subagents (Active Total = 0)            │
│            • Deliver structured Final Orchestration Report                │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 1. The Dual Mandatory Delegation Gates

Traditional multi-agent frameworks often suffer from two failure modes:
1. **Premature Solo Exploration**: An agent burns hundreds of tokens wandering through a massive repo before asking for help.
2. **Uncontrolled Parallel Chaos**: Multiple agents simultaneously write to overlapping files, causing corruptions and merge collisions.

Adaptive Orchestrator solves this with **Two Non-Bypassable Gates**:

### Phase 1: Pre-Planning Dispatch Gate
- **Trigger**: Fired immediately upon orchestrator activation.
- **Rule**: If the task involves 3+ domains, 2+ independent lanes, or 5+ files across 2+ modules, SOLO reconnaissance is **strictly forbidden**.
- **Action**: Output the Pre-Planning Dispatch Checklist and spawn `explorer-researcher` specialists BEFORE calling reading/exploration tools.

### Phase 2: Post-Approval Execution Dispatch Gate
- **Trigger**: Fired after plan approval (or Auto-Proceed), BEFORE any code modification.
- **Rule**: Re-evaluates the concrete implementation streams. If $\ge 2$ independent streams exist, SOLO implementation is forbidden.
- **Action**: Output the Execution Dispatch Checklist and dispatch scoped implementers with isolated worktrees (`Workspace='branch'`).

---

## 2. Hard Resource Limits & Shared Ceiling

Every mission strictly adheres to budget bounds across the entire hierarchy:

| Metric | Normal Default | Mission Hard Limit |
|:---|:---:|:---:|
| **Concurrent Subagents** | 2 | **4 Max Tree-Wide** |
| **Total Launches** | 4–6 | **10 Max Shared Ceiling** |

### Concurrency Invariant:
$$	ext{GLOBAL\_ACTIVE} = 	ext{Root\_Direct\_Leaves} + \sum 	ext{Reserved\_Coordinator\_Allocations} \le 4$$

### Launch Ceiling Invariant:
$$	ext{SPAWNED\_TOTAL} = 	ext{Root\_Launches} + 	ext{Coordinator\_Launches} + 	ext{Replacements} \le 10$$

---

## 3. The Fundamental Law: READ PARALLEL — WRITE CONTROLLED

- **Read Operations**: Safe to execute in parallel across up to 4 concurrent subagents.
- **Write Operations**: Uncoordinated parallel writing is prohibited.
  - **Single Writer Default**: One assigned implementer executes tightly coupled modifications.
  - **Disjoint Worktrees (`Workspace='branch'`)**: If two workers modify distinct modules, each operates in an isolated workspace; the parent merges and verifies diffs before completion.

---

## 4. Operational & Hierarchical Workforce Collapse

The active workforce shrinks as problem uncertainty decreases:
1. **Wave 1 Explorers** are killed via `manage_subagents(Action='kill')` as soon as Wave 2 Synthesis begins.
2. **Hierarchical Coordinators** kill their children before reporting to Root.
3. **Wave 3 Implementers** are collapsed before Wave 4 Verification.
4. **Final Active Total** must reach **0** before final delivery is reported.
