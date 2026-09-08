"""
Adaptive Orchestrator v5 - Verifier Interfaces & Implementations.
Tier 1 (Self-Test), Tier 2 (Independent Verification), and extensible tier stubs.
"""

from __future__ import annotations

import os
import subprocess
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from orchestrator.verification.models import (
    FailureClassification,
    VerificationEvidence,
    VerificationResult,
    VerificationStatus,
    VerificationTier,
    VictoryAuditResult,
)
from orchestrator.models import TaskState
from orchestrator.verification.policy import VerificationPolicy, resolve_policy_for_task

if TYPE_CHECKING:
    from orchestrator.models import Mission, Task


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
        execution_result: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
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
        execution_result: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
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
        execution_result: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
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
        execution_result: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> VerificationResult:
        try:
            return self.verify_independent(
                task,
                evidence_so_far=[],
                policy=policy,
                context=context,
                execution_result=execution_result,
                **kwargs,
            )
        except TypeError:
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
        execution_result: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
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
    Tier 3: Adversarial Challenger.
    Actively stress-tests and challenges task deliverables:
      - Validates write set boundaries (detects undeclared file modifications)
      - Executes adversarial stress/regression checks
      - Probes edge cases and boundary conditions
    Strictly non-mutating: produces evidence and challenge findings without altering source code.
    """

    def __init__(
        self,
        command_runner: Optional[Callable[[str, Optional[str], float], Tuple[int, str, str]]] = None,
        checker: Optional[Callable[[Task, Dict[str, Any]], Tuple[bool, str, Dict[str, Any]]]] = None,
        verifier_identity: str = "tier3_adversarial_challenger",
        default_success: bool = True,
    ) -> None:
        self._command_runner = command_runner
        self._checker = checker
        self.verifier_identity = verifier_identity
        self.default_success = default_success
        self.task_overrides: Dict[str, VerificationResult] = {}

    def set_task_override(self, task_id: str, result: VerificationResult) -> None:
        """Sets an explicit verification override for a specific task."""
        self.task_overrides[task_id] = result

    def verify(
        self,
        task: Task,
        policy: Optional[VerificationPolicy] = None,
        context: Optional[Dict[str, Any]] = None,
        execution_result: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        start_time = time.time()
        active_policy = policy or resolve_policy_for_task(task)

        # Check policy activation
        should_run = (
            active_policy.tier3_enabled
            or active_policy.require_adversarial_challenge
            or task.metadata.get("require_tier3", False)
            or execution_result is not None
        )

        if not should_run:
            evidence = VerificationEvidence(
                task_id=task.task_id,
                tier=VerificationTier.TIER_3_ADVERSARIAL,
                status=VerificationStatus.SKIPPED,
                verifier_identity=self.verifier_identity,
                details={"reason": "Tier 3 disabled in policy"},
            )
            return VerificationResult(
                task_id=task.task_id,
                passed=True,
                tier=VerificationTier.TIER_3_ADVERSARIAL,
                evidences=[evidence],
                summary="Tier 3 adversarial challenge skipped by policy",
            )

        if task.task_id in self.task_overrides:
            return self.task_overrides[task.task_id]

        evidences: List[VerificationEvidence] = []
        all_passed = True
        failure_classification: Optional[FailureClassification] = None
        failure_reasons: List[str] = []

        # 1. Write Contract Exclusivity Check
        # Check whether the task modified files outside its declared write set
        modified_files = set(task.metadata.get("modified_files", []))
        if not modified_files and execution_result and "modified_files" in execution_result:
            modified_files = set(execution_result["modified_files"])
        elif not modified_files and context:
            if "modified_files" in context:
                modified_files = set(context["modified_files"])
            elif "execution_result" in context and "modified_files" in context["execution_result"]:
                modified_files = set(context["execution_result"]["modified_files"])

        if modified_files and task.write_set:
            forbidden_modifications = modified_files - task.write_set
            if forbidden_modifications:
                all_passed = False
                failure_classification = FailureClassification.WORKSPACE_COLLISION
                msg = f"Contract violation: modified undeclared files: {sorted(list(forbidden_modifications))}"
                failure_reasons.append(msg)
                evidences.append(
                    VerificationEvidence(
                        task_id=task.task_id,
                        tier=VerificationTier.TIER_3_ADVERSARIAL,
                        status=VerificationStatus.FAILED,
                        command="write_contract_check",
                        exit_code=1,
                        stderr_summary=msg,
                        verifier_identity=self.verifier_identity,
                        failure_classification=FailureClassification.WORKSPACE_COLLISION,
                        details={"undeclared_writes": sorted(list(forbidden_modifications))},
                    )
                )

        # 2. Custom Adversarial Commands
        commands = list(active_policy.tier3_commands)
        if not commands and "tier3_command" in task.metadata:
            commands = [task.metadata["tier3_command"]]

        cwd = task.workspace_path
        if commands and all_passed:
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
                    failure_reasons.append(f"Adversarial command '{cmd}' failed: {stderr or stdout}")

                evidences.append(
                    VerificationEvidence(
                        task_id=task.task_id,
                        tier=VerificationTier.TIER_3_ADVERSARIAL,
                        status=VerificationStatus.PASSED if cmd_passed else VerificationStatus.FAILED,
                        command=cmd,
                        exit_code=exit_code,
                        stdout_summary=stdout,
                        stderr_summary=stderr,
                        duration=cmd_duration,
                        verifier_identity=self.verifier_identity,
                        failure_classification=failure_classification if not cmd_passed else None,
                    )
                )
                if not cmd_passed:
                    break

        # 3. Custom Callable Checker
        if self._checker and all_passed:
            chk_passed, chk_msg, chk_details = self._checker(task, context or {})
            if not chk_passed:
                all_passed = False
                failure_classification = FailureClassification.REPAIRABLE
                failure_reasons.append(f"Adversarial check failed: {chk_msg}")

            evidences.append(
                VerificationEvidence(
                    task_id=task.task_id,
                    tier=VerificationTier.TIER_3_ADVERSARIAL,
                    status=VerificationStatus.PASSED if chk_passed else VerificationStatus.FAILED,
                    command="adversarial_checker",
                    exit_code=0 if chk_passed else 1,
                    stdout_summary=chk_msg if chk_passed else "",
                    stderr_summary="" if chk_passed else chk_msg,
                    duration=time.time() - start_time,
                    verifier_identity=self.verifier_identity,
                    failure_classification=failure_classification if not chk_passed else None,
                    details=chk_details,
                )
            )

        # Fallback default check if no commands or checks were provided
        if not evidences:
            evidences.append(
                VerificationEvidence(
                    task_id=task.task_id,
                    tier=VerificationTier.TIER_3_ADVERSARIAL,
                    status=VerificationStatus.PASSED if self.default_success else VerificationStatus.FAILED,
                    command="default_adversarial_check",
                    exit_code=0 if self.default_success else 1,
                    stdout_summary="Adversarial checks passed." if self.default_success else "",
                    stderr_summary="" if self.default_success else "Default adversarial check failed.",
                    duration=time.time() - start_time,
                    verifier_identity=self.verifier_identity,
                    failure_classification=None if self.default_success else FailureClassification.REPAIRABLE,
                )
            )
            all_passed = self.default_success

        summary = (
            "Adversarial checks passed"
            if all_passed
            else f"Tier 3 adversarial challenge failed: {'; '.join(failure_reasons)}"
        )

        return VerificationResult(
            task_id=task.task_id,
            passed=all_passed,
            tier=VerificationTier.TIER_3_ADVERSARIAL,
            evidences=evidences,
            failure_classification=failure_classification if not all_passed else None,
            summary=summary,
        )


class MockAdversarialVerifier(Tier3AdversarialVerifier):
    """
    Deterministic Mock Adversarial Verifier for testing and benchmark simulations.
    """

    def __init__(
        self,
        default_success: bool = True,
        verifier_identity: str = "mock_adversarial_verifier",
        should_pass: Optional[bool] = None,
        failure_reason: str = "",
    ) -> None:
        if should_pass is not None:
            default_success = should_pass
        super().__init__(default_success=default_success, verifier_identity=verifier_identity)
        self.failure_reason = failure_reason
        self.invocations: List[Dict[str, Any]] = []

    def verify(
        self,
        task: Task,
        policy: Optional[VerificationPolicy] = None,
        context: Optional[Dict[str, Any]] = None,
        execution_result: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        self.invocations.append({"task_id": task.task_id, "execution_result": execution_result})
        if not self.default_success and self.failure_reason and task.task_id not in self.task_overrides:
            ev = VerificationEvidence(
                task_id=task.task_id,
                tier=VerificationTier.TIER_3_ADVERSARIAL,
                status=VerificationStatus.FAILED,
                command="mock_adversarial_probe",
                exit_code=1,
                stderr_summary=self.failure_reason,
                verifier_identity=self.verifier_identity,
                failure_classification=FailureClassification.LOGIC_ERROR,
            )
            return VerificationResult(
                task_id=task.task_id,
                passed=False,
                tier=VerificationTier.TIER_3_ADVERSARIAL,
                evidences=[ev],
                failure_classification=FailureClassification.LOGIC_ERROR,
                summary=f"Adversarial check failed: {self.failure_reason}",
            )
        return super().verify(task, policy=policy, context=context, execution_result=execution_result)

    def set_task_outcome(
        self,
        task_id: str,
        success: bool,
        error_message: str = "",
        classification: Optional[FailureClassification] = None,
    ) -> None:
        evidence = VerificationEvidence(
            task_id=task_id,
            tier=VerificationTier.TIER_3_ADVERSARIAL,
            status=VerificationStatus.PASSED if success else VerificationStatus.FAILED,
            command="mock_adversarial_probe",
            exit_code=0 if success else 1,
            stdout_summary="Mock adversarial check passed." if success else "",
            stderr_summary=error_message if not success else "",
            verifier_identity=self.verifier_identity,
            failure_classification=classification or (FailureClassification.REPAIRABLE if not success else None),
        )
        self.set_task_override(
            task_id,
            VerificationResult(
                task_id=task_id,
                passed=success,
                tier=VerificationTier.TIER_3_ADVERSARIAL,
                evidences=[evidence],
                failure_classification=classification or (FailureClassification.REPAIRABLE if not success else None),
                summary="Mock adversarial passed" if success else f"Mock adversarial failed: {error_message}",
            )
        )


class Tier4VictoryAuditVerifier(Verifier):
    """
    Tier 4: Mission-Level Victory Audit Engine.
    Evaluates whole-mission acceptance criteria:
      1. All tasks completed in valid terminal states (PASSED / MERGED)
      2. Zero unresolved task failures or blocking errors
      3. Verification evidence present across deliverables
      4. Merges completed (if worktree isolation was used)
      5. Required deliverable artifacts exist
      6. Acceptance criteria verified
    Strictly non-mutating: produces an acceptance decision and audit evidence.
    """

    def __init__(
        self,
        test_runner: Optional[Callable[[], Tuple[int, str, str]]] = None,
        artifact_checker: Optional[Callable[[str], bool]] = None,
        default_success: bool = True,
        verifier_identity: str = "tier4_victory_auditor",
        required_artifacts: Optional[List[str]] = None,
        acceptance_criteria: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._test_runner = test_runner
        self._artifact_checker = artifact_checker or os.path.exists
        self.default_success = default_success
        self.verifier_identity = verifier_identity
        self.required_artifacts = list(required_artifacts or [])
        self.acceptance_criteria = dict(acceptance_criteria or {})

    def verify(
        self,
        task: Task,
        policy: Optional[VerificationPolicy] = None,
        context: Optional[Dict[str, Any]] = None,
        execution_result: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> VerificationResult:
        """
        Single-task verifier interface compliance.
        For mission-wide audit, use audit_mission.
        """
        evidence = VerificationEvidence(
            task_id=task.task_id,
            tier=VerificationTier.TIER_4_VICTORY_AUDIT,
            status=VerificationStatus.PASSED if self.default_success else VerificationStatus.FAILED,
            command="victory_audit_task_check",
            exit_code=0 if self.default_success else 1,
            verifier_identity=self.verifier_identity,
        )
        return VerificationResult(
            task_id=task.task_id,
            passed=self.default_success,
            tier=VerificationTier.TIER_4_VICTORY_AUDIT,
            evidences=[evidence],
            summary="Task victory check passed" if self.default_success else "Task victory check failed",
        )

    def audit_mission(
        self,
        mission_or_engine: Any = None,
        tasks: Optional[List[Task]] = None,
        required_artifacts: Optional[List[str]] = None,
        acceptance_criteria: Optional[Dict[str, Any]] = None,
        verification_history: Optional[Dict[str, List[VerificationResult]]] = None,
        mission: Any = None,
    ) -> VictoryAuditResult:
        """
        Conducts authoritative mission-level Victory Audit.
        Accepts either a Mission object + tasks list or a MissionEngine instance directly.
        """
        target = mission if mission is not None else mission_or_engine
        if hasattr(target, "mission") and hasattr(target, "graph"):
            actual_mission = target.mission
            tasks = tasks if tasks is not None else target.graph.all_tasks()
        else:
            actual_mission = target
            tasks = tasks if tasks is not None else []

        effective_artifacts = required_artifacts if required_artifacts is not None else self.required_artifacts
        effective_criteria = acceptance_criteria if acceptance_criteria is not None else self.acceptance_criteria

        start_time = time.time()
        evidences: List[VerificationEvidence] = []
        criteria_results: Dict[str, bool] = {}
        unresolved_failures: List[str] = []
        all_passed = True

        # 1. Task Terminal State & Zero Unresolved Failures Check
        if not tasks:
            all_passed = False
            unresolved_failures.append("Mission has zero tasks in graph.")
            criteria_results["non_empty_mission"] = False
        else:
            criteria_results["non_empty_mission"] = True

        failed_tasks = [
            f"Task {t.task_id} in state {t.status.value}"
            for t in tasks
            if not (t.status.is_terminal and t.status.is_success)
        ]
        if failed_tasks:
            all_passed = False
            for ft in failed_tasks:
                unresolved_failures.append(ft)
            criteria_results["all_tasks_passed"] = False
            evidences.append(
                VerificationEvidence(
                    task_id="mission",
                    tier=VerificationTier.TIER_4_VICTORY_AUDIT,
                    status=VerificationStatus.FAILED,
                    command="task_completion_check",
                    exit_code=1,
                    stderr_summary="; ".join(failed_tasks),
                    verifier_identity=self.verifier_identity,
                )
            )
        else:
            criteria_results["all_tasks_passed"] = True

        # 2. Verification Evidence Presence Check
        if verification_history is not None and tasks:
            missing_evidence = [t.task_id for t in tasks if not verification_history.get(t.task_id)]
            if missing_evidence:
                all_passed = False
                err = f"Tasks missing verification evidence: {missing_evidence}"
                unresolved_failures.append(err)
                criteria_results["verification_evidence_complete"] = False
            else:
                criteria_results["verification_evidence_complete"] = True
        else:
            criteria_results["verification_evidence_complete"] = True

        # 3. Worktree Integration / Merge Status Check
        unmerged_branch_tasks = [
            t.task_id for t in tasks
            if t.workspace_mode == "branch" and t.write_set and t.status != TaskState.MERGED
        ]
        if unmerged_branch_tasks:
            all_passed = False
            err = f"Worktree tasks modified files but were not merged into integration: {unmerged_branch_tasks}"
            unresolved_failures.append(err)
            criteria_results["all_writes_merged"] = False
        else:
            criteria_results["all_writes_merged"] = True

        evidence_dict: Dict[str, Any] = {}

        # 4. Required Deliverable Artifacts Check
        if effective_artifacts:
            missing_artifacts = [
                path for path in effective_artifacts
                if not self._artifact_checker(path)
            ]
            if missing_artifacts:
                all_passed = False
                err = f"Missing required artifact(s): {missing_artifacts}"
                unresolved_failures.append(err)
                criteria_results["required_artifacts_exist"] = False
                evidence_dict["artifacts"] = f"Missing required artifact: {missing_artifacts}"
            else:
                criteria_results["required_artifacts_exist"] = True
                evidence_dict["artifacts"] = "All required artifacts present."
        else:
            criteria_results["required_artifacts_exist"] = True
            evidence_dict["artifacts"] = "No required artifacts specified."

        # 5. Global Regression Test Runner Check
        if self._test_runner and all_passed:
            exit_code, stdout, stderr = self._test_runner()
            if exit_code != 0:
                all_passed = False
                err = f"Final mission test runner failed (exit code {exit_code}): {stderr or stdout}"
                unresolved_failures.append(err)
                criteria_results["global_test_suite"] = False
                evidence_dict["tests"] = err
                evidences.append(
                    VerificationEvidence(
                        task_id="mission",
                        tier=VerificationTier.TIER_4_VICTORY_AUDIT,
                        status=VerificationStatus.FAILED,
                        command="global_test_runner",
                        exit_code=exit_code,
                        stdout_summary=stdout,
                        stderr_summary=stderr,
                        verifier_identity=self.verifier_identity,
                    )
                )
            else:
                criteria_results["global_test_suite"] = True
                evidence_dict["tests"] = "Global test suite passed."
                evidences.append(
                    VerificationEvidence(
                        task_id="mission",
                        tier=VerificationTier.TIER_4_VICTORY_AUDIT,
                        status=VerificationStatus.PASSED,
                        command="global_test_runner",
                        exit_code=0,
                        stdout_summary=stdout,
                        verifier_identity=self.verifier_identity,
                    )
                )
        else:
            criteria_results["global_test_suite"] = True

        # 6. Explicit Acceptance Criteria
        if effective_criteria:
            for crit_name, expected_val in effective_criteria.items():
                actual_val = actual_mission.metadata.get(crit_name)
                crit_pass = (actual_val == expected_val)
                criteria_results[crit_name] = crit_pass
                if not crit_pass:
                    all_passed = False
                    unresolved_failures.append(
                        f"Acceptance criterion '{crit_name}' unsatisfied: expected {expected_val}, got {actual_val}"
                    )
            evidence_dict["criteria"] = criteria_results

        # Build final evidence
        evidences.append(
            VerificationEvidence(
                task_id="mission",
                tier=VerificationTier.TIER_4_VICTORY_AUDIT,
                status=VerificationStatus.PASSED if all_passed else VerificationStatus.FAILED,
                command="tier4_victory_audit",
                exit_code=0 if all_passed else 1,
                stdout_summary="Mission satisfies all acceptance criteria." if all_passed else "",
                stderr_summary="; ".join(unresolved_failures) if not all_passed else "",
                duration=time.time() - start_time,
                verifier_identity=self.verifier_identity,
            )
        )

        summary = (
            "Tier 4 Victory Audit PASSED: All mission objectives and criteria satisfied."
            if all_passed
            else f"Tier 4 Victory Audit FAILED: {'; '.join(unresolved_failures)}"
        )

        mid = getattr(actual_mission, "mission_id", "mission")
        return VictoryAuditResult(
            mission_id=mid,
            passed=all_passed,
            summary=summary,
            evidences=evidences,
            criteria_results=criteria_results,
            unresolved_failures=unresolved_failures,
            evidence=evidence_dict,
        )


class MockVictoryAuditVerifier(Tier4VictoryAuditVerifier):
    """
    Deterministic Mock Victory Audit Verifier for simulation and failure testing.
    """

    def __init__(
        self,
        override_success: Optional[bool] = None,
        override_summary: str = "",
        verifier_identity: str = "mock_victory_auditor",
        required_artifacts: Optional[List[str]] = None,
        acceptance_criteria: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            verifier_identity=verifier_identity,
            required_artifacts=required_artifacts,
            acceptance_criteria=acceptance_criteria,
        )
        self.override_success = override_success
        self.override_summary = override_summary

    def audit_mission(
        self,
        mission_or_engine: Any = None,
        tasks: Optional[List[Task]] = None,
        required_artifacts: Optional[List[str]] = None,
        acceptance_criteria: Optional[Dict[str, Any]] = None,
        verification_history: Optional[Dict[str, List[VerificationResult]]] = None,
        mission: Any = None,
    ) -> VictoryAuditResult:
        target = mission if mission is not None else mission_or_engine
        if hasattr(target, "mission") and hasattr(target, "graph"):
            actual_mission = target.mission
            tasks = tasks if tasks is not None else target.graph.all_tasks()
        else:
            actual_mission = target
            tasks = tasks if tasks is not None else []

        mid = getattr(actual_mission, "mission_id", "mock_mission")

        if self.override_success is not None:
            passed = self.override_success
            summary = self.override_summary or ("Mock audit passed" if passed else "Mock audit failed")
            evidence = VerificationEvidence(
                task_id="mission",
                tier=VerificationTier.TIER_4_VICTORY_AUDIT,
                status=VerificationStatus.PASSED if passed else VerificationStatus.FAILED,
                command="mock_victory_audit",
                exit_code=0 if passed else 1,
                stderr_summary="" if passed else summary,
                verifier_identity=self.verifier_identity,
            )
            return VictoryAuditResult(
                mission_id=mid,
                passed=passed,
                summary=summary,
                evidences=[evidence],
                criteria_results={"mock_override": passed},
                unresolved_failures=[] if passed else [summary],
                evidence={"mock": summary},
            )
        return super().audit_mission(
            mission_or_engine=actual_mission,
            tasks=tasks,
            required_artifacts=required_artifacts,
            acceptance_criteria=acceptance_criteria,
            verification_history=verification_history,
        )
