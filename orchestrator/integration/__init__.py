"""
Adaptive Orchestrator v5 - Controlled Integration & Merge Subsystem.
Enforces sequential, conflict-aware Git branch integration.
"""

from orchestrator.integration.adapter import (
    GitMergeAdapter,
    MergeAdapter,
    MockMergeAdapter,
)
from orchestrator.integration.manager import IntegrationManager
from orchestrator.integration.models import (
    MergeRequest,
    MergeResult,
    MergeStatus,
)
from orchestrator.integration.queue import MergeQueue

__all__ = [
    "MergeStatus",
    "MergeRequest",
    "MergeResult",
    "MergeAdapter",
    "MockMergeAdapter",
    "GitMergeAdapter",
    "MergeQueue",
    "IntegrationManager",
]
