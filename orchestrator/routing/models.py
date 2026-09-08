"""
Adaptive Orchestrator v5 - Model Routing Models and Configuration.
Defines execution tiers, execution profiles, and routing policies.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Set


class ModelTier(str, Enum):
    """
    Execution performance and reasoning tiers for model selection.
    """
    FAST = "FAST"
    PRO = "PRO"


@dataclass
class ExecutionProfile:
    """
    Structured execution profile produced by the ModelRouter.
    Passed to the execution adapter to govern subagent model allocation.
    """
    tier: ModelTier = ModelTier.FAST
    model_id: str = "model:flash"
    routing_reason: str = ""
    fast_candidate: bool = False
    risk_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        tier: ModelTier = ModelTier.FAST,
        model_id: Optional[str] = None,
        routing_reason: Optional[str] = None,
        reason: Optional[str] = None,
        fast_candidate: bool = False,
        risk_score: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        self.tier = tier
        eff_model = model_id or ("model:pro" if tier == ModelTier.PRO else "model:flash")
        self.model_id = eff_model
        self.routing_reason = routing_reason or reason or ""
        self.fast_candidate = fast_candidate
        self.risk_score = risk_score
        meta = dict(metadata or {})
        meta.update(kwargs)
        self.metadata = meta

    @property
    def reason(self) -> str:
        return self.routing_reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier.value if hasattr(self.tier, "value") else str(self.tier),
            "model_id": self.model_id,
            "routing_reason": self.routing_reason,
            "fast_candidate": self.fast_candidate,
            "risk_score": self.risk_score,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RouterConfig:
    """
    Configurable model routing policy parameters.
    No hard-coded vendor strings across the scheduler.
    """
    fast_model_id: str = "model: flash"
    pro_model_id: str = "model: pro"
    high_priority_threshold: float = 8.0
    max_retries_before_escalation: int = 1
    pro_domains: Set[str] = field(default_factory=lambda: {
        "architecture",
        "planning",
        "security",
        "audit",
        "adversarial",
    })
    pro_task_types: Set[str] = field(default_factory=lambda: {
        "PLANNING",
        "ARCHITECTURE",
        "ADVERSARIAL_CHALLENGE",
        "VICTORY_AUDIT",
    })

    def get_model_id_for_tier(self, tier: ModelTier) -> str:
        """Returns the configured model identifier for a given tier."""
        if tier == ModelTier.PRO:
            return self.pro_model_id
        return self.fast_model_id
