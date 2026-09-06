"""Main entrypoint for Kubernetes remediation agent."""

import sys
import time
import structlog
import threading
import uvicorn
from pathlib import Path

from datetime import datetime

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

                # Check authorization using policy decision
                policy_decision = policy_engine.get_policy_decision(diagnosis)
                if policy_decision["decision"] != "ALLOW_AUTO":
                    logger.info(
                        "Remediation not authorized",
                        pod=symptom.pod_name,
                        reason=policy_decision["reasons"][0] if policy_decision["reasons"] else "Unknown reason",
                    )
                    # Log the authorization denied event with the reason from the policy decision
                    audit_logger.log_authorization_denied(diagnosis, "; ".join(policy_decision["reasons"]))

                    # If the decision is REQUIRE_APPROVAL, emit an approval requested event
                    if policy_decision["decision"] == "REQUIRE_APPROVAL":
                        action_id = policy_decision.get("action_id")
                        if action_id:
                            import asyncio
                            from k8s_agent.dashboard.api import app
                            try:
                                try:
                                    loop = asyncio.get_event_loop()
                                except RuntimeError:
                                    loop = asyncio.new_event_loop()
                                    asyncio.set_event_loop(loop)

                                approval_event = {
                                    "event_type": "approval_requested",
                                    "timestamp": datetime.utcnow().isoformat() + "Z",
                                    "resource": {
                                        "namespace": symptom.namespace,
                                        "kind": "Pod",
                                        "name": symptom.pod_name
                                    },
                                    "severity": "medium",
                                    "payload": {
                                        "action_id": action_id,
                                        "diagnosis": diagnosis.model_dump(),
                                        "risk_level": diagnosis.risk_level.value if hasattr(diagnosis.risk_level, 'value') else str(diagnosis.risk_level),
                                        "reasons": policy_decision["reasons"]
                                    }
                                }

                                if loop.is_running():
                                    asyncio.run_coroutine_threadsafe(
                                        app.state.emit_event(
                                            "approval_requested",
                                            approval_event["resource"],
                                            approval_event["severity"],
                                            approval_event["payload"]
                                        ),
                                        loop
                                    )
                                else:
                                    loop.run_until_complete(
                                        app.state.emit_event(
                                            "approval_requested",
                                            approval_event["resource"],
                                            approval_event["severity"],
                                            approval_event["payload"]
                                        )
                                    )
                            except Exception as e:
                                logger.error("Failed to emit approval requested event", error=str(e))
                    continue

                # Emit issue detected event to dashboard
                import asyncio
                from k8s_agent.dashboard.api import app
                try:
                    # Create event loop if needed
                    try:
                        loop = asyncio.get_event_loop()
                    except RuntimeError:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)

                    # Emit issue detected event
                    issue_event = {
                        "event_type": "issue_detected",
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                        "resource": {
                            "namespace": symptom.namespace,
                            "kind": "Pod",
                            "name": symptom.pod_name
                        },
                        "severity": "high" if diagnosis.risk_level.value in ["high", "medium"] else "medium",
                        "payload": {
                            "failure_type": symptom.failure_type.value,
                            "restart_count": symptom.restart_count,
                            "message": symptom.message or f"Pod {symptom.pod_name} is experiencing issues",
                            "diagnosis": {
                                "root_cause": diagnosis.root_cause,
                                "remediation_type": diagnosis.remediation_type.value if hasattr(diagnosis.remediation_type, 'value') else str(diagnosis.remediation_type),
                                "risk_level": diagnosis.risk_level.value if hasattr(diagnosis.risk_level, 'value') else str(diagnosis.risk_level),
                                "confidence": diagnosis.confidence
                            }
                        }
                    }

                    # Schedule the coroutine to run in the event loop
                    if loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            app.state.emit_event(
                                "issue_detected",
                                issue_event["resource"],
                                issue_event["severity"],
                                issue_event["payload"]
                            ),
                            loop
                        )
                    else:
                        loop.run_until_complete(
                            app.state.emit_event(
                                "issue_detected",
                                issue_event["resource"],
                                issue_event["severity"],
                                issue_event["payload"]
                            )
                        )
                except Exception as e:
                    logger.error("Failed to emit issue detected event", error=str(e))

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

                    # Emit fix applied event
                    try:
                        fix_event = {
                            "event_type": "fix_applied",
                            "timestamp": datetime.utcnow().isoformat() + "Z",
                            "resource": {
                                "namespace": symptom.namespace,
                                "kind": "Pod",
                                "name": symptom.pod_name
                            },
                            "severity": "low",
                            "payload": {
                                "remediation_type": diagnosis.remediation_type.value if hasattr(diagnosis.remediation_type, 'value') else str(diagnosis.remediation_type),
                                "message": result.output or "Remediation completed successfully",
                                "success": True,
                                "duration_seconds": result.timestamp.timestamp() - symptom.timestamp.timestamp() if hasattr(symptom.timestamp, 'timestamp') else 0
                            }
                        }

                        # Schedule the coroutine to run in the event loop
                        if loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                app.state.emit_event(
                                    "fix_applied",
                                    fix_event["resource"],
                                    fix_event["severity"],
                                    fix_event["payload"]
                                ),
                                loop
                            )
                        else:
                            loop.run_until_complete(
                                app.state.emit_event(
                                    "fix_applied",
                                    fix_event["resource"],
                                    fix_event["severity"],
                                    fix_event["payload"]
                                )
                            )
                    except Exception as e:
                        logger.error("Failed to emit fix applied event", error=str(e))

                else:
                    logger.error(
                        "Remediation failed",
                        pod=symptom.pod_name,
                        error=result.error,
                    )

                    # Emit fix failed event
                    try:
                        fail_event = {
                            "event_type": "fix_failed",
                            "timestamp": datetime.utcnow().isoformat() + "Z",
                            "resource": {
                                "namespace": symptom.namespace,
                                "kind": "Pod",
                                "name": symptom.pod_name
                            },
                            "severity": "medium",
                            "payload": {
                                "remediation_type": diagnosis.remediation_type.value if hasattr(diagnosis.remediation_type, 'value') else str(diagnosis.remediation_type),
                                "message": result.error or "Remediation failed",
                                "success": False,
                                "duration_seconds": result.timestamp.timestamp() - symptom.timestamp.timestamp() if hasattr(symptom.timestamp, 'timestamp') else 0
                            }
                        }

                        # Schedule the coroutine to run in the event loop
                        if loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                app.state.emit_event(
                                    "fix_failed",
                                    fail_event["resource"],
                                    fail_event["severity"],
                                    fail_event["payload"]
                                ),
                                loop
                            )
                        else:
                            loop.run_until_complete(
                                app.state.emit_event(
                                    "fix_failed",
                                    fail_event["resource"],
                                    fail_event["severity"],
                                    fail_event["payload"]
                                )
                            )
                    except Exception as e:
                        logger.error("Failed to emit fix failed event", error=str(e))

                # Record action result for policy engine risk assessment
                policy_engine.record_action_result(
                    action_id=f"action-{int(time.time())}",
                    success=result.success
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