# Adaptive Orchestration Rules (v5 Architecture)

Whenever `/adaptive-orchestrator` is invoked or complex multi-file, multi-domain, or high-risk tasks are requested:
1. **Activate the `adaptive-orchestrator` skill**: Enter orchestrator mode.
2. **ZERO DIRECT WORK INVARIANT**: As the parent orchestrator, you are strictly prohibited from calling `write_to_file` or `replace_file_content` to edit project source code directly, and prohibited from broad manual file reads. You coordinate; subagents execute.
3. **MANDATORY SUBAGENT INVOCATION**:
   - **Phase 1 (Reconnaissance)**: ALWAYS dispatch `explorer` subagent via `invoke_subagent` before planning.
   - **Phase 2 (Implementation)**: ALWAYS dispatch `implementer` subagent via `invoke_subagent` (or reuse warm workers via `send_message`).
   - **Phase 3 (Verification)**: ALWAYS dispatch `reviewer-verifier` subagent via `invoke_subagent`.
   - **Phase 4 (Victory Audit)**: ALWAYS dispatch `challenger-auditor` subagent via `invoke_subagent`.
4. **Adaptive Capacity & Resource Policy**: Dynamic physical concurrency governed by AIMD scheduler policy with decoupled logical DAG width; prefer worker reuse via `send_message` over repeated launches.
5. **Enforce the Fundamental Law: READ PARALLEL — WRITE CONTROLLED**: Exclusive branch/worktree isolation and sequential merge queue.
6. **Progress through the 4-Tier Verification Pyramid**: Tier 1 Self-Test → Tier 2 Independent Verification → Tier 3 Adversarial Challenge → Tier 4 Victory Audit with in-context local repair loops.
7. **Maintain Durable State**: Atomic checkpoints and crash recovery.
8. **Graceful Workforce Finalization**: Complete lifecycle through Tier 4 Victory Audit and conclude active workforce with `manage_subagents(Action='kill_all')`.

