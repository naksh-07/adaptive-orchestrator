"""
Adaptive Orchestrator v5 - Failure Classification & In-Context Local Repair Loop.
Coordinates focused defect recovery without destroying warm worker contexts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from orchestrator.verification.models import (
    FailureClassification,
    VerificationEvidence,
    VerificationResult,
    truncate_summary,
)

if TYPE_CHECKING:
    from orchestrator.models import Task
    from orchestrator.verification.policy import VerificationPolicy
    from orchestrator.workers.adapter import ExecutionAdapter
    from orchestrator.workers.models import Worker


def classify_failure(
    evidence: Optional[VerificationEvidence] = None,
    error_message: str = "",
) -> FailureClassification:
    """
    Deterministically classifies verification failures based on evidence summaries,
    exit codes, and error messages.
    """
    # If evidence already has an explicit classification, respect it
    if evidence and evidence.failure_classification:
        return evidence.failure_classification

    combined_text = ""
    if evidence:
        combined_text += f"{evidence.stderr_summary} {evidence.stdout_summary} "
    combined_text += error_message
    lower_err = combined_text.lower()

    # 1. Environmental Indicators
    env_keywords = [
        "modulenotfounderror",
        "nodename nor servname provided",
        "connection refused",
        "no route to host",
        "network unreachable",
        "dns resolution failed",
        "service unavailable",
        "dependency not found",
        "could not find a version that satisfies the requirement",
        "environment error",
    ]
    if any(kw in lower_err for kw in env_keywords):
        return FailureClassification.ENVIRONMENTAL

    # 2. Infrastructure Indicators
    infra_keywords = [
        "killed",
        "out of memory",
        "disk space",
        "permission denied",
        "sigkill",
        "sigterm",
        "timed out",
        "adapter crash",
        "runtime error",
        "process terminated unexpectedly",
    ]
    if any(kw in lower_err for kw in infra_keywords):
        return FailureClassification.INFRASTRUCTURE

    # 3. Code/Test Defect Indicators (Repairable)
    repairable_keywords = [
        "assertionerror",
        "failed",
        "failure",
        "syntaxerror",
        "typeerror",
        "nameerror",
        "attributeerror",
        "valueerror",
        "indexerror",
        "keyerror",
        "lint error",
        "mypy error",
        "flake8",
        "exit status 1",
        "exit code 1",
        "test_fail",
        "tests failed",
    ]
    if any(kw in lower_err for kw in repairable_keywords):
        return FailureClassification.REPAIRABLE

    # Default fallback
    return FailureClassification.UNKNOWN


@dataclass
class RepairPayload:
    """
    Focused, compact repair instructions sent to the implementing worker.
    Does NOT reconstruct or resend the entire mission context or large logs.
    """
    task_id: str
    repair_attempt: int
    failed_tier: str
    failed_command: Optional[str] = None
    failure_classification: FailureClassification = FailureClassification.REPAIRABLE
    error_summary: str = ""
    affected_files: List[str] = field(default_factory=list)
    suggested_action: str = ""
    timestamp: float = field(default_factory=time.time)
    worker_id: Optional[str] = None

    def __post_init__(self) -> None:
        self.error_summary = truncate_summary(self.error_summary)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "repair_attempt": self.repair_attempt,
            "failed_tier": self.failed_tier,
            "failed_command": self.failed_command,
            "failure_classification": self.failure_classification.value,
            "error_summary": self.error_summary,
            "affected_files": list(self.affected_files),
            "suggested_action": self.suggested_action,
            "timestamp": self.timestamp,
            "worker_id": self.worker_id,
        }


class RepairCoordinator:
    """
    Coordinates local in-context repairs.
    Determines repairability, enforces retry bounds, generates focused repair payloads,
    and preserves worker and workspace context.
    """

    def can_repair(
        self,
        task: Task,
        verification_result: VerificationResult,
        policy: Optional[VerificationPolicy] = None,
    ) -> bool:
        """
        Determines whether the failed task is eligible for an in-context repair attempt.
        """
        if policy and not policy.auto_repair:
            return False

        # Must be classified as REPAIRABLE
        classification = verification_result.failure_classification
        if not classification:
            classification = classify_failure(error_message=verification_result.summary)
        if not classification or not classification.is_repairable:
            return False

        # Must not exceed task retry ceiling
        max_retries = policy.max_repair_attempts if policy else task.max_retries
        if task.retry_count >= max_retries:
            return False

        return True

    def build_repair_payload(
        self,
        task: Task,
        verification_result: VerificationResult,
    ) -> RepairPayload:
        """
        Extracts concise failure details and constructs a focused RepairPayload.
        """
        failed_ev = next(
            (e for e in verification_result.evidences if not e.is_success),
            None
        )

        error_summary = ""
        failed_cmd = None
        if failed_ev:
            failed_cmd = failed_ev.command
            error_summary = failed_ev.stderr_summary or failed_ev.stdout_summary or verification_result.summary
        else:
            error_summary = verification_result.summary

        classification = verification_result.failure_classification or classify_failure(error_message=verification_result.summary)

        return RepairPayload(
            task_id=task.task_id,
            repair_attempt=task.retry_count + 1,
            failed_tier=verification_result.tier.value,
            failed_command=failed_cmd,
            failure_classification=classification,
            error_summary=error_summary,
            affected_files=sorted(list(task.write_set)),
            suggested_action=f"Fix defect identified by {verification_result.tier.value}. Verify changes locally.",
            worker_id=getattr(task, "assigned_worker_id", None),
        )
