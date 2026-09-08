# Subagent Taxonomy & Specialist Roles (v5 Architecture)

Adaptive Orchestrator v5 defines 4 canonical native leaf roles for Google Antigravity, internal domain metadata, and native lifecycle bridging.

---

## 1. Canonical Native Agents (`.agents/agents/`)

These agent definitions are canonically placed in `.agents/agents/<name>/agent.md` (or globally in `~/.gemini/config/agents/<name>/agent.md`) and discovered by Antigravity's native runtime:

```text
+----------------------+------------+-------+-------------------------------------------------------------+
| Agent Name           | Model      | Nature| Allowed Tools                                               |
+----------------------+------------+-------+-------------------------------------------------------------+
| explorer             | flash      | Read  | view_file, grep_search, find_by_name, list_dir,             |
|                      |            |       | read_url_content, search_web                                |
| implementer          | pro        | Write | view_file, grep_search, find_by_name, list_dir,             |
|                      |            |       | write_to_file, replace_file_content, run_command             |
| reviewer-verifier    | flash      | Test  | view_file, grep_search, find_by_name, list_dir, run_command |
| challenger-auditor   | pro        | Audit | view_file, grep_search, find_by_name, list_dir, run_command |
+----------------------+------------+-------+-------------------------------------------------------------+
```

### 1. Explorer (`explorer`)
- **Native YAML**: `subagent: true`, `model: flash`
- **Tool Group**: Strictly read-only Antigravity tools.
- **Mandate**: Rapid reconnaissance, symbol search, execution path tracing, error diagnosis without modifying workspace files.
- **Model Routing**: `FAST` tier (`flash`).

### 2. Implementer (`implementer`)
- **Native YAML**: `subagent: true`, `model: pro`
- **Tool Group**: Scoped editing tools (`write_to_file`, `replace_file_content`, `run_command`).
- **Mandate**: Surgical code implementation strictly within assigned write sets on isolated worktree branches (`WorkspaceMode.BRANCH`).
- **Model Routing**: `PRO` tier (`pro`).

### 3. Reviewer / Verifier (`reviewer-verifier`)
- **Native YAML**: `subagent: true`, `model: flash`
- **Tool Group**: Read tools plus `run_command` for test suite execution.
- **Mandate**: Tier 2 Independent Verification. Executes test suites, validates typecheckers and linters, and inspects git diffs for regressions.
- **Model Routing**: `FAST` tier (`flash`).

### 4. Challenger / Auditor (`challenger-auditor`)
- **Native YAML**: `subagent: true`, `model: pro`
- **Tool Group**: Read tools plus `run_command` for adversarial scripts.
- **Mandate**: Tier 3 Adversarial Challenge and Tier 4 Victory Audit. Validates write-set exclusivity, security invariants, and whole-mission acceptance criteria.
- **Model Routing**: `PRO` tier (`pro`).

---

## 2. Antigravity Invocation Protocol

### Initial Spawn (Fresh Worker)
When a task is dispatched to an uninitialized worker, `NativeExecutionAdapter` generates an `invoke_subagent` instruction:
```json
{
  "TypeName": "implementer",
  "Role": "Backend Specialist",
  "Prompt": "# Mission Task: task_1 ...",
  "Model": "pro",
  "Workspace": "branch"
}
```
If the environment has not discovered `.agents/agents/`, the orchestrator falls back to built-in types (`TypeName='self'` or `TypeName='research'`), or defines them via `define_subagent`.

### Warm Worker Reuse (`send_message`)
Once Antigravity returns the subagent's `conversationID`, the orchestrator binds it via `registry.bind_native_conversation(worker_id, conversation_id)`. Subsequent dispatches generate `send_message` instructions:
```json
{
  "Recipient": "<conversationID>",
  "Message": "# Mission Task: task_2 ...\nYou are in your existing warm workspace. Continue with assigned task."
}
```

### In-Context Local Repair (`send_message`)
When Tier 1–3 verification detects a repairable defect, the orchestrator re-awakens the worker's existing session:
```json
{
  "Recipient": "<conversationID>",
  "Message": "# IN-CONTEXT REPAIR REQUEST for Task: task_1\n**Attempt**: 1 / 2\n**Failure Classification**: SYNTAX_ERROR\n..."
}
```

---

## 3. Internal Registry vs Native Definitions

- **`.agents/agents/`**: Canonical Antigravity-discoverable agent definitions with YAML frontmatter.
- **`subagents/`**: Internal metadata and role documentation used by the Python engine.
- **`manifest.json` & `plugin.json`**: Reference both canonical native agents and internal specifications for backward compatibility.
