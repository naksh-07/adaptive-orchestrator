"""
Adaptive Orchestrator v5 - Verifier Interfaces & Implementations.
Tier 1 (Self-Test), Tier 2 (Independent Verification), and extensible tier stubs.
"""

from __future__ import annotations

import subprocess
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from orchestrator.verification.models import (
    FailureClassification,
    VerificationEvidence,
    VerificationResult,
    VerificationStatus,
    VerificationTier,
)
from orchestrator.verification.policy import VerificationPolicy, resolve_policy_for_task

if TYPE_CHECKING:
    from orchestrator.models import Task


class Verifier(ABC):
    """
    Abstract Base Class for all verifiers.
    Strictly non-mutating: observes deliverables, runs checks, produces evidence.
    """

    @abstractmethod
    def verify(
        self,
        task: Task,
        policy: Optional[VerificationPolicy] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        """
        Executes verification for a task deliverable and returns a structured result.
        """
        raise NotImplementedError


class Tier1SelfTestVerifier(Verifier):
    """
    Tier 1: Worker Self-Validation.
    Executed in the context of the implementing worker's workspace.
    Runs task-specific fast checks, lint, and targeted unit tests.
    """

    def __init__(
        self,
        command_runner: Optional[Callable[[str, Optional[str], float], Tuple[int, str, str]]] = None,
        default_success: bool = True,
    ) -> None:
        self._command_runner = command_runner
        self._default_success = default_success
        self.task_overrides: Dict[str, VerificationResult] = {}

    def set_task_override(self, task_id: str, result: VerificationResult) -> None:
        """Sets an explicit verification override for a specific task."""
        self.task_overrides[task_id] = result

    def verify(
        self,
        task: Task,
        policy: Optional[VerificationPolicy] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        """
        Executes Tier 1 self-validation.
        """
        start_time = time.time()
        active_policy = policy or resolve_policy_for_task(task)

        if not active_policy.tier1_enabled:
            evidence = VerificationEvidence(
                task_id=task.task_id,
                tier=VerificationTier.TIER_1_SELF_TEST,
                status=VerificationStatus.SKIPPED,
                verifier_identity="tier1_self_test",
                details={"reason": "Tier 1 disabled in policy"},
            )
            return VerificationResult(
                task_id=task.task_id,
                passed=True,
                tier=VerificationTier.TIER_1_SELF_TEST,
                evidences=[evidence],
                summary="Tier 1 self-test skipped by policy",
            )

        # Check explicit overrides
        if task.task_id in self.task_overrides:
            return self.task_overrides[task.task_id]

        evidences: List[VerificationEvidence] = []
        all_passed = True
        failure_classification: Optional[FailureClassification] = None

        # Check for configured tier1 commands
        commands = active_policy.tier1_commands
        if not commands and "tier1_command" in task.metadata:
            commands = [task.metadata["tier1_command"]]

        cwd = task.workspace_path

        if commands:
            for cmd in commands:
                cmd_start = time.time()
                if self._command_runner:
                    exit_code, stdout, stderr = self._command_runner(cmd, cwd, active_policy.timeout)
                else:
                    try:
                        proc = subprocess.run(
                            cmd,
                            shell=True,
                            cwd=cwd,
                            capture_output=True,
                            text=True,
                            timeout=active_policy.timeout,
                        )
                        exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
                    except subprocess.TimeoutExpired:
                        exit_code, stdout, stderr = 124, "", f"Command timed out after {active_policy.timeout}s"
                    except Exception as e:
                        exit_code, stdout, stderr = 1, "", f"Command execution error: {str(e)}"

                cmd_duration = time.time() - cmd_start
                cmd_passed = (exit_code == 0)
                if not cmd_passed:
                    all_passed = False
                    failure_classification = FailureClassification.REPAIRABLE

                evidence = VerificationEvidence(
                    task_id=task.task_id,
                    tier=VerificationTier.TIER_1_SELF_TEST,
                    status=VerificationStatus.PASSED if cmd_passed else VerificationStatus.FAILED,
                    command=cmd,
                    exit_code=exit_code,
                    stdout_summary=stdout,
                    stderr_summary=stderr,
                    duration=cmd_duration,
                    verifier_identity="tier1_worker_self_test",
                    failure_classification=failure_classification if not cmd_passed else None,
                )
                evidences.append(evidence)
                if not cmd_passed:
                    break
        else:
            # Default self-test behavior (no explicit commands configured)
            evidence = VerificationEvidence(
                task_id=task.task_id,
                tier=VerificationTier.TIER_1_SELF_TEST,
                status=VerificationStatus.PASSED if self._default_success else VerificationStatus.FAILED,
                duration=time.time() - start_time,
                verifier_identity="tier1_worker_self_test",
                failure_classification=None if self._default_success else FailureClassification.REPAIRABLE,
                details={"mode": "default_self_test"},
            )
            evidences.append(evidence)
            all_passed = self._default_success

        return VerificationResult(
            task_id=task.task_id,
            passed=all_passed,
            tier=VerificationTier.TIER_1_SELF_TEST,
            evidences=evidences,
            failure_classification=failure_classification if not all_passed else None,
            summary="Tier 1 self-test passed" if all_passed else "Tier 1 self-test failed",
        )


class IndependentVerifier(Verifier):
    """
    Tier 2: Independent Component Verification Interface.
    Executed independently from the implementer.
    Inspects deliverable evidence and conducts independent validations.
    """

    @abstractmethod
    def verify_independent(
        self,
        task: Task,
        evidence_so_far: List[VerificationEvidence],
        policy: Optional[VerificationPolicy] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        """
        Conducts independent verification without trusting Tier 1 blindly.
        """
        raise NotImplementedError

    def verify(
        self,
        task: Task,
        policy: Optional[VerificationPolicy] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        return self.verify_independent(task, evidence_so_far=[], policy=policy, context=context)


class MockIndependentVerifier(IndependentVerifier):
    """
    Deterministic Mock Independent Verifier for Tier 2 testing and simulations.
    Supports independent pass/fail simulation, explicit task overrides,
    and proving that Tier 2 does not mirror Tier 1 state.
    """

    def __init__(
        self,
        default_success: bool = True,
        verifier_identity: str = "mock_independent_verifier",
    ) -> None:
        self.default_success = default_success
        self.verifier_identity = verifier_identity
        self.task_overrides: Dict[str, bool] = {}
        self.task_errors: Dict[str, str] = {}
        self.task_classifications: Dict[str, FailureClassification] = {}
        self.invocations: List[Dict[str, Any]] = []

    def set_task_override(
        self,
        task_id: str,
        success: bool,
        error_message: str = "",
        classification: Optional[FailureClassification] = None,
    ) -> None:
        """Overrides the independent verification outcome for a specific task."""
        self.task_overrides[task_id] = success
        if error_message:
            self.task_errors[task_id] = error_message
        if classification:
            self.task_classifications[task_id] = classification

    def verify_independent(
        self,
        task: Task,
        evidence_so_far: List[VerificationEvidence],
        policy: Optional[VerificationPolicy] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        start_time = time.time()
        task_id = task.task_id

        # Determine success independently
        passed = self.task_overrides.get(task_id, self.default_success)
        err = self.task_errors.get(task_id, "Independent check failed" if not passed else "")
        classification = self.task_classifications.get(
            task_id,
            FailureClassification.REPAIRABLE if not passed else None
        )

        evidence = VerificationEvidence(
            task_id=task_id,
            tier=VerificationTier.TIER_2_INDEPENDENT,
            status=VerificationStatus.PASSED if passed else VerificationStatus.FAILED,
            command="independent_component_check",
            exit_code=0 if passed else 1,
            stdout_summary="Independent checks verified contract." if passed else "",
            stderr_summary=err,
            duration=time.time() - start_time,
            verifier_identity=self.verifier_identity,
            failure_classification=classification,
            details={"tier1_evidence_count": len(evidence_so_far)},
        )

        self.invocations.append({
            "task_id": task_id,
            "passed": passed,
            "tier1_evidence_count": len(evidence_so_far),
            "timestamp": time.time(),
        })

        return VerificationResult(
            task_id=task_id,
            passed=passed,
            tier=VerificationTier.TIER_2_INDEPENDENT,
            evidences=[evidence],
            failure_classification=classification,
            summary="Tier 2 independent verification passed" if passed else f"Tier 2 failed: {err}",
        )


class Tier3AdversarialVerifier(Verifier):
    """
    Tier 3: Adversarial Challenger (Extensible Stub).
    Deferred to a future phase.
    """

    def verify(
        self,
        task: Task,
        policy: Optional[VerificationPolicy] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        raise NotImplementedError("Tier 3 Adversarial Challenge is deferred to a future phase.")


class Tier4VictoryAuditVerifier(Verifier):
    """
    Tier 4: Final Victory Audit (Extensible Stub).
    Deferred to a future phase.
    """

    def verify(
        self,
        task: Task,
        policy: Optional[VerificationPolicy] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        raise NotImplementedError("Tier 4 Victory Audit is deferred to a future phase.")
