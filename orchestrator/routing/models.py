"""
Adaptive Orchestrator v5 - Model Routing Models and Configuration.
Defines execution tiers, execution profiles, and routing policies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Set


class ModelTier(str, Enum):
    """
    Execution performance and reasoning tiers for model selection.
    """
    FAST = "FAST"
    PRO = "PRO"


@dataclass(frozen=True)
class ExecutionProfile:
    """
    Structured execution profile produced by the ModelRouter.
    Passed to the execution adapter to govern subagent model allocation.
    """
    tier: ModelTier
    model_id: str
    routing_reason: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier.value,
            "model_id": self.model_id,
            "routing_reason": self.routing_reason,
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
