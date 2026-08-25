"""Policy engine for authorization and rate limiting."""

import time
import structlog
from collections import deque
from datetime import datetime, timedelta

from .types import Diagnosis, RiskLevel
from .config import Settings

logger = structlog.get_logger()


class PolicyEngine:
    """Independent authorization and rate limiting for remediation actions."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._action_history: deque[datetime] = deque()

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
                return False, "Manual approval required"

            # Auto-approve only low-risk actions
            if diagnosis.risk_level != RiskLevel.LOW:
                return False, f"Manual approval required for {diagnosis.risk_level.value} risk actions"

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
