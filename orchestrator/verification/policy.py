"""
Adaptive Orchestrator v5 - Verification Policy.
Configures verification gates, tier activations, command checks, and repair limits per task.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from orchestrator.models import Task


@dataclass
class VerificationPolicy:
    """
    Verification policy dictating what checks to run and how to gate integration.
    """
    tier1_enabled: bool = True
    tier2_enabled: bool = False
    tier3_enabled: bool = False
    tier4_enabled: bool = True
    tier1_commands: List[str] = field(default_factory=list)
    tier2_commands: List[str] = field(default_factory=list)
    tier3_commands: List[str] = field(default_factory=list)
    timeout: float = 30.0
    require_independent_verification: bool = False
    require_adversarial_challenge: bool = False
    allow_merge_after_tier1: bool = True
    auto_repair: bool = True
    max_repair_attempts: int = 2
    custom_checks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier1_enabled": self.tier1_enabled,
            "tier2_enabled": self.tier2_enabled,
            "tier3_enabled": self.tier3_enabled,
            "tier4_enabled": self.tier4_enabled,
            "tier1_commands": list(self.tier1_commands),
            "tier2_commands": list(self.tier2_commands),
            "tier3_commands": list(self.tier3_commands),
            "timeout": self.timeout,
            "require_independent_verification": self.require_independent_verification,
            "require_adversarial_challenge": self.require_adversarial_challenge,
            "allow_merge_after_tier1": self.allow_merge_after_tier1,
            "auto_repair": self.auto_repair,
            "max_repair_attempts": self.max_repair_attempts,
            "custom_checks": list(self.custom_checks),
        }


def resolve_policy_for_task(
    task: Task,
    default_policy: Optional[VerificationPolicy] = None,
) -> VerificationPolicy:
    """
    Resolves the applicable VerificationPolicy for a task based on its metadata,
    domain, risk level, and access contract.
    """
    base = default_policy or VerificationPolicy()

    # Check for direct policy in metadata
    meta = task.metadata or {}
    policy_meta = meta.get("verification_policy")

    if isinstance(policy_meta, VerificationPolicy):
        return policy_meta
    elif isinstance(policy_meta, dict):
        return VerificationPolicy(
            tier1_enabled=policy_meta.get("tier1_enabled", base.tier1_enabled),
            tier2_enabled=policy_meta.get("tier2_enabled", base.tier2_enabled),
            tier3_enabled=policy_meta.get("tier3_enabled", base.tier3_enabled),
            tier4_enabled=policy_meta.get("tier4_enabled", base.tier4_enabled),
            tier1_commands=policy_meta.get("tier1_commands", list(base.tier1_commands)),
            tier2_commands=policy_meta.get("tier2_commands", list(base.tier2_commands)),
            tier3_commands=policy_meta.get("tier3_commands", list(base.tier3_commands)),
            timeout=policy_meta.get("timeout", base.timeout),
            require_independent_verification=policy_meta.get(
                "require_independent_verification",
                base.require_independent_verification
            ),
            require_adversarial_challenge=policy_meta.get(
                "require_adversarial_challenge",
                base.require_adversarial_challenge
            ),
            allow_merge_after_tier1=policy_meta.get(
                "allow_merge_after_tier1",
                base.allow_merge_after_tier1
            ),
            auto_repair=policy_meta.get("auto_repair", base.auto_repair),
            max_repair_attempts=policy_meta.get("max_repair_attempts", task.max_retries),
            custom_checks=policy_meta.get("custom_checks", list(base.custom_checks)),
        )

    # Resolve from task flags
    tier1_enabled = meta.get("tier1_enabled", base.tier1_enabled)
    tier2_enabled = meta.get("tier2_enabled", base.tier2_enabled)
    tier3_enabled = meta.get("tier3_enabled", base.tier3_enabled)
    tier4_enabled = meta.get("tier4_enabled", base.tier4_enabled)
    require_independent = meta.get(
        "require_independent_verification",
        meta.get("require_tier2", base.require_independent_verification)
    )
    require_adversarial = meta.get(
        "require_adversarial_challenge",
        meta.get("require_tier3", base.require_adversarial_challenge)
    )

    # High-risk or security-sensitive tasks automatically enforce Tier 2 and Tier 3
    risk = str(meta.get("risk", "")).lower()
    if risk in ("high", "critical") or meta.get("require_tier2", False):
        tier2_enabled = True
        require_independent = True
    if risk in ("critical",) or meta.get("require_tier3", False):
        tier3_enabled = True
        require_adversarial = True

    allow_merge_after_tier1 = base.allow_merge_after_tier1
    if require_independent or require_adversarial:
        allow_merge_after_tier1 = False

    t1_cmds = list(meta.get("tier1_commands", base.tier1_commands))
    t2_cmds = list(meta.get("tier2_commands", base.tier2_commands))
    t3_cmds = list(meta.get("tier3_commands", base.tier3_commands))

    return VerificationPolicy(
        tier1_enabled=tier1_enabled,
        tier2_enabled=tier2_enabled,
        tier3_enabled=tier3_enabled,
        tier4_enabled=tier4_enabled,
        tier1_commands=t1_cmds,
        tier2_commands=t2_cmds,
        tier3_commands=t3_cmds,
        timeout=meta.get("timeout", base.timeout),
        require_independent_verification=require_independent,
        require_adversarial_challenge=require_adversarial,
        allow_merge_after_tier1=allow_merge_after_tier1,
        auto_repair=meta.get("auto_repair", base.auto_repair),
        max_repair_attempts=task.max_retries,
        custom_checks=list(meta.get("custom_checks", base.custom_checks)),
    )
