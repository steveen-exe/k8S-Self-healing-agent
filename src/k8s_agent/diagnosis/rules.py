"""Rule-based diagnosis matchers for common failure patterns."""

import re
import structlog

from ..types import Symptom, Diagnosis, FailureType, RemediationType, RiskLevel

logger = structlog.get_logger()


class RuleMatcher:
    """Fast path rule-based diagnosis for known failure patterns."""

    def diagnose(self, symptom: Symptom) -> Diagnosis | None:
        """Try to diagnose using rule-based patterns. Returns None if no match."""
        logger.debug("Attempting rule-based diagnosis", failure_type=symptom.failure_type)

        match symptom.failure_type:
            case FailureType.CRASH_LOOP_BACKOFF:
                return self._diagnose_crash_loop(symptom)
            case FailureType.IMAGE_PULL_BACKOFF:
                return self._diagnose_image_pull(symptom)
            case FailureType.OOM_KILLED:
                return self._diagnose_oom(symptom)
            case _:
                return None

    def _diagnose_crash_loop(self, symptom: Symptom) -> Diagnosis | None:
        """Diagnose CrashLoopBackOff failures."""
        logs = symptom.logs or ""

        # Pattern 1: OOM in logs
        if "out of memory" in logs.lower() or "cannot allocate memory" in logs.lower():
            return Diagnosis(
                symptom=symptom,
                root_cause="Container ran out of memory (OOM)",
                remediation_type=RemediationType.INCREASE_MEMORY,
                remediation_params={"memory_increase_factor": 1.5},
                risk_level=RiskLevel.MEDIUM,
                confidence=0.9,
                reasoning="Logs contain OOM indicators. Increasing memory limits should resolve.",
                requires_approval=True,
                diagnosed_by="rule_engine",
            )

        # Pattern 2: Exit code 137 (SIGKILL, often OOM)
        if symptom.last_state.get("exit_code") == 137:
            return Diagnosis(
                symptom=symptom,
                root_cause="Container killed with exit code 137 (likely OOM)",
                remediation_type=RemediationType.INCREASE_MEMORY,
                remediation_params={"memory_increase_factor": 1.5},
                risk_level=RiskLevel.MEDIUM,
                confidence=0.85,
                reasoning="Exit code 137 typically indicates OOM kill by kernel.",
                requires_approval=True,
                diagnosed_by="rule_engine",
            )

        # Pattern 3: Panic in logs (Go services)
        if re.search(r"panic:|fatal error:", logs, re.IGNORECASE):
            return Diagnosis(
                symptom=symptom,
                root_cause="Application panic detected in logs",
                remediation_type=RemediationType.RESTART_POD,
                remediation_params={},
                risk_level=RiskLevel.LOW,
                confidence=0.7,
                reasoning="Application crash detected. Restart may help, but code fix likely needed.",
                requires_approval=False,
                diagnosed_by="rule_engine",
            )

        # Pattern 4: Connection refused (dependency issue)
        if "connection refused" in logs.lower() or "unable to connect" in logs.lower():
            return Diagnosis(
                symptom=symptom,
                root_cause="Dependency connection failure during startup",
                remediation_type=RemediationType.NO_ACTION,
                remediation_params={},
                risk_level=RiskLevel.LOW,
                confidence=0.8,
                reasoning="Container cannot connect to dependency. Check dependent services first.",
                requires_approval=False,
                diagnosed_by="rule_engine",
            )

        # No rule match
        return None

    def _diagnose_image_pull(self, symptom: Symptom) -> Diagnosis | None:
        """Diagnose ImagePullBackOff failures."""
        message = symptom.message.lower()

        # Pattern 1: Authentication error
        if "unauthorized" in message or "authentication" in message:
            return Diagnosis(
                symptom=symptom,
                root_cause="Image registry authentication failure",
                remediation_type=RemediationType.NO_ACTION,
                remediation_params={},
                risk_level=RiskLevel.LOW,
                confidence=0.95,
                reasoning="Missing or invalid image pull secret. Manual intervention required.",
                requires_approval=False,
                diagnosed_by="rule_engine",
            )

        # Pattern 2: Not found
        if "not found" in message or "manifest unknown" in message:
            return Diagnosis(
                symptom=symptom,
                root_cause="Image or tag does not exist in registry",
                remediation_type=RemediationType.NO_ACTION,
                remediation_params={},
                risk_level=RiskLevel.LOW,
                confidence=0.95,
                reasoning="Image tag not found. Check image name and tag, or rollback deployment.",
                requires_approval=False,
                diagnosed_by="rule_engine",
            )

        # Pattern 3: Network timeout
        if "timeout" in message or "timed out" in message:
            return Diagnosis(
                symptom=symptom,
                root_cause="Network timeout pulling image",
                remediation_type=RemediationType.RESTART_POD,
                remediation_params={},
                risk_level=RiskLevel.LOW,
                confidence=0.7,
                reasoning="Transient network issue. Retry may succeed.",
                requires_approval=False,
                diagnosed_by="rule_engine",
            )

        return None

    def _diagnose_oom(self, symptom: Symptom) -> Diagnosis | None:
        """Diagnose OOMKilled failures."""
        return Diagnosis(
            symptom=symptom,
            root_cause="Container exceeded memory limit and was killed by Kubernetes",
            remediation_type=RemediationType.INCREASE_MEMORY,
            remediation_params={"memory_increase_factor": 1.5},
            risk_level=RiskLevel.MEDIUM,
            confidence=1.0,
            reasoning="OOMKilled status is definitive. Increase memory limits.",
            requires_approval=True,
            diagnosed_by="rule_engine",
        )
