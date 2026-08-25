"""Audit logging for SIEM integration."""

import json
import structlog
from pathlib import Path
from datetime import datetime

from .types import AuditEntry, Diagnosis, RemediationResult
from .config import Settings

logger = structlog.get_logger()


class AuditLogger:
    """Logs all diagnosis and remediation actions for compliance and debugging."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.audit_log_path = settings.audit_log_path

    def log_diagnosis(self, diagnosis: Diagnosis) -> None:
        """Log a completed diagnosis."""
        entry = AuditEntry(
            event_type="diagnosis_completed",
            diagnosis=diagnosis,
            metadata={
                "diagnosed_by": diagnosis.diagnosed_by,
                "confidence": diagnosis.confidence,
            },
        )
        self._write_entry(entry)

    def log_remediation(self, result: RemediationResult, approved_by: str = "system") -> None:
        """Log a remediation attempt (executed or skipped)."""
        entry = AuditEntry(
            event_type="remediation_executed" if result.executed else "remediation_skipped",
            diagnosis=result.diagnosis,
            result=result,
            approved_by=approved_by,
            metadata={
                "success": result.success,
                "dry_run": result.dry_run,
            },
        )
        self._write_entry(entry)

    def log_authorization_denied(self, diagnosis: Diagnosis, reason: str) -> None:
        """Log when a remediation was denied by policy engine."""
        entry = AuditEntry(
            event_type="authorization_denied",
            diagnosis=diagnosis,
            metadata={"denial_reason": reason},
        )
        self._write_entry(entry)

    def _write_entry(self, entry: AuditEntry) -> None:
        """Write audit entry to structured log file."""
        try:
            # Ensure parent directory exists
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)

            # Append JSON line
            with open(self.audit_log_path, "a") as f:
                json_line = entry.model_dump_json()
                f.write(json_line + "\n")

            logger.debug("Audit entry written", event_type=entry.event_type)

        except Exception as e:
            logger.error("Failed to write audit log", error=str(e))
