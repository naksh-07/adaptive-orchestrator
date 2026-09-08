"""
Adaptive Orchestrator v5 - Verification Engine.
Coordinates Tier 1 self-tests, Tier 2 independent verifications, evidence collection,
event emission, and local in-context repair gating.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from orchestrator.models import Event, EventEmitter, EventType, MissionState, TaskState
from orchestrator.verification.models import (
    FailureClassification,
    VerificationEvidence,
    VerificationResult,
    VerificationTier,
    VictoryAuditResult,
)
from orchestrator.verification.policy import VerificationPolicy, resolve_policy_for_task
from orchestrator.verification.repair import RepairCoordinator, classify_failure
from orchestrator.verification.verifier import (
    IndependentVerifier,
    MockIndependentVerifier,
    Tier1SelfTestVerifier,
    Tier3AdversarialVerifier,
    Tier4VictoryAuditVerifier,
)

if TYPE_CHECKING:
    from orchestrator.models import Mission, Task
    from orchestrator.workers.adapter import ExecutionAdapter
    from orchestrator.workers.models import Worker


class VerificationEngine:
    """
    Deterministic Verification Engine.
    Coordinates verification progression:
      Tier 1 (Self-Test) -> Tier 2 (Independent) -> Pass / Fail -> Local In-Context Repair.
    Strictly non-mutating.
    """

    def __init__(
        self,
        tier1_verifier: Optional[Tier1SelfTestVerifier] = None,
        independent_verifier: Optional[IndependentVerifier] = None,
        tier3_verifier: Optional[Tier3AdversarialVerifier] = None,
        tier4_verifier: Optional[Tier4VictoryAuditVerifier] = None,
        repair_coordinator: Optional[RepairCoordinator] = None,
        event_emitter: Optional[EventEmitter] = None,
    ) -> None:
        self._tier1_verifier = tier1_verifier or Tier1SelfTestVerifier()
        self._independent_verifier = independent_verifier or MockIndependentVerifier()
        self._tier3_verifier = tier3_verifier or Tier3AdversarialVerifier()
        self._tier4_verifier = tier4_verifier or Tier4VictoryAuditVerifier()
        self._repair_coordinator = repair_coordinator or RepairCoordinator()
        self._event_emitter: Optional[EventEmitter] = event_emitter

        # In-memory history ledger: task_id -> list of VerificationResult
        self._history: Dict[str, List[VerificationResult]] = {}

    @property
    def tier1_verifier(self) -> Tier1SelfTestVerifier:
        return self._tier1_verifier

    @property
    def independent_verifier(self) -> IndependentVerifier:
        return self._independent_verifier

    @property
    def tier3_verifier(self) -> Tier3AdversarialVerifier:
        return self._tier3_verifier

    @property
    def tier4_verifier(self) -> Tier4VictoryAuditVerifier:
        return self._tier4_verifier

    @property
    def repair_coordinator(self) -> RepairCoordinator:
        return self._repair_coordinator

    @property
    def tier2_verifier(self) -> Any:
        return self._independent_verifier

    @tier2_verifier.setter
    def tier2_verifier(self, verifier: Any) -> None:
        self._independent_verifier = verifier

    def request_repair(
        self,
        task: Task,
        verification_result: VerificationResult,
        policy: Optional[VerificationPolicy] = None,
    ) -> Optional[Any]:
        """
        Coordinates in-context repair request for a task.
        """
        payload = self._repair_coordinator.build_repair_payload(task, verification_result)
        if payload and not payload.worker_id and getattr(task, "assigned_worker_id", None):
            payload.worker_id = task.assigned_worker_id
        if self._event_emitter and payload:
            self._event_emitter(
                EventType.REPAIR_REQUESTED,
                task_id=task.task_id,
                payload={
                    "worker_id": payload.worker_id,
                    "attempt": payload.repair_attempt,
                    "classification": payload.failure_classification.value,
                }
            )
        return payload

    def get_task_history(self, task_id: str) -> List[VerificationResult]:
        """Returns all historical verification results for a task."""
        return list(self._history.get(task_id, []))

    def get_latest_result(self, task_id: str) -> Optional[VerificationResult]:
        """Returns the most recent verification result for a task."""
        hist = self._history.get(task_id, [])
        return hist[-1] if hist else None

    def verify_task(
        self,
        task: Task,
        policy: Optional[VerificationPolicy] = None,
        context: Optional[Dict[str, Any]] = None,
        execution_result: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        """
        Runs configured verification tiers incrementally for a task.
        Tier 1 (Self-Test) -> Tier 2 (Independent) -> Tier 3 (Adversarial).
        Emits VERIFICATION_STARTED, and then VERIFICATION_PASSED or VERIFICATION_FAILED.
        """
        task_id = task.task_id
        active_policy = policy or resolve_policy_for_task(task)

        # 1. Emit VERIFICATION_STARTED
        if self._event_emitter:
            self._event_emitter(
                EventType.VERIFICATION_STARTED,
                task_id=task_id,
                payload={
                    "tier1_enabled": active_policy.tier1_enabled,
                    "tier2_enabled": active_policy.tier2_enabled,
                    "tier3_enabled": active_policy.tier3_enabled,
                    "require_independent": active_policy.require_independent_verification,
                    "require_adversarial": active_policy.require_adversarial_challenge,
                    "retry_count": task.retry_count,
                }
            )

        all_evidences: List[VerificationEvidence] = []

        # 2. Tier 1: Worker Self-Validation
        t1_result = self._tier1_verifier.verify(task, policy=active_policy, context=context)
        all_evidences.extend(t1_result.evidences)

        if not t1_result.passed:
            classification = t1_result.failure_classification or classify_failure(
                t1_result.evidences[0] if t1_result.evidences else None,
                error_message=t1_result.summary
            )
            final_result = VerificationResult(
                task_id=task_id,
                passed=False,
                tier=VerificationTier.TIER_1_SELF_TEST,
                evidences=all_evidences,
                failure_classification=classification,
                summary=f"Tier 1 self-test failed: {t1_result.summary}",
            )
            self._record_result(final_result)
            self._emit_failure(task_id, final_result)
            return final_result

        # 3. Tier 2: Independent Verification (if required/enabled)
        should_run_tier2 = (
            active_policy.tier2_enabled
            or active_policy.require_independent_verification
            or (self._independent_verifier is not None and type(self._independent_verifier).__name__ != "MockIndependentVerifier")
        )

        if should_run_tier2:
            if hasattr(self._independent_verifier, "verify_independent"):
                try:
                    t2_result = self._independent_verifier.verify_independent(
                        task,
                        evidence_so_far=all_evidences,
                        policy=active_policy,
                        context=context,
                        execution_result=execution_result,
                    )
                except TypeError:
                    t2_result = self._independent_verifier.verify(task, execution_result=execution_result)
            else:
                try:
                    t2_result = self._independent_verifier.verify(task, execution_result=execution_result)
                except TypeError:
                    t2_result = self._independent_verifier.verify(task)

            all_evidences.extend(t2_result.evidences if getattr(t2_result, "evidences", None) else [])

            if not t2_result.passed:
                classification = t2_result.failure_classification or classify_failure(
                    t2_result.evidences[0] if getattr(t2_result, "evidences", None) else None,
                    error_message=getattr(t2_result, "summary", "") or getattr(t2_result, "error_message", "")
                )
                err_msg = getattr(t2_result, "summary", "") or getattr(t2_result, "error_message", "")
                final_result = VerificationResult(
                    task_id=task_id,
                    passed=False,
                    tier=VerificationTier.TIER_2_INDEPENDENT,
                    evidences=all_evidences,
                    failure_classification=classification,
                    summary=f"Tier 2 independent verification failed: {err_msg}",
                )
                self._record_result(final_result)
                self._emit_failure(task_id, final_result)
                return final_result

        # 4. Tier 3: Adversarial Challenge (if required/enabled)
        should_run_tier3 = (
            active_policy.tier3_enabled
            or active_policy.require_adversarial_challenge
            or task.metadata.get("require_tier3", False)
        )

        if should_run_tier3:
            if self._event_emitter:
                self._event_emitter(
                    EventType.TIER3_CHALLENGE_STARTED,
                    task_id=task_id,
                    payload={"tier": VerificationTier.TIER_3_ADVERSARIAL.value}
                )

            t3_result = self._tier3_verifier.verify(task, policy=active_policy, context=context)
            all_evidences.extend(t3_result.evidences)

            if not t3_result.passed:
                classification = t3_result.failure_classification or classify_failure(
                    t3_result.evidences[0] if t3_result.evidences else None,
                    error_message=t3_result.summary
                )
                final_result = VerificationResult(
                    task_id=task_id,
                    passed=False,
                    tier=VerificationTier.TIER_3_ADVERSARIAL,
                    evidences=all_evidences,
                    failure_classification=classification,
                    summary=f"Tier 3 adversarial challenge failed: {t3_result.summary}",
                )
                self._record_result(final_result)
                if self._event_emitter:
                    self._event_emitter(
                        EventType.TIER3_CHALLENGE_FAILED,
                        task_id=task_id,
                        payload={"summary": t3_result.summary}
                    )
                self._emit_failure(task_id, final_result)
                return final_result
            else:
                if self._event_emitter:
                    self._event_emitter(
                        EventType.TIER3_CHALLENGE_PASSED,
                        task_id=task_id,
                        payload={"summary": t3_result.summary}
                    )

        # 5. All required tiers passed
        if should_run_tier3:
            final_tier = VerificationTier.TIER_3_ADVERSARIAL
        elif should_run_tier2:
            final_tier = VerificationTier.TIER_2_INDEPENDENT
        else:
            final_tier = VerificationTier.TIER_1_SELF_TEST

        final_result = VerificationResult(
            task_id=task_id,
            passed=True,
            tier=final_tier,
            evidences=all_evidences,
            failure_classification=None,
            summary="All required verification tiers passed successfully.",
        )
        self._record_result(final_result)

        if self._event_emitter:
            self._event_emitter(
                EventType.VERIFICATION_PASSED,
                task_id=task_id,
                payload={
                    "tier": final_tier.value,
                    "evidence_count": len(all_evidences),
                    "summary": final_result.summary,
                }
            )

        return final_result

    def run_victory_audit(
        self,
        mission: Mission,
        tasks: List[Task],
        required_artifacts: Optional[List[str]] = None,
        acceptance_criteria: Optional[Dict[str, Any]] = None,
    ) -> VictoryAuditResult:
        """
        Executes Tier 4 mission-level Victory Audit.
        Evaluates task deliverables, artifact presence, merge completeness, and criteria.
        Emits TIER4_AUDIT_STARTED and TIER4_AUDIT_PASSED / TIER4_AUDIT_FAILED.
        """
        if self._event_emitter:
            self._event_emitter(
                EventType.TIER4_AUDIT_STARTED,
                payload={
                    "mission_id": mission.mission_id,
                    "task_count": len(tasks),
                    "required_artifacts": required_artifacts or [],
                }
            )

        audit_result = self._tier4_verifier.audit_mission(
            mission=mission,
            tasks=tasks,
            required_artifacts=required_artifacts,
            acceptance_criteria=acceptance_criteria,
            verification_history=self._history,
        )

        if self._event_emitter:
            if audit_result.passed:
                self._event_emitter(
                    EventType.TIER4_AUDIT_PASSED,
                    payload={
                        "mission_id": mission.mission_id,
                        "summary": audit_result.summary,
                        "criteria_results": audit_result.criteria_results,
                    }
                )
            else:
                self._event_emitter(
                    EventType.TIER4_AUDIT_FAILED,
                    payload={
                        "mission_id": mission.mission_id,
                        "summary": audit_result.summary,
                        "unresolved_failures": audit_result.unresolved_failures,
                    }
                )

        return audit_result

    def _record_result(self, result: VerificationResult) -> None:
        """Appends result to task history ledger."""
        if result.task_id not in self._history:
            self._history[result.task_id] = []
        self._history[result.task_id].append(result)

    def _emit_failure(self, task_id: str, result: VerificationResult) -> None:
        """Emits VERIFICATION_FAILED event."""
        if self._event_emitter:
            self._event_emitter(
                EventType.VERIFICATION_FAILED,
                task_id=task_id,
                payload={
                    "tier": result.tier.value,
                    "failure_classification": result.failure_classification.value if result.failure_classification else None,
                    "summary": result.summary,
                    "evidence_count": len(result.evidences),
                }
            )
