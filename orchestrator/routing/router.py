"""
Adaptive Orchestrator v5 - Intelligent Model Router.
Selects execution tiers (FAST vs PRO) deterministically based on task attributes,
failure history, and domain characteristics.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from orchestrator.models import Task
from orchestrator.routing.models import ExecutionProfile, ModelTier, RouterConfig
from orchestrator.scheduler.feedback import FeedbackCollector
from orchestrator.workers.models import Worker


class ModelRouter:
    """
    Deterministic Model Router.
    Routes tasks to FAST (e.g. Gemini Flash) or PRO (e.g. Gemini Pro) tiers.
    Does NOT invoke models or execute prompts; returns an ExecutionProfile decision object.
    """

    def __init__(self, config: Optional[RouterConfig] = None) -> None:
        self._config = config or RouterConfig()

    @property
    def config(self) -> RouterConfig:
        return self._config

    def route(
        self,
        task: Task,
        worker: Optional[Worker] = None,
        feedback: Optional[FeedbackCollector] = None,
    ) -> ExecutionProfile:
        """
        Computes the execution profile for a task.

        Decision precedence:
        1. Explicit Task Override (via task metadata 'model_tier' or 'force_tier')
        2. Escalation on Retries / Repeated Failures (retry_count >= threshold)
        3. Strategic Domain Requirements (e.g. architecture, security, audit)
        4. Strategic Task Type / Verification (e.g. adversarial review, planning)
        5. Priority Escalation (priority >= high_priority_threshold)
        6. Default Baseline (FAST for routine, high-volume implementation/testing)
        """
        # 1. Explicit override in task metadata
        task_meta = getattr(task, "metadata", {}) or {}
        if isinstance(task.result, dict) and "force_tier" in task.result:
            tier_str = str(task.result["force_tier"]).upper()
            tier = ModelTier.PRO if tier_str == "PRO" else ModelTier.FAST
            return self._build_profile(tier, f"Explicit override via task result: {tier_str}")

        if "model_tier" in task_meta:
            tier_str = str(task_meta["model_tier"]).upper()
            tier = ModelTier.PRO if "PRO" in tier_str else ModelTier.FAST
            return self._build_profile(tier, f"Explicit task metadata tier: {tier_str}")

        # 2. Escalation on repeated failures / retries
        if task.retry_count >= self._config.max_retries_before_escalation:
            return self._build_profile(
                ModelTier.PRO,
                f"Escalated to PRO after {task.retry_count} prior failure(s)"
            )

        # 3. Domain requirements
        domain_normalized = (task.domain or "").strip().lower()
        if domain_normalized in self._config.pro_domains:
            return self._build_profile(
                ModelTier.PRO,
                f"Domain '{task.domain}' requires frontier reasoning tier"
            )

        # 4. Task type or verification policy requirements
        task_type = str(task_meta.get("type", "")).upper()
        if task_type in self._config.pro_task_types:
            return self._build_profile(
                ModelTier.PRO,
                f"Task type '{task_type}' requires frontier reasoning tier"
            )

        if task_meta.get("adversarial_review") is True:
            return self._build_profile(
                ModelTier.PRO,
                "Adversarial verification requires frontier reasoning tier"
            )

        # 5. Priority escalation
        if task.priority >= self._config.high_priority_threshold:
            return self._build_profile(
                ModelTier.PRO,
                f"High task priority ({task.priority:.1f} >= {self._config.high_priority_threshold:.1f})"
            )

        # 6. Default to FAST
        return self._build_profile(
            ModelTier.FAST,
            "Standard implementation/testing task routed to fast execution tier"
        )

    def _build_profile(self, tier: ModelTier, reason: str, metadata: Optional[Dict[str, Any]] = None) -> ExecutionProfile:
        model_id = self._config.get_model_id_for_tier(tier)
        return ExecutionProfile(
            tier=tier,
            model_id=model_id,
            routing_reason=reason,
            metadata=metadata or {},
        )
