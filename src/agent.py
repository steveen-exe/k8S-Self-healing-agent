"""Main entrypoint for Kubernetes remediation agent."""

import sys
import time
import structlog
import threading
import uvicorn
from pathlib import Path

from k8s_agent.config import load_settings
from k8s_agent.watcher import PodWatcher
from k8s_agent.diagnosis.engine import DiagnosisEngine
from k8s_agent.policy import PolicyEngine
from k8s_agent.executor import Executor
from k8s_agent.audit import AuditLogger

# Import dashboard components to set shared instances
from k8s_agent.dashboard.api import shared_policy_engine, shared_settings, shared_audit_logger


def setup_logging(log_level: str) -> None:
    """Configure structured logging."""
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def main() -> None:
    """Run the Kubernetes remediation agent."""
    # Load configuration
    config_path = Path("config/agent.yaml") if Path("config/agent.yaml").exists() else None
    settings = load_settings(config_path)

    # Setup logging
    setup_logging(settings.log_level)
    logger = structlog.get_logger()

    logger.info(
        "Starting Kubernetes Remediation Agent",
        dry_run_only=settings.policy.dry_run_only,
        namespaces=settings.watcher.namespaces,
    )

    # Initialize components
    watcher = PodWatcher(settings)
    diagnosis_engine = DiagnosisEngine(settings)
    policy_engine = PolicyEngine(settings)
    executor = Executor(settings)
    audit_logger = AuditLogger(settings)

    # Set shared instances for dashboard
    from k8s_agent.dashboard.api import shared_policy_engine, shared_settings, shared_audit_logger
    shared_policy_engine = policy_engine
    shared_settings = settings
    shared_audit_logger = audit_logger

    # Start dashboard server in a background thread
    dashboard_thread = threading.Thread(
        target=lambda: uvicorn.run(
            "k8s_agent.dashboard.api:app",
            host="0.0.0.0",
            port=8000,
            log_level="info"
        ),
        daemon=True
    )
    dashboard_thread.start()
    logger.info("Dashboard server started on http://0.0.0.0:8000")

    # Main watch loop
    while True:
        try:
            logger.debug("Starting watch cycle")

            # Watch for symptoms
            for symptom in watcher.watch_pods():
                logger.info(
                    "Detected failing pod",
                    pod=symptom.pod_name,
                    namespace=symptom.namespace,
                    failure_type=symptom.failure_type.value,
                )

                # Diagnose
                diagnosis = diagnosis_engine.diagnose(symptom)
                if not diagnosis:
                    logger.warning("Could not diagnose symptom", pod=symptom.pod_name)
                    continue

                audit_logger.log_diagnosis(diagnosis)

                logger.info(
                    "Diagnosis completed",
                    pod=symptom.pod_name,
                    root_cause=diagnosis.root_cause,
                    remediation=diagnosis.remediation_type.value,
                    confidence=diagnosis.confidence,
                )

                # Check authorization
                authorized, reason = policy_engine.authorize(diagnosis)
                if not authorized:
                    logger.info(
                        "Remediation not authorized",
                        pod=symptom.pod_name,
                        reason=reason,
                    )
                    audit_logger.log_authorization_denied(diagnosis, reason)
                    continue

                # Execute remediation
                result = executor.execute(diagnosis)
                audit_logger.log_remediation(result)

                if result.success:
                    logger.info(
                        "Remediation completed",
                        pod=symptom.pod_name,
                        output=result.output,
                        dry_run=result.dry_run,
                    )
                else:
                    logger.error(
                        "Remediation failed",
                        pod=symptom.pod_name,
                        error=result.error,
                    )

            # Sleep before next watch cycle
            logger.debug("Watch cycle complete, sleeping", seconds=settings.watcher.watch_interval_seconds)
            time.sleep(settings.watcher.watch_interval_seconds)

        except KeyboardInterrupt:
            logger.info("Shutting down agent (received interrupt)")
            break
        except Exception as e:
            logger.error("Unexpected error in main loop", error=str(e), exc_info=True)
            time.sleep(10)  # Back off on error


if __name__ == "__main__":
    main()
