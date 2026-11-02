"""Pydantic models for dashboard API responses."""

from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime


class NodeInfo(BaseModel):
    """Kubernetes node information."""
    name: str
    status: str
    roles: List[str] = Field(default_factory=list)
    age: str


class NamespaceSummary(BaseModel):
    """Summary of a namespace."""
    name: str
    status: str
    pod_count: int = 0
    pod_running: int = 0
    pod_pending: int = 0
    pod_failed: int = 0
    deployment_count: int = 0
    age: str


class ContainerResourceRequirements(BaseModel):
    """Container resource requests and limits."""
    cpu_request: Optional[str] = None
    cpu_limit: Optional[str] = None
    memory_request: Optional[str] = None
    memory_limit: Optional[str] = None


class PodInfo(BaseModel):
    """Detailed pod information."""
    name: str
    namespace: str
    status: str
    ready: str  # e.g., "2/2" containers ready
    restart_count: int = 0
    age: str
    node: Optional[str] = None
    resources: ContainerResourceRequirements = Field(default_factory=ContainerResourceRequirements)
    container_statuses: List[Dict[str, Any]] = Field(default_factory=list)


class DeploymentInfo(BaseModel):
    """Deployment information."""
    name: str
    namespace: str
    replicas_desired: int
    replicas_ready: int
    replicas_updated: int
    image: Optional[str] = None
    age: str
    pod_count: int = 0


class ClusterOverview(BaseModel):
    """Overall cluster state."""
    nodes: List[NodeInfo]
    namespaces: List[NamespaceSummary]
    pod_summary: Dict[str, int]  # running, pending, failed, succeeded, unknown counts
    deployment_summary: Dict[str, int]
    timestamp: str


class PodDetails(BaseModel):
    """Full pod details for drill-down view."""
    pod_info: PodInfo
    logs: Optional[str] = None
    events: List[Dict[str, Any]] = Field(default_factory=list)
    last_error: Optional[str] = None


class RecentIssue(BaseModel):
    """Recent audit event (diagnosis, remediation, or denial)."""
    timestamp: str
    event_type: str  # diagnosis_completed, remediation_executed, remediation_skipped, authorization_denied
    namespace: str
    pod_name: str
    container_name: Optional[str] = None
    failure_type: Optional[str] = None  # e.g., OOMKilled, CrashLoopBackOff
    root_cause: Optional[str] = None
    remediation_type: Optional[str] = None
    confidence: Optional[float] = None
    risk_level: Optional[str] = None  # low, medium, high
    decision: Optional[str] = None  # ALLOW_AUTO, REQUIRE_APPROVAL, DENY, etc.
    success: Optional[bool] = None
    reasoning: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RecentIssuesResponse(BaseModel):
    """Response containing recent issues."""
    issues: List[RecentIssue]
    total_count: int
    timestamp: str


class HistoricalAction(BaseModel):
    """Historical remediation action."""
    timestamp: str
    event_type: str
    pod_name: str
    namespace: str
    remediation_type: str
    outcome: str
    details: Dict[str, Any] = Field(default_factory=dict)


class CurrentIssue(BaseModel):
    """Current issue being monitored."""
    symptom: Dict[str, Any]
    diagnosis: Dict[str, Any]
    suggested_fix: str
    risk_level: str
    requires_approval: bool


class PendingAction(BaseModel):
    """Action pending operator approval."""
    action_id: str
    symptom: Dict[str, Any]
    diagnosis: Dict[str, Any]
    suggested_remediation: Dict[str, Any]
    risk_level: str
    timestamp: str


class ApproveRejectResponse(BaseModel):
    """Response to approve/reject action."""
    success: bool
    message: str
    action_id: str


class GrafanaAlert(BaseModel):
    """Minimal Grafana webhook payload accepted by the agent."""
    status: str
    title: str | None = None
    message: str | None = None
    rule_uid: str | None = None
    fingerprint: str | None = None
    labels: Dict[str, str] = Field(default_factory=dict)
    annotations: Dict[str, str] = Field(default_factory=dict)


class AlertIngestResponse(BaseModel):
    """Result of accepting a Grafana alert."""
    accepted: bool
    fingerprint: str
    message: str
