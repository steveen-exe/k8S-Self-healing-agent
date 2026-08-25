"""Core domain types for the remediation agent."""

from enum import Enum
from typing import Any
from datetime import datetime
from pydantic import BaseModel, Field


class FailureType(str, Enum):
    """Known Kubernetes failure patterns."""
    CRASH_LOOP_BACKOFF = "CrashLoopBackOff"
    IMAGE_PULL_BACKOFF = "ImagePullBackOff"
    OOM_KILLED = "OOMKilled"
    UNKNOWN = "Unknown"


class RemediationType(str, Enum):
    """Available remediation actions."""
    RESTART_POD = "restart_pod"
    INCREASE_MEMORY = "increase_memory"
    UPDATE_IMAGE_TAG = "update_image_tag"
    ROLLBACK_DEPLOYMENT = "rollback_deployment"
    NO_ACTION = "no_action"


class RiskLevel(str, Enum):
    """Risk classification for remediations."""
    LOW = "low"          # Safe, reversible (e.g., restart pod)
    MEDIUM = "medium"    # Some impact (e.g., update resources)
    HIGH = "high"        # Significant impact (e.g., rollback)


class Symptom(BaseModel):
    """Observed failure symptom from Kubernetes."""
    namespace: str
    pod_name: str
    container_name: str | None = None
    failure_type: FailureType
    message: str
    logs: str | None = None
    restart_count: int = 0
    last_state: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class Diagnosis(BaseModel):
    """Diagnosis result with proposed remediation."""
    symptom: Symptom
    root_cause: str
    remediation_type: RemediationType
    remediation_params: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    requires_approval: bool = True
    diagnosed_by: str  # "rule_engine" or "llm"


class RemediationResult(BaseModel):
    """Result of executing a remediation."""
    diagnosis: Diagnosis
    executed: bool
    success: bool
    dry_run: bool = False
    output: str
    error: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AuditEntry(BaseModel):
    """Audit log entry for SIEM."""
    event_type: str
    diagnosis: Diagnosis | None = None
    result: RemediationResult | None = None
    user: str = "system"
    approved_by: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
