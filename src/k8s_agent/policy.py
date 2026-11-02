
"""Policy engine for authorization and rate limiting."""

import time
import structlog
import threading
import uuid
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import statistics

from .types import Diagnosis, RiskLevel, RemediationResult
from enum import Enum
from .config import Settings
from .action_store import ActionStore

logger = structlog.get_logger()


class PolicyEngine:
    """Independent authorization and rate limiting for remediation actions."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._action_history: deque[datetime] = deque()
        self._action_results: deque[Tuple[datetime, bool]] = deque()  # (timestamp, success)
        self._risk_assessment_cache: Dict[str, dict] = {}
        # Track pending actions requiring approval
        self._pending_actions: Dict[str, dict] = {}
        # Track approved actions (for agent execution)
        self._approved_actions: Dict[str, dict] = {}
        # Track rejected actions
        self._rejected_actions: Dict[str, dict] = {}
        self._lock = threading.Lock()
        configured_store_path = getattr(settings, "action_store_path", Path("logs/actions.sqlite3"))
        self._action_store = ActionStore(configured_store_path)

        # Risk assessment weights
        self.risk_weights = {
            'base_risk': 0.4,
            'time_of_day': 0.15,
            'frequency': 0.15,
            'recent_success_rate': 0.2,
            'resource_impact': 0.1
        }

        # Confidence threshold for diagnosis acceptance
        # Default to 0.7 if not explicitly set in PolicyConfig
        self.min_confidence_threshold = 0.7

    def authorize(self, diagnosis: Diagnosis) -> tuple[bool, str]:
        """
        Determine if a remediation should be executed.

        Returns: (authorized: bool, reason: str)
        """
        # Check dry-run mode
        if self.settings.policy.dry_run_only:
            return False, "System is in dry-run-only mode"

        # Check confidence threshold
        if diagnosis.confidence < self.min_confidence_threshold:
            return False, f"Diagnosis confidence {diagnosis.confidence:.2f} below threshold {self.min_confidence_threshold}"

        # Perform enhanced risk assessment
        enhanced_risk_level, risk_factors = self._assess_enhanced_risk(diagnosis)

        # Use enhanced_risk_level for decisions but do NOT mutate diagnosis
        # Check approval requirements based on enhanced risk
        if diagnosis.requires_approval or self._requires_approval(enhanced_risk_level):
            if not self.settings.policy.auto_approve_low_risk:
                # Generate action ID and add to pending actions
                action_id = str(uuid.uuid4())
                pending_action = {
                    "action_id": action_id,
                    "symptom": diagnosis.symptom.model_dump(),
                    "diagnosis": diagnosis.model_dump(),
                    "suggested_remediation": {
                        "remediation_type": diagnosis.remediation_type.value if hasattr(diagnosis.remediation_type, 'value') else str(diagnosis.remediation_type),
                        "parameters": diagnosis.remediation_params
                    },
                    "risk_level": enhanced_risk_level.value if hasattr(enhanced_risk_level, 'value') else str(enhanced_risk_level),
                    "risk_factors": risk_factors,
                    "timestamp": datetime.utcnow().isoformat()
                }
                self._pending_actions[action_id] = pending_action
                self._action_store.create(action_id, pending_action)
                logger.info(
                    "Action added to pending approvals",
                    action_id=action_id,
                    pod=diagnosis.symptom.pod_name,
                    remediation=diagnosis.remediation_type.value,
                    enhanced_risk=enhanced_risk_level.value,
                    risk_factors=risk_factors
                )
                return False, f"Manual approval required. Action ID: {action_id}"

            # Auto-approve only low-risk actions
            if enhanced_risk_level != RiskLevel.LOW:
                # Generate action ID and add to pending actions
                action_id = str(uuid.uuid4())
                pending_action = {
                    "action_id": action_id,
                    "symptom": diagnosis.symptom.model_dump(),
                    "diagnosis": diagnosis.model_dump(),
                    "suggested_remediation": {
                        "remediation_type": diagnosis.remediation_type.value if hasattr(diagnosis.remediation_type, 'value') else str(diagnosis.remediation_type),
                        "parameters": diagnosis.remediation_params
                    },
                    "risk_level": enhanced_risk_level.value if hasattr(enhanced_risk_level, 'value') else str(enhanced_risk_level),
                    "risk_factors": risk_factors,
                    "timestamp": datetime.utcnow().isoformat()
                }
                self._pending_actions[action_id] = pending_action
                self._action_store.create(action_id, pending_action)
                logger.info(
                    "Action added to pending approvals",
                    action_id=action_id,
                    pod=diagnosis.symptom.pod_name,
                    remediation=diagnosis.remediation_type.value,
                    enhanced_risk=enhanced_risk_level.value,
                    risk_factors=risk_factors
                )
                return False, f"Manual approval required for {enhanced_risk_level.value} risk actions. Action ID: {action_id}"

        # Check rate limits
        if not self._check_rate_limit():
            return False, "Rate limit exceeded"

        # Authorization granted
        self._record_action()
        logger.info(
            "Remediation authorized",
            pod=diagnosis.symptom.pod_name,
            remediation=diagnosis.remediation_type.value,
            risk_level=diagnosis.risk_level.value,
            risk_score=sum(risk_factors.values()) if risk_factors else 0
        )
        return True, "Authorized"

    def _assess_enhanced_risk(self, diagnosis: Diagnosis) -> Tuple[RiskLevel, Dict[str, float]]:
        """
        Perform enhanced risk assessment based on multiple factors.

        Returns: (enhanced_risk_level, risk_factors_dict)
        """
        risk_factors = {}
        total_risk_score = 0.0

        # Base risk from diagnosis
        base_risk_score = self._risk_level_to_score(diagnosis.risk_level)
        risk_factors['base_risk'] = base_risk_score * self.risk_weights['base_risk']
        total_risk_score += risk_factors['base_risk']

        # Time of day risk (higher risk during business hours)
        time_risk = self._assess_time_of_day_risk()
        risk_factors['time_of_day'] = time_risk * self.risk_weights['time_of_day']
        total_risk_score += risk_factors['time_of_day']

        # Frequency risk (higher risk if same issue occurs frequently)
        frequency_risk = self._assess_frequency_risk(diagnosis)
        risk_factors['frequency'] = frequency_risk * self.risk_weights['frequency']
        total_risk_score += risk_factors['frequency']

        # Recent success rate risk (lower success = higher risk)
        success_risk = self._assess_recent_success_rate_risk()
        risk_factors['recent_success_rate'] = success_risk * self.risk_weights['recent_success_rate']
        total_risk_score += risk_factors['recent_success_rate']

        # Resource impact risk
        resource_risk = self._assess_resource_impact_risk(diagnosis)
        risk_factors['resource_impact'] = resource_risk * self.risk_weights['resource_impact']
        total_risk_score += risk_factors['resource_impact']

        # Normalize total risk score to 0-1 range
        normalized_risk = min(1.0, max(0.0, total_risk_score))

        # Convert back to risk level
        enhanced_risk_level = self._score_to_risk_level(normalized_risk)

        logger.debug(
            "Enhanced risk assessment completed",
            base_risk=diagnosis.risk_level.value,
            enhanced_risk=enhanced_risk_level.value,
            risk_score=normalized_risk,
            risk_factors=risk_factors
        )

        return enhanced_risk_level, risk_factors

    def _risk_level_to_score(self, risk_level: RiskLevel) -> float:
        """Convert risk level to numerical score (0-1)."""
        mapping = {
            RiskLevel.LOW: 0.25,
            RiskLevel.MEDIUM: 0.5,
            RiskLevel.HIGH: 0.75
        }
        return mapping.get(risk_level, 0.5)

    def _score_to_risk_level(self, score: float) -> RiskLevel:
        """Convert numerical score (0-1) to risk level."""
        if score < 0.35:
            return RiskLevel.LOW
        elif score < 0.65:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.HIGH

    def _assess_time_of_day_risk(self) -> float:
        """Assess risk based on time of day (higher during business hours)."""
        hour = datetime.utcnow().hour
        # Business hours: 9 AM - 5 PM UTC (adjust as needed for your timezone)
        if 9 <= hour <= 17:
            return 0.7  # Higher risk during business hours
        elif 22 <= hour or hour <= 6:  # Night time
            return 0.3  # Lower risk during off-hours
        else:
            return 0.5  # Moderate risk during transition hours

    def _assess_frequency_risk(self, diagnosis: Diagnosis) -> float:
        """Assess risk based on frequency of similar issues."""
        # Create a key based on pod name and failure type
        key = f"{diagnosis.symptom.namespace}:{diagnosis.symptom.pod_name}:{diagnosis.symptom.failure_type.value}"

        # Check recent occurrences (last hour)
        now = datetime.utcnow()
        one_hour_ago = now - timedelta(hours=1)

        # Count similar actions in recent history
        similar_count = 0
        for action_timestamp, success in self._action_results:
            if action_timestamp > one_hour_ago:
                # We would need to store more context in action_results to match properly
                # For now, using a simplified approach
                pass

        # Simple frequency assessment based on restart count
        restart_count = diagnosis.symptom.restart_count
        if restart_count >= 10:
            return 0.8  # High frequency
        elif restart_count >= 5:
            return 0.6  # Medium frequency
        elif restart_count >= 3:
            return 0.4  # Low-medium frequency
        else:
            return 0.2  # Low frequency

    def _assess_recent_success_rate_risk(self) -> float:
        """Assess risk based on recent success rate (inverse - lower success = higher risk)."""
        if len(self._action_results) < 3:
            return 0.5  # Neutral if not enough data

        # Look at last 10 actions or all if less than 10
        recent_actions = list(self._action_results)[-10:] if len(self._action_results) >= 10 else list(self._action_results)
        successful_actions = sum(1 for _, success in recent_actions if success)
        success_rate = successful_actions / len(recent_actions) if recent_actions else 0.5

        # Invert so that lower success rate = higher risk
        return 1.0 - success_rate

    def _assess_resource_impact_risk(self, diagnosis: Diagnosis) -> float:
        """Assess risk based on potential resource impact."""
        remediation_type = diagnosis.remediation_type

        # Define risk levels for different remediation types
        resource_risk_map = {
            'restart_pod': 0.2,           # Low impact
            'increase_memory': 0.4,       # Low-medium impact
            'update_image_tag': 0.5,      # Medium impact
            'rollback_deployment': 0.7,   # Medium-high impact
            'scale_deployment': 0.4,      # Low-medium impact
            'add_replica': 0.3,           # Low impact
            'no_action': 0.1              # Very low impact
        }

        remediation_str = remediation_type.value if hasattr(remediation_type, 'value') else str(remediation_type)
        return resource_risk_map.get(remediation_str, 0.5)

    def _requires_approval(self, risk_level: RiskLevel) -> bool:
        """Determine if action requires approval based on risk level and policy."""
        # Always require approval for high risk
        if risk_level == RiskLevel.HIGH:
            return True

        # Require approval for medium risk if auto-approve is disabled for low risk only
        if risk_level == RiskLevel.MEDIUM and not self.settings.policy.auto_approve_low_risk:
            return True

        return False

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
        # Note: Actual success/failure would be recorded elsewhere when we know the outcome

    def record_action_result(self, action_id: str, success: bool) -> None:
        """Record the result of an executed action for future risk assessment."""
        self._action_results.append((datetime.utcnow(), success))
        # Keep only recent results (last 100 actions)
        if len(self._action_results) > 100:
            self._action_results = deque(list(self._action_results)[-100:])

    def get_pending_actions(self) -> Dict[str, dict]:
        """Get dict of pending actions requiring approval."""
        with self._lock:
            persisted = self._action_store.list_by_status("pending")
            self._pending_actions.update(persisted)
            return dict(self._pending_actions)

    def approve_action(self, action_id: str) -> bool:
        """Approve a pending action."""
        with self._lock:
            if action_id in self._pending_actions:
                action_data = self._pending_actions.pop(action_id)
                self._approved_actions[action_id] = action_data
                self._action_store.transition(
                    action_id, "pending", "approved", actor="operator"
                )
                self._record_action()
                logger.info("Action approved", action_id=action_id)
                return True
            return False

    def reject_action(self, action_id: str) -> bool:
        """Reject a pending action."""
        with self._lock:
            if action_id in self._pending_actions:
                action_data = self._pending_actions.pop(action_id)
                self._rejected_actions[action_id] = action_data
                self._action_store.transition(
                    action_id, "pending", "rejected", actor="operator"
                )
                logger.info("Action rejected", action_id=action_id)
                return True
            return False

    def claim_approved_action(self, action_id: str) -> Optional[dict]:
        """Atomically claim an approved action for execution. Returns payload or None."""
        with self._lock:
            action_data = self._approved_actions.get(action_id)
            if action_data is None:
                action_data = self._action_store.list_by_status("approved").get(action_id)
            if action_data is None:
                return None
            claimed = self._action_store.transition(
                action_id, "approved", "claimed", actor="agent"
            )
            if claimed is None:
                return None
            self._approved_actions.pop(action_id, None)
            return claimed

    def get_approved_action_ids(self) -> list:
        """Return list of action IDs currently approved and awaiting execution."""
        with self._lock:
            persisted = self._action_store.list_by_status("approved")
            self._approved_actions.update(persisted)
            return list(self._approved_actions.keys())

    def complete_action(self, action_id: str, success: bool, error: str | None = None) -> bool:
        """Persist the terminal result of a claimed action."""
        terminal_status = "succeeded" if success else "failed"
        with self._lock:
            return (
                self._action_store.transition(
                    action_id,
                    "claimed",
                    terminal_status,
                    actor="agent",
                    error=error,
                )
                is not None
            )

    def get_risk_assessment_stats(self) -> dict:
        """Get statistics about risk assessments for monitoring."""
        if not self._action_results:
            return {"total_actions": 0, "success_rate": 0.0, "recent_trend": "unknown"}

        total_actions = len(self._action_results)
        successful_actions = sum(1 for _, success in self._action_results if success)
        success_rate = successful_actions / total_actions if total_actions > 0 else 0.0

        # Calculate recent trend (last 10 vs previous 10)
        if len(self._action_results) >= 20:
            recent = list(self._action_results)[-10:]
            previous = list(self._action_results)[-20:-10]
            recent_success = sum(1 for _, success in recent if success) / len(recent)
            previous_success = sum(1 for _, success in previous if success) / len(previous)
            trend = "improving" if recent_success > previous_success else "declining" if recent_success < previous_success else "stable"
        else:
            trend = "insufficient_data"

        return {
            "total_actions": total_actions,
            "success_rate": success_rate,
            "recent_trend": trend,
            "pending_approvals": len(self._pending_actions)
        }

    def get_policy_decision(self, diagnosis: Diagnosis) -> dict:
        """
        Provide a structured policy decision for a diagnosis.

        Returns:
            dict with keys:
                decision: str (ALLOW_AUTO, REQUIRE_APPROVAL, DENY)
                risk_score: float (0-1)
                risk_level: RiskLevel
                reasons: List[str]
                required_approval: bool
                policy_rule: str
                constraints: dict
                action_id: str (if approval required)
        """
        # Check dry-run mode
        if self.settings.policy.dry_run_only:
            return {
                "decision": "DENY",
                "risk_score": 0.0,
                "risk_level": diagnosis.risk_level,
                "reasons": ["System is in dry-run-only mode"],
                "required_approval": False,
                "policy_rule": "dry_run_only",
                "constraints": {},
                "action_id": None
            }

        # Check confidence threshold
        if diagnosis.confidence < self.min_confidence_threshold:
            return {
                "decision": "DENY",
                "risk_score": 0.0,
                "risk_level": diagnosis.risk_level,
                "reasons": [f"Diagnosis confidence {diagnosis.confidence:.2f} below threshold {self.min_confidence_threshold}"],
                "required_approval": False,
                "policy_rule": "confidence_threshold",
                "constraints": {},
                "action_id": None
            }

        # Perform enhanced risk assessment
        enhanced_risk_level, risk_factors = self._assess_enhanced_risk(diagnosis)
        total_risk_score = sum(risk_factors.values()) if risk_factors else 0.0

        # Use enhanced_risk_level for decisions but do NOT mutate diagnosis
        # Determine if approval is required
        requires_approval = diagnosis.requires_approval or self._requires_approval(enhanced_risk_level)

        # Build reasons list
        reasons = []
        if self.settings.policy.dry_run_only:
            reasons.append("System is in dry-run-only mode")
        else:
            reasons.append(f"Enhanced risk assessment score: {total_risk_score:.2f}")
            for factor, score in risk_factors.items():
                reasons.append(f"{factor}: {score:.2f}")

        # Check rate limits
        if not self._check_rate_limit():
            return {
                "decision": "DENY",
                "risk_score": total_risk_score,
                "risk_level": enhanced_risk_level,
                "reasons": reasons + ["Rate limit exceeded"],
                "required_approval": False,
                "policy_rule": "rate_limit",
                "constraints": {},
                "action_id": None
            }

        # Determine decision
        if requires_approval and not self.settings.policy.auto_approve_low_risk:
            if enhanced_risk_level != RiskLevel.LOW:
                # Generate action ID for pending approval
                action_id = str(uuid.uuid4())
                pending_action = {
                    "action_id": action_id,
                    "symptom": diagnosis.symptom.model_dump(),
                    "diagnosis": diagnosis.model_dump(),
                    "suggested_remediation": {
                        "remediation_type": diagnosis.remediation_type.value if hasattr(diagnosis.remediation_type, 'value') else str(diagnosis.remediation_type),
                        "parameters": diagnosis.remediation_params
                    },
                    "risk_level": enhanced_risk_level.value if hasattr(enhanced_risk_level, 'value') else str(enhanced_risk_level),
                    "risk_factors": risk_factors,
                    "timestamp": datetime.utcnow().isoformat()
                }
                self._pending_actions[action_id] = pending_action

                reasons.append(f"Manual approval required for {enhanced_risk_level.value} risk action")

                return {
                    "decision": "REQUIRE_APPROVAL",
                    "risk_score": total_risk_score,
                    "risk_level": enhanced_risk_level,
                    "reasons": reasons,
                    "required_approval": True,
                    "policy_rule": "approval_required",
                    "constraints": {
                        "auto_approve_low_risk": self.settings.policy.auto_approve_low_risk
                    },
                    "action_id": action_id
                }

        # Authorization granted
        self._record_action()
        reasons.append("Remediation authorized by policy engine")

        return {
            "decision": "ALLOW_AUTO",
            "risk_score": total_risk_score,
            "risk_level": enhanced_risk_level,
            "reasons": reasons,
            "required_approval": False,
            "policy_rule": "authorized",
            "constraints": {},
            "action_id": None
        }