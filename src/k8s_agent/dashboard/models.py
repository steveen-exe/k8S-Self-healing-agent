"""Pydantic models for the dashboard API."""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel

from ..types import Diagnosis, RemediationResult, Symptom


class HistoricalAction(BaseModel):
    """Model for historical actions from audit log."""
    timestamp: str
    event_type: str
    pod_name: str
    namespace: str
    remediation_type: Optional[str] = None
    outcome: str
    details: Dict


class CurrentIssue(BaseModel):
    """Model for current issues being monitored."""
    symptom: Dict
    diagnosis: Dict
    suggested_fix: str
    risk_level: str
    requires_approval: bool


class PendingAction(BaseModel):
    """Model for actions requiring human approval."""
    action_id: str
    symptom: Dict
    diagnosis: Dict
    suggested_remediation: Dict
    risk_level: str
    timestamp: str


class ApproveRejectResponse(BaseModel):
    """Response model for approve/reject actions."""
    success: bool
    message: str
    action_id: str