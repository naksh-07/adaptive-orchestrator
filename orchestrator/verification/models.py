"""
Adaptive Orchestrator v5 - Verification & Evidence Data Models.
Compact, structured, deterministic evidence for verification and repair.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class VerificationTier(str, Enum):
    """
    Verification tiers corresponding to the v5 verification pyramid.
    """
    TIER_1_SELF_TEST = "TIER_1_SELF_TEST"
    TIER_2_INDEPENDENT = "TIER_2_INDEPENDENT"
    TIER_3_ADVERSARIAL = "TIER_3_ADVERSARIAL"
    TIER_4_VICTORY_AUDIT = "TIER_4_VICTORY_AUDIT"
    # Aliases
    TIER1_SELF_TEST = "TIER_1_SELF_TEST"
    TIER2_INDEPENDENT = "TIER_2_INDEPENDENT"
    TIER3_ADVERSARIAL = "TIER_3_ADVERSARIAL"
    TIER4_VICTORY_AUDIT = "TIER_4_VICTORY_AUDIT"


class VerificationStatus(str, Enum):
    """
    Status of an individual check or overall verification result.
    """
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class FailureClassification(str, Enum):
    """
    Classification of verification defects to guide the local repair loop.
    """
    REPAIRABLE = "REPAIRABLE"
    NON_REPAIRABLE = "NON_REPAIRABLE"
    ENVIRONMENTAL = "ENVIRONMENTAL"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    WORKSPACE_COLLISION = "WORKSPACE_COLLISION"
    LOGIC_ERROR = "LOGIC_ERROR"
    UNKNOWN = "UNKNOWN"

    @property
    def is_repairable(self) -> bool:
        """Indicates if this failure category is eligible for in-context worker repair."""
        return self in (
            FailureClassification.REPAIRABLE,
            FailureClassification.WORKSPACE_COLLISION,
            FailureClassification.LOGIC_ERROR,
        )


MAX_LOG_SUMMARY_CHARS: int = 500


def truncate_summary(text: Optional[str], max_chars: int = MAX_LOG_SUMMARY_CHARS) -> str:
    """
    Deterministically truncates verbose logs to preserve compact in-memory state.
    """
    if not text:
        return ""
    clean = text.strip()
    if len(clean) <= max_chars:
        return clean
    half = (max_chars - 30) // 2
    return f"{clean[:half]}\n... [truncated] ...\n{clean[-half:]}"


@dataclass
class VerificationEvidence:
    """
    Compact, deterministic record of an individual verification action.
    """
    task_id: str
    verification_id: str = field(default_factory=lambda: f"verif_{uuid.uuid4().hex[:8]}")
    tier: VerificationTier = VerificationTier.TIER_1_SELF_TEST
    status: VerificationStatus = VerificationStatus.PASSED
    command: Optional[str] = None
    exit_code: Optional[int] = None
    stdout_summary: str = ""
    stderr_summary: str = ""
    evidence_paths: List[str] = field(default_factory=list)
    duration: float = 0.0
    timestamp: float = field(default_factory=time.time)
    verifier_identity: str = "default_verifier"
    failure_classification: Optional[FailureClassification] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.stdout_summary = truncate_summary(self.stdout_summary)
        self.stderr_summary = truncate_summary(self.stderr_summary)

    @property
    def is_success(self) -> bool:
        return self.status == VerificationStatus.PASSED

    @property
    def summary(self) -> str:
        return self.stderr_summary or self.stdout_summary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "verification_id": self.verification_id,
            "tier": self.tier.value,
            "status": self.status.value,
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout_summary": self.stdout_summary,
            "stderr_summary": self.stderr_summary,
            "evidence_paths": list(self.evidence_paths),
            "duration": self.duration,
            "timestamp": self.timestamp,
            "verifier_identity": self.verifier_identity,
            "failure_classification": self.failure_classification.value if self.failure_classification else None,
            "details": dict(self.details),
        }


@dataclass
class VerificationResult:
    """
    Aggregated outcome of a task verification run across one or more tiers.
    """
    task_id: str = ""
    passed: bool = True
    tier: VerificationTier = VerificationTier.TIER_1_SELF_TEST
    evidences: List[VerificationEvidence] = field(default_factory=list)
    failure_classification: Optional[FailureClassification] = None
    summary: str = ""
    timestamp: float = field(default_factory=time.time)

    def __init__(
        self,
        task_id: str = "",
        passed: bool = True,
        tier: VerificationTier = VerificationTier.TIER_1_SELF_TEST,
        evidences: Optional[List[VerificationEvidence]] = None,
        failure_classification: Optional[FailureClassification] = None,
        summary: str = "",
        timestamp: Optional[float] = None,
        status: Optional[Any] = None,
        evidence: Optional[Any] = None,
        error_message: str = "",
        **kwargs: Any,
    ) -> None:
        self.task_id = task_id
        self.tier = tier
        self.failure_classification = failure_classification
        self.summary = summary or error_message
        self.timestamp = timestamp if timestamp is not None else time.time()
        self.evidences = list(evidences or [])
        if evidence is not None and not self.evidences:
            if isinstance(evidence, list):
                self.evidences = list(evidence)
            else:
                self.evidences = [evidence]

        if status is not None:
            st_val = getattr(status, "value", str(status))
            self.passed = (st_val == "PASSED")
        else:
            self.passed = passed

    @property
    def is_success(self) -> bool:
        return self.passed

    @property
    def status(self) -> VerificationStatus:
        return VerificationStatus.PASSED if self.passed else VerificationStatus.FAILED

    @property
    def evidence(self) -> Optional[VerificationEvidence]:
        return self.evidences[0] if self.evidences else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "passed": self.passed,
            "tier": self.tier.value,
            "evidences": [e.to_dict() for e in self.evidences],
            "failure_classification": self.failure_classification.value if self.failure_classification else None,
            "summary": self.summary,
            "timestamp": self.timestamp,
        }


@dataclass
class VictoryAuditResult:
    """
    Final mission-level victory audit outcome (Tier 4).
    Validates global requirements, merge safety, artifact delivery, and regression status.
    """
    mission_id: str
    passed: bool
    summary: str
    evidences: List[VerificationEvidence] = field(default_factory=list)
    criteria_results: Dict[str, bool] = field(default_factory=dict)
    unresolved_failures: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.passed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "passed": self.passed,
            "summary": self.summary,
            "evidences": [e.to_dict() for e in self.evidences],
            "criteria_results": dict(self.criteria_results),
            "unresolved_failures": list(self.unresolved_failures),
            "timestamp": self.timestamp,
            "evidence": dict(self.evidence),
        }
