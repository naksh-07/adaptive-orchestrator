"""
Adaptive Orchestrator v5 - Persistence Package.
Provides atomic state persistence, checkpoint policies, and crash recovery.
"""

from orchestrator.persistence.checkpoint import CheckpointPolicy
from orchestrator.persistence.manager import PersistenceManager, atomic_write_json
from orchestrator.persistence.models import (
    CheckpointTrigger,
    InterruptedTaskRecovery,
    RecoveryReport,
    SerializedMissionState,
)

__all__ = [
    "CheckpointTrigger",
    "SerializedMissionState",
    "InterruptedTaskRecovery",
    "RecoveryReport",
    "CheckpointPolicy",
    "PersistenceManager",
    "atomic_write_json",
]
