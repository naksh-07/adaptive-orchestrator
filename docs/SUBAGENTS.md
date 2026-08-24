# Subagent Taxonomy & Specialist Roles

Adaptive Orchestrator (v4 Foundation) defines 4 specialized leaf roles and 1 dynamic coordinator pattern.

---

## 1. Leaf Specialist Taxonomy

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        SPECIALIST SUBAGENT ROLES                       │
├──────────────────────┬──────────────────────┬──────────────────────────┤
│ Role                 │ Tool Group           │ Primary Mandate          │
├──────────────────────┼──────────────────────┼──────────────────────────┤
│ Explorer / Researcher│ Read-Only            │ Fast codebase recon      │
│ Implementer          │ Scoped Write         │ Surgical code edits      │
│ Reviewer / Verifier  │ Test & Diff Review   │ Independent verification │
│ Challenger / Auditor │ Adversarial Testing  │ Edge-case Victory audit  │
└──────────────────────┴──────────────────────┴──────────────────────────┘
```

### 1. Explorer / Researcher
- **Configuration**: `enable_write_tools=false`, `enable_subagent_tools=false`, `enable_mcp_tools=true`
- **Mapping**: `TypeName='research'` or `TypeName='self'`
- **Mandate**: Methodical codebase search, log analysis, and architectural tracing. Strictly read-only; never touches project files.

### 2. Implementer (Controlled Writer)
- **Configuration**: `enable_write_tools=true`, `enable_subagent_tools=false`, `enable_mcp_tools=true`
- **Mapping**: `TypeName='self'`
- **Mandate**: Precise, surgical code changes strictly within assigned file boundaries. Produces clean, minimal diffs and runs local syntax/lint checks before handoff.

### 3. Reviewer / Verifier
- **Configuration**: `enable_write_tools=true` (for test execution), `enable_subagent_tools=false`
- **Mapping**: `TypeName='self'` or `TypeName='research'`
- **Mandate**: Independent verification. Executes test suites, validates typecheckers and linters, and inspects git diffs for regressions.

### 4. Challenger / Auditor (Victory-Style)
- **Configuration**: `enable_write_tools=true`, `enable_subagent_tools=false`
- **Mapping**: `TypeName='self'` (Adversarial Prompting)
- **Mandate**: Adversarial stress-testing and prompt compliance audit. Actively attempts to break the solution with boundary conditions, malformed inputs, and edge cases.

---

## 2. Dynamic Coordinator Pattern

When a specialist domain contains $\ge 2$ independent subproblems requiring nested delegation:
1. Root dynamically defines a Coordinator via `define_subagent` with `enable_subagent_tools=true`.
2. Root assigns a strict local budget: `LOCAL_CHILD_BUDGET = N` ($N \le 2$).
3. The Coordinator spawns up to $N$ child leaves, collects their structured handoffs, terminates the children, and returns a synthesized handoff report to Root.
4. Root terminates the Coordinator.
