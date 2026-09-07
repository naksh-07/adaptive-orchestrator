"""
Adaptive Orchestrator v5 - Verification Engine.
Coordinates Tier 1 self-tests, Tier 2 independent verifications, evidence collection,
event emission, and local in-context repair gating.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from orchestrator.models import Event, EventType, TaskState
from orchestrator.verification.models import (
    FailureClassification,
    VerificationEvidence,
    VerificationResult,
    VerificationTier,
)
from orchestrator.verification.policy import VerificationPolicy, resolve_policy_for_task
from orchestrator.verification.repair import RepairCoordinator, classify_failure
from orchestrator.verification.verifier import (
    IndependentVerifier,
    MockIndependentVerifier,
    Tier1SelfTestVerifier,
)

if TYPE_CHECKING:
    from orchestrator.models import Task
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
        repair_coordinator: Optional[RepairCoordinator] = None,
        event_emitter: Optional[Callable[[EventType, Optional[str], Optional[Dict[str, Any]]], Event]] = None,
    ) -> None:
        self._tier1_verifier = tier1_verifier or Tier1SelfTestVerifier()
        self._independent_verifier = independent_verifier or MockIndependentVerifier()
        self._repair_coordinator = repair_coordinator or RepairCoordinator()
        self._event_emitter = event_emitter

        # In-memory history ledger: task_id -> list of VerificationResult
        self._history: Dict[str, List[VerificationResult]] = {}

    @property
    def tier1_verifier(self) -> Tier1SelfTestVerifier:
        return self._tier1_verifier

    @property
    def independent_verifier(self) -> IndependentVerifier:
        return self._independent_verifier

    @property
    def repair_coordinator(self) -> RepairCoordinator:
        return self._repair_coordinator

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
    ) -> VerificationResult:
        """
        Runs configured verification tiers incrementally for a task.
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
                    "require_independent": active_policy.require_independent_verification,
                    "retry_count": task.retry_count,
                }
            )

        all_evidences: List[VerificationEvidence] = []

        # 2. Tier 1: Worker Self-Validation
        t1_result = self._tier1_verifier.verify(task, policy=active_policy, context=context)
        all_evidences.extend(t1_result.evidences)

        if not t1_result.passed:
            # Tier 1 failed
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
        )

        if should_run_tier2:
            t2_result = self._independent_verifier.verify_independent(
                task,
                evidence_so_far=all_evidences,
                policy=active_policy,
                context=context,
            )
            all_evidences.extend(t2_result.evidences)

            if not t2_result.passed:
                # Tier 2 failed
                classification = t2_result.failure_classification or classify_failure(
                    t2_result.evidences[0] if t2_result.evidences else None,
                    error_message=t2_result.summary
                )
                final_result = VerificationResult(
                    task_id=task_id,
                    passed=False,
                    tier=VerificationTier.TIER_2_INDEPENDENT,
                    evidences=all_evidences,
                    failure_classification=classification,
                    summary=f"Tier 2 independent verification failed: {t2_result.summary}",
                )
                self._record_result(final_result)
                self._emit_failure(task_id, final_result)
                return final_result

        # 4. All required tiers passed
        final_tier = (
            VerificationTier.TIER_2_INDEPENDENT
            if should_run_tier2
            else VerificationTier.TIER_1_SELF_TEST
        )
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
