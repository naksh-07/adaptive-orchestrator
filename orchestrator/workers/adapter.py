"""
Adaptive Orchestrator v5 - Execution Adapter & Worker Wake Boundary.
Handles execution abstraction, passing tasks and execution profiles to workers.
"""

from __future__ import annotations

import inspect
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from orchestrator.models import Task
from orchestrator.routing.models import ExecutionProfile, ModelTier
from orchestrator.workers.models import Worker


@dataclass
class ExecutionResult:
    """
    Structured outcome of a worker task execution.
    """
    success: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration: float = 0.0
    is_reuse: bool = False
    is_capacity_error: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "result": self.result,
            "error": self.error,
            "duration": self.duration,
            "is_reuse": self.is_reuse,
            "is_capacity_error": self.is_capacity_error,
        }


class ExecutionAdapter(ABC):
    """
    Abstract Execution Adapter separating scheduling from actual process/subagent invocation.
    Antigravity process spawning and send_message wake mechanics live behind this interface.
    """

    @abstractmethod
    def dispatch(
        self,
        worker: Worker,
        task: Task,
        execution_profile: Optional[ExecutionProfile] = None,
    ) -> ExecutionResult:
        """
        Dispatches an assigned task to the designated worker with an execution profile.
        Returns the ExecutionResult upon task completion.
        """
        raise NotImplementedError

    def is_reuse(self, worker: Worker) -> bool:
        """
        Determines whether dispatching to this worker is a wake/reuse operation
        or an initial spawn.
        """
        return len(worker.task_history) > 0 or worker.metrics.tasks_completed > 0

    def request_repair(
        self,
        worker: Worker,
        task: Task,
        payload: Any,
    ) -> Optional[ExecutionResult]:
        """
        Sends a targeted repair request to the assigned worker in its existing context.
        Returns the ExecutionResult after repair is attempted.
        """
        raise NotImplementedError


class MockExecutionAdapter(ExecutionAdapter):
    """
    Deterministic Mock Execution Adapter for Phase 2 & 3 testing and simulations.
    Records all invocations, tracks worker reuse vs fresh spawn, routes execution profiles,
    and supports simulating rate limits and capacity errors.
    """

    def __init__(
        self,
        default_success: bool = True,
        default_result: Optional[Dict[str, Any]] = None,
        simulate_duration: float = 0.0,
        auto_complete: bool = True,
    ) -> None:
        self.default_success = default_success
        self.default_result = default_result or {}
        self.simulate_duration = simulate_duration
        self.auto_complete = auto_complete

        # Configuration overrides
        self.task_results: Dict[str, ExecutionResult] = {}
        self.fail_tasks: Set[str] = set()
        self.rate_limit_tasks: Set[str] = set()

        # Telemetry & call records
        self.dispatches: List[Dict[str, Any]] = []
        self.repair_requests: List[Dict[str, Any]] = []
        self.repair_results: Dict[str, ExecutionResult] = {}
        self.default_repair_success: bool = True
        self.spawn_count: int = 0
        self.reuse_count: int = 0

    def dispatch(
        self,
        worker: Worker,
        task: Task,
        execution_profile: Optional[ExecutionProfile] = None,
    ) -> Optional[ExecutionResult]:
        """
        Executes mock dispatch deterministically.
        If auto_complete is False, returns None to simulate async execution.
        """
        if not self.auto_complete:
            record = {
                "worker_id": worker.worker_id,
                "task_id": task.task_id,
                "domain": worker.domain,
                "is_reuse": self.is_reuse(worker),
                "execution_profile": execution_profile.to_dict() if execution_profile else None,
                "timestamp": time.time(),
            }
            self.dispatches.append(record)
            return None

        reused = self.is_reuse(worker)

        if reused:
            self.reuse_count += 1
        else:
            self.spawn_count += 1

        record = {
            "worker_id": worker.worker_id,
            "task_id": task.task_id,
            "domain": worker.domain,
            "is_reuse": reused,
            "execution_profile": execution_profile.to_dict() if execution_profile else None,
            "timestamp": time.time(),
        }
        self.dispatches.append(record)

        # Check explicit overrides
        if task.task_id in self.task_results:
            res = self.task_results[task.task_id]
            res.is_reuse = reused
            return res

        # Check simulated rate limit / capacity errors
        if task.task_id in self.rate_limit_tasks:
            return ExecutionResult(
                success=False,
                error=f"HTTP 429: RESOURCE_EXHAUSTED for task '{task.task_id}'",
                duration=self.simulate_duration,
                is_reuse=reused,
                is_capacity_error=True,
            )

        if task.task_id in self.fail_tasks or not self.default_success:
            return ExecutionResult(
                success=False,
                error=f"Mock failure on task '{task.task_id}'",
                duration=self.simulate_duration,
                is_reuse=reused,
                is_capacity_error=False,
            )

        return ExecutionResult(
            success=True,
            result=dict(self.default_result),
            duration=self.simulate_duration,
            is_reuse=reused,
            is_capacity_error=False,
        )

    def request_repair(
        self,
        worker: Worker,
        task: Task,
        payload: Any,
    ) -> Optional[ExecutionResult]:
        """
        Processes a mock repair request, recording the call and returning simulated outcome.
        """
        record = {
            "worker_id": worker.worker_id,
            "task_id": task.task_id,
            "domain": worker.domain,
            "payload": payload.to_dict() if hasattr(payload, "to_dict") else str(payload),
            "timestamp": time.time(),
        }
        self.repair_requests.append(record)
        self.reuse_count += 1

        if not self.auto_complete:
            return None

        if task.task_id in self.repair_results:
            res = self.repair_results[task.task_id]
            res.is_reuse = True
            return res

        return ExecutionResult(
            success=self.default_repair_success,
            result={"repaired": True, "task_id": task.task_id},
            error=None if self.default_repair_success else f"Mock repair failed for task '{task.task_id}'",
            duration=self.simulate_duration,
            is_reuse=True,
        )


class LocalExecutionAdapter(ExecutionAdapter):
    """
    Execution adapter that invokes a Python callable for each dispatched task.
    Enables local in-process testing or custom simulation handlers.
    """

    def __init__(
        self,
        handler: Callable[..., ExecutionResult]
    ) -> None:
        self._handler = handler
        self.dispatches: List[Dict[str, Any]] = []

    def dispatch(
        self,
        worker: Worker,
        task: Task,
        execution_profile: Optional[ExecutionProfile] = None,
    ) -> ExecutionResult:
        reused = self.is_reuse(worker)
        self.dispatches.append({
            "worker_id": worker.worker_id,
            "task_id": task.task_id,
            "is_reuse": reused,
            "execution_profile": execution_profile.to_dict() if execution_profile else None,
            "timestamp": time.time(),
        })

        # Support handler taking 2 args (worker, task) or 3 args (worker, task, execution_profile)
        sig = inspect.signature(self._handler)
        params_count = len(sig.parameters)
        if params_count >= 3:
            result = self._handler(worker, task, execution_profile)
        else:
            result = self._handler(worker, task)

        result.is_reuse = reused
        return result

    def request_repair(
        self,
        worker: Worker,
        task: Task,
        payload: Any,
    ) -> ExecutionResult:
        self.dispatches.append({
            "worker_id": worker.worker_id,
            "task_id": task.task_id,
            "is_reuse": True,
            "is_repair": True,
            "payload": payload.to_dict() if hasattr(payload, "to_dict") else str(payload),
            "timestamp": time.time(),
        })
        sig = inspect.signature(self._handler)
        params_count = len(sig.parameters)
        if params_count >= 3:
            result = self._handler(worker, task, payload)
        else:
            result = self._handler(worker, task)
        result.is_reuse = True
        return result


@dataclass
class NativeDispatchInstruction:
    """
    Structured native dispatch instruction ready for execution by the parent Antigravity agent.
    Separates Python scheduling from the actual LLM tool-calling boundary.
    """
    action: str  # 'invoke_subagent' or 'send_message'
    worker_id: str
    task_id: str
    recipient: Optional[str] = None  # Antigravity conversation ID if send_message
    type_name: str = "implementer"
    role: str = ""
    prompt: str = ""
    model: str = "inherit"  # 'flash', 'pro', 'inherit', 'flash_lite'
    workspace: str = "branch"  # 'branch', 'inherit', 'share'
    is_reuse: bool = False
    timestamp: float = field(default_factory=time.time)

    @property
    def is_first_launch(self) -> bool:
        return self.action == "invoke_subagent"

    @property
    def requires_subagent_invocation(self) -> bool:
        return self.action == "invoke_subagent"

    @property
    def requires_message_send(self) -> bool:
        return self.action == "send_message"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "worker_id": self.worker_id,
            "task_id": self.task_id,
            "recipient": self.recipient,
            "type_name": self.type_name,
            "role": self.role,
            "prompt": self.prompt,
            "model": self.model,
            "workspace": self.workspace,
            "is_reuse": self.is_reuse,
            "timestamp": self.timestamp,
        }

    def to_subagent_payload(self) -> Dict[str, Any]:
        """Formats the payload for the invoke_subagent tool."""
        return {
            "TypeName": self.type_name,
            "Role": self.role or f"{self.type_name.capitalize()} Worker",
            "Prompt": self.prompt,
            "Model": self.model,
            "Workspace": self.workspace,
        }

    def to_invoke_subagent_payload(self) -> Dict[str, Any]:
        """Alias for to_subagent_payload."""
        return self.to_subagent_payload()

    def to_send_message_payload(self) -> Dict[str, Any]:
        """Formats the payload for the send_message tool."""
        return {
            "Recipient": self.recipient or "",
            "Message": self.prompt,
        }


@dataclass
class NativeRepairInstruction:
    """
    Targeted in-context repair instruction to re-awaken an existing native conversation.
    """
    action: str = "send_message"
    worker_id: str = ""
    task_id: str = ""
    recipient: str = ""  # Antigravity conversation ID
    message: str = ""
    attempt_number: int = 1
    is_reuse: bool = True
    timestamp: float = field(default_factory=time.time)

    @property
    def recipient_conversation_id(self) -> str:
        return self.recipient

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "worker_id": self.worker_id,
            "task_id": self.task_id,
            "recipient": self.recipient,
            "message": self.message,
            "attempt_number": self.attempt_number,
            "is_reuse": self.is_reuse,
            "timestamp": self.timestamp,
        }

    def to_send_message_payload(self) -> Dict[str, Any]:
        """Formats payload for the send_message tool."""
        return {
            "Recipient": self.recipient,
            "Message": self.message,
        }


class NativeExecutionAdapter(ExecutionAdapter):
    """
    Truthful Native Antigravity Execution Adapter.
    Bridges the internal Python MissionEngine to the real Antigravity tool calling boundary.
    
    Generates typed NativeDispatchInstruction and NativeRepairInstruction records that the
    parent Antigravity agent executes via invoke_subagent or send_message.
    Ingests observable native conversation IDs, tracks real conversation reuse, and provides
    truthful telemetry without faking native execution.
    """

    def __init__(
        self,
        repo_dir: Optional[str] = None,
        execution_handler: Optional[Callable[..., Any]] = None,
        auto_complete: bool = False,
    ) -> None:
        self.repo_dir = repo_dir
        self._execution_handler = execution_handler
        self.auto_complete = auto_complete

        # Instructions generated for the native Antigravity boundary
        self.pending_instructions: List[Any] = []
        self.dispatches: List[Dict[str, Any]] = []
        self.repair_requests: List[Dict[str, Any]] = []

        # Worker to native conversation mapping
        self.active_conversations: Dict[str, str] = {}  # worker_id -> conversation_id
        self.worker_dispatch_counts: Dict[str, int] = {}
        self.task_results: Dict[str, ExecutionResult] = {}
        self.repair_results: Dict[str, ExecutionResult] = {}

        # Distinct reuse metrics
        self.spawn_count: int = 0
        self.internal_reuse_count: int = 0
        self.native_reuse_count: int = 0

    @property
    def total_reuse_count(self) -> int:
        return self.internal_reuse_count + self.native_reuse_count

    @property
    def last_instruction(self) -> Optional[Any]:
        return self.pending_instructions[-1] if self.pending_instructions else None

    def is_reuse(self, worker: Worker) -> bool:
        """
        Determines whether dispatching to this worker is a wake/reuse operation
        or an initial spawn.
        """
        return (
            len(worker.task_history) > 0
            or worker.metrics.tasks_completed > 0
            or self.worker_dispatch_counts.get(worker.worker_id, 0) > 0
        )

    def resolve_native_agent_name(self, worker: Worker, task: Task) -> str:
        """
        Maps a domain worker and task to the canonical native Antigravity subagent name
        discoverable in .agents/agents/.
        """
        domain = (worker.domain or "").lower()
        raw_type = getattr(task, "task_type", None) or (task.metadata.get("task_type") if hasattr(task, "metadata") and isinstance(task.metadata, dict) else "")
        task_type = str(raw_type or "").upper()

        if domain in ("discovery", "research", "architecture") or task_type == "EXPLORATION":
            return "explorer"
        if domain in ("test", "quality", "verification") or task_type in ("VERIFICATION", "TEST"):
            return "reviewer-verifier"
        if domain in ("security", "adversarial", "audit") or task_type in ("ADVERSARIAL_CHALLENGE", "VICTORY_AUDIT"):
            return "challenger-auditor"
        return "implementer"

    def resolve_native_model(self, profile: Optional[ExecutionProfile]) -> str:
        """
        Maps the execution profile to the exact model enum supported by invoke_subagent:
        'flash', 'pro', 'inherit', 'flash_lite'.
        """
        if profile is None:
            return "inherit"
        tier_val = str(profile.tier.value if hasattr(profile.tier, "value") else profile.tier).upper()
        if "PRO" in tier_val:
            return "pro"
        if "FAST" in tier_val:
            return "flash"
        return "inherit"

    def resolve_native_workspace(self, task: Task) -> str:
        """
        Resolves the Antigravity native workspace mode: 'branch', 'inherit', or 'share'.
        """
        mode = (task.workspace_mode or "branch").lower()
        if mode in ("branch", "share", "inherit"):
            return mode
        return "branch"

    def build_task_prompt(
        self,
        worker: Worker,
        task: Task,
        profile: Optional[ExecutionProfile] = None,
    ) -> str:
        """Formats the prompt sent to the native subagent."""
        lines = [
            f"# Mission Task: {task.task_id}",
            f"**Objective**: {task.description or task.task_id}",
            f"**Domain**: {worker.domain}",
        ]
        if task.write_set:
            lines.append(f"**Assigned Write Set**: {sorted(list(task.write_set))}")
        if task.workspace_path:
            lines.append(f"**Workspace Path**: {task.workspace_path}")
        if profile:
            lines.append(f"**Execution Tier**: {profile.tier.value if hasattr(profile.tier, 'value') else profile.tier}")

        lines.extend([
            "",
            "## Requirements",
            "1. Execute strictly within your assigned role and boundary.",
            "2. Ground all changes or findings with verifiable evidence.",
            "3. Conclude your turn with a structured HANDOFF REPORT.",
        ])
        return "\n".join(lines)

    def build_repair_prompt(
        self,
        worker: Worker,
        task: Task,
        payload: Any,
    ) -> str:
        """Formats an in-context repair prompt for an existing native conversation."""
        payload_dict = payload.to_dict() if hasattr(payload, "to_dict") else {"details": str(payload)}
        lines = [
            f"# IN-CONTEXT REPAIR REQUEST for Task: {task.task_id}",
            f"**Attempt**: {task.retry_count} / {task.max_retries}",
            f"**Failure Classification**: {payload_dict.get('failure_classification', 'REPAIRABLE')}",
            f"**Summary**: {payload_dict.get('summary', 'Verification failed')}",
            "",
            "## Verification Evidence & Errors",
            str(payload_dict.get("error_details") or payload_dict.get("details") or ""),
            "",
            "## Repair Instructions",
            "1. You are in your existing warm workspace. DO NOT start from scratch.",
            "2. Review the verification failure details above.",
            "3. Apply targeted surgical repairs strictly to the affected files.",
            "4. Run local Tier 1 validation to ensure the fix resolves the failure.",
            "5. Return an updated HANDOFF REPORT confirming the repair.",
        ]
        return "\n".join(lines)

    def generate_dispatch_instruction(
        self,
        worker: Worker,
        task: Task,
        execution_profile: Optional[ExecutionProfile] = None,
    ) -> NativeDispatchInstruction:
        """
        Generates a typed NativeDispatchInstruction for the task and worker.
        """
        native_agent = self.resolve_native_agent_name(worker, task)
        native_model = self.resolve_native_model(execution_profile)
        native_ws = self.resolve_native_workspace(task)
        prompt = self.build_task_prompt(worker, task, execution_profile)

        cid = worker.conversation_id or self.active_conversations.get(worker.worker_id)
        has_native_conversation = bool(cid)
        reused = self.is_reuse(worker)

        action = "send_message" if has_native_conversation else "invoke_subagent"
        recipient = cid if has_native_conversation else None

        return NativeDispatchInstruction(
            action=action,
            worker_id=worker.worker_id,
            task_id=task.task_id,
            recipient=recipient,
            type_name=native_agent,
            role=f"{worker.domain.capitalize()} Specialist",
            prompt=prompt,
            model=native_model,
            workspace=native_ws,
            is_reuse=reused or has_native_conversation,
        )

    def generate_repair_instruction(
        self,
        worker: Worker,
        task: Task,
        payload: Any,
    ) -> NativeRepairInstruction:
        """
        Generates a typed NativeRepairInstruction targeting the existing worker session.
        """
        cid = worker.conversation_id or self.active_conversations.get(worker.worker_id)
        repair_msg = self.build_repair_prompt(worker, task, payload)

        return NativeRepairInstruction(
            action="send_message",
            worker_id=worker.worker_id,
            task_id=task.task_id,
            recipient=cid or "",
            message=repair_msg,
            is_reuse=True,
        )

    def dispatch(
        self,
        worker: Worker,
        task: Task,
        execution_profile: Optional[ExecutionProfile] = None,
    ) -> Optional[ExecutionResult]:
        """
        Dispatches a task by preparing a native instruction (invoke_subagent or send_message).
        """
        instruction = self.generate_dispatch_instruction(worker, task, execution_profile)

        cid = worker.conversation_id or self.active_conversations.get(worker.worker_id)
        has_native_conversation = bool(cid)
        reused = self.is_reuse(worker)

        if has_native_conversation:
            self.native_reuse_count += 1
        else:
            if reused:
                self.internal_reuse_count += 1
            else:
                self.spawn_count += 1

        self.worker_dispatch_counts[worker.worker_id] = self.worker_dispatch_counts.get(worker.worker_id, 0) + 1

        # Update worker native metadata
        worker.native_agent_name = instruction.type_name
        worker.model_tier = execution_profile.tier.value if execution_profile and hasattr(execution_profile.tier, "value") else None
        worker.resolved_model = instruction.model
        worker.workspace_mode = instruction.workspace
        worker.is_native = True

        self.pending_instructions.append(instruction)

        record = {
            "worker_id": worker.worker_id,
            "task_id": task.task_id,
            "native_agent": instruction.type_name,
            "action": instruction.action,
            "recipient": instruction.recipient,
            "is_reuse": reused or has_native_conversation,
            "is_native_reuse": has_native_conversation,
            "execution_profile": execution_profile.to_dict() if execution_profile else None,
            "timestamp": time.time(),
        }
        self.dispatches.append(record)

        # Check explicit overrides
        if task.task_id in self.task_results:
            res = self.task_results[task.task_id]
            res.is_reuse = reused or has_native_conversation
            return res

        # If a live execution handler is attached, invoke it
        if self._execution_handler is not None:
            sig = inspect.signature(self._execution_handler)
            if len(sig.parameters) >= 3:
                res = self._execution_handler(worker, task, instruction)
            else:
                res = self._execution_handler(worker, task)
            if isinstance(res, ExecutionResult):
                res.is_reuse = reused or has_native_conversation
                return res

        if not self.auto_complete:
            return None

        # Synchronous fallback if auto_complete is configured
        return ExecutionResult(
            success=True,
            result={"instruction": instruction.to_dict()},
            is_reuse=reused or has_native_conversation,
        )

    def request_repair(
        self,
        worker: Worker,
        task: Task,
        payload: Any,
    ) -> Optional[ExecutionResult]:
        """
        Dispatches an in-context repair request via send_message to the existing native conversation.
        """
        instruction = self.generate_repair_instruction(worker, task, payload)
        self.pending_instructions.append(instruction)

        cid = worker.conversation_id or self.active_conversations.get(worker.worker_id)

        if cid:
            self.native_reuse_count += 1
        else:
            self.internal_reuse_count += 1

        record = {
            "worker_id": worker.worker_id,
            "task_id": task.task_id,
            "action": "send_message",
            "recipient": cid,
            "payload": payload.to_dict() if hasattr(payload, "to_dict") else str(payload),
            "timestamp": time.time(),
        }
        self.repair_requests.append(record)

        if task.task_id in self.repair_results:
            res = self.repair_results[task.task_id]
            res.is_reuse = True
            return res

        if self._execution_handler is not None:
            res = self._execution_handler(worker, task, instruction)
            if isinstance(res, ExecutionResult):
                res.is_reuse = True
                return res

        if not self.auto_complete:
            return None

        return ExecutionResult(
            success=True,
            result={"repaired": True, "instruction": instruction.to_dict()},
            is_reuse=True,
        )

    def record_native_result(
        self,
        task_id: str,
        worker_id: str,
        success: bool,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        conversation_id: Optional[str] = None,
        duration: float = 0.0,
    ) -> ExecutionResult:
        """
        Records the verified outcome from a real native Antigravity tool call.
        Binds the real Antigravity conversation ID to the worker if observed.
        """
        if conversation_id:
            self.active_conversations[worker_id] = conversation_id

        exec_res = ExecutionResult(
            success=success,
            result=result or {},
            error=error,
            duration=duration,
            is_reuse=bool(conversation_id and conversation_id in self.active_conversations.values()),
        )
        self.task_results[task_id] = exec_res
        return exec_res

    def drain_pending_instructions(self) -> List[Any]:
        """Returns and clears all queued native dispatch/repair instructions."""
        pending = list(self.pending_instructions)
        self.pending_instructions.clear()
        return pending

