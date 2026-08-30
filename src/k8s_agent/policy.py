"""Policy engine for authorization and rate limiting."""

import time
import structlog
import uuid
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .types import Diagnosis, RiskLevel, RemediationResult
from .config import Settings

logger = structlog.get_logger()


class PolicyEngine:
    """Independent authorization and rate limiting for remediation actions."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._action_history: deque[datetime] = deque()
        # Track pending actions requiring approval
        self._pending_actions: Dict[str, dict] = {}

    def authorize(self, diagnosis: Diagnosis) -> tuple[bool, str]:
        """
        Determine if a remediation should be executed.

        Returns: (authorized: bool, reason: str)
        """
        # Check dry-run mode
        if self.settings.policy.dry_run_only:
            return False, "System is in dry-run-only mode"

        # Check approval requirements
        if diagnosis.requires_approval:
            if not self.settings.policy.auto_approve_low_risk:
                # Generate action ID and add to pending actions
                action_id = str(uuid.uuid4())
                pending_action = {
                    "action_id": action_id,
                    "symptom": diagnosis.symptom.model_dump(),
                    "diagnosis": diagnosis.model_dump(),
                    "suggested_remediation": {
                        "remediation_type": diagnosis.remediation_type.value if hasattr(diagnosis.remediation_type, 'value') else str(diagnosis.remediation_type),
                        "parameters": {}  # Could be enhanced with actual parameters
                    },
                    "risk_level": diagnosis.risk_level.value if hasattr(diagnosis.risk_level, 'value') else str(diagnosis.risk_level),
                    "timestamp": datetime.utcnow().isoformat()
                }
                self._pending_actions[action_id] = pending_action
                logger.info(
                    "Action added to pending approvals",
                    action_id=action_id,
                    pod=diagnosis.symptom.pod_name,
                    remediation=diagnosis.remediation_type.value,
                )
                return False, f"Manual approval required. Action ID: {action_id}"

            # Auto-approve only low-risk actions
            if diagnosis.risk_level != RiskLevel.LOW:
                # Generate action ID and add to pending actions
                action_id = str(uuid.uuid4())
                pending_action = {
                    "action_id": action_id,
                    "symptom": diagnosis.symptom.model_dump(),
                    "diagnosis": diagnosis.model_dump(),
                    "suggested_remediation": {
                        "remediation_type": diagnosis.remediation_type.value if hasattr(diagnosis.remediation_type, 'value') else str(diagnosis.remediation_type),
                        "parameters": {}
                    },
                    "risk_level": diagnosis.risk_level.value if hasattr(diagnosis.risk_level, 'value') else str(diagnosis.risk_level),
                    "timestamp": datetime.utcnow().isoformat()
                }
                self._pending_actions[action_id] = pending_action
                logger.info(
                    "Action added to pending approvals",
                    action_id=action_id,
                    pod=diagnosis.symptom.pod_name,
                    remediation=diagnosis.remediation_type.value,
                )
                return False, f"Manual approval required for {diagnosis.risk_level.value} risk actions. Action ID: {action_id}"

        # Check rate limits
        if not self._check_rate_limit():
            return False, "Rate limit exceeded"

        # Authorization granted
        self._record_action()
        logger.info(
            "Remediation authorized",
            pod=diagnosis.symptom.pod_name,
            remediation=diagnosis.remediation_type.value,
        )
        return True, "Authorized"

    def _check_rate_limit(self) -> bool:
        """Check if rate limits allow another action."""
        now = datetime.utcnow()
        rate_config = self.settings.policy.rate_limit

        # Clean old entries
        minute_ago = now - timedelta(minutes=1)
        hour_ago = now - timedelta(hours=1)

        self._action_history = deque(
            [ts for ts in self._action_history if ts > hour_ago]
        )

        # Count recent actions
        actions_last_minute = sum(1 for ts in self._action_history if ts > minute_ago)
        actions_last_hour = len(self._action_history)

        if actions_last_minute >= rate_config.max_actions_per_minute:
            logger.warning(
                "Rate limit exceeded (per minute)",
                count=actions_last_minute,
                limit=rate_config.max_actions_per_minute,
            )
            return False

        if actions_last_hour >= rate_config.max_actions_per_hour:
            logger.warning(
                "Rate limit exceeded (per hour)",
                count=actions_last_hour,
                limit=rate_config.max_actions_per_hour,
            )
            return False

        return True

    def _record_action(self) -> None:
        """Record that an action was authorized."""
        self._action_history.append(datetime.utcnow())

    def get_pending_actions(self) -> List[dict]:
        """Get list of pending actions requiring approval."""
        return list(self._pending_actions.values())

    def approve_action(self, action_id: str) -> bool:
        """Approve a pending action."""
        if action_id in self._pending_actions:
            # Remove from pending and record as authorized (without re-checking policy)
            del self._pending_actions[action_id]
            self._record_action()
            logger.info("Action approved", action_id=action_id)
            return True
        return False

    def reject_action(self, action_id: str) -> bool:
        """Reject a pending action."""
        if action_id in self._pending_actions:
            del self._pending_actions[action_id]
            logger.info("Action rejected", action_id=action_id)
            return True
        return False
