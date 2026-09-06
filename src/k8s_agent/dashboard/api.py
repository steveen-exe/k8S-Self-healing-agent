"""FastAPI application for the Kubernetes remediation dashboard with WebSocket support."""

from fastapi import FastAPI, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Dict, Optional, Set
import json
from pathlib import Path
from datetime import datetime
import structlog
import asyncio
import random
from enum import Enum

from k8s_agent.audit import AuditLogger
from k8s_agent.watcher import PodWatcher
from k8s_agent.diagnosis.engine import DiagnosisEngine
from k8s_agent.policy import PolicyEngine
from k8s_agent.config import Settings
from k8s_agent.dashboard.models import HistoricalAction, CurrentIssue, PendingAction, ApproveRejectResponse

logger = structlog.get_logger()

app = FastAPI(title="K8s Remediation Dashboard", version="0.1.0")

# Mount static files
app.mount("/static", StaticFiles(directory="src/k8s_agent/dashboard/static"), name="static")

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.pending_approvals: Dict[str, dict] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info("WebSocket client connected", count=len(self.active_connections))

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        logger.info("WebSocket client disconnected", count=len(self.active_connections))

    async def broadcast(self, message: dict):
        """Broadcast a message to all connected WebSocket clients."""
        if self.active_connections:
            # Convert message to JSON string
            data = json.dumps(message)
            # Send to all connected clients
            for connection in self.active_connections:
                try:
                    await connection.send_text(data)
                except Exception as e:
                    logger.error("Failed to send WebSocket message", error=str(e))
                    # Remove broken connection
                    self.active_connections.discard(connection)

manager = ConnectionManager()

# Shared instances from main agent
shared_policy_engine: Optional[PolicyEngine] = None
shared_settings: Optional[Settings] = None
shared_audit_logger: Optional[AuditLogger] = None

@app.get("/")
async def read_root():
    """Serve the dashboard HTML page."""
    try:
        with open("src/k8s_agent/dashboard/static/index.html") as f:
            content = f.read()
        return HTMLResponse(content=content)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Dashboard not found")

@app.get("/health")
async def health_check():
    """Health check endpoint for the dashboard."""
    return {"status": "healthy", "service": "k8s-remediation-dashboard"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time event updates."""
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, wait for any messages from client (if needed)
            data = await websocket.receive_text()
            # Echo back or handle client messages if needed
            await websocket.send_text(f"Message received: {data}")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error("WebSocket error", error=str(e))
        manager.disconnect(websocket)

def get_audit_logger() -> AuditLogger:
    if shared_audit_logger is not None:
        return shared_audit_logger
    # Fallback to creating a new one (should not happen in normal operation)
    from k8s_agent.config import load_settings
    settings = load_settings()
    return AuditLogger(settings)

def get_policy_engine() -> PolicyEngine:
    if shared_policy_engine is not None:
        return shared_policy_engine
    # Fallback to creating a new one (should not happen in normal operation)
    from k8s_agent.config import load_settings
    settings = load_settings()
    return PolicyEngine(settings)

def get_watcher() -> PodWatcher:
    # For current issues, we use a separate watcher instance in the dashboard
    # to avoid interfering with the main agent's watcher loop
    from k8s_agent.config import load_settings
    settings = load_settings()
    return PodWatcher(settings)

def get_diagnosis_engine() -> DiagnosisEngine:
    # For current issues, we use a separate diagnosis engine instance in the dashboard
    from k8s_agent.config import load_settings
    settings = load_settings()
    return DiagnosisEngine(settings)

@app.get("/api/historical-actions", response_model=List[HistoricalAction])
async def get_historical_actions(limit: int = 100):
    """Get historical actions from audit log."""
    try:
        audit_logger = get_audit_logger()
        actions = []

        if audit_logger.audit_log_path.exists():
            with open(audit_logger.audit_log_path, "r") as f:
                lines = f.readlines()
                # Get last 'limit' lines
                for line in lines[-limit:]:
                    try:
                        entry = json.loads(line.strip())
                        # Convert audit entry to historical action format
                        diagnosis_data = entry.get("diagnosis", {})
                        symptom_data = diagnosis_data.get("symptom", {})
                        result_data = entry.get("result", {})

                        action = HistoricalAction(
                            timestamp=entry.get("timestamp", ""),
                            event_type=entry.get("event_type", ""),
                            pod_name=symptom_data.get("pod_name", ""),
                            namespace=symptom_data.get("namespace", ""),
                            remediation_type=diagnosis_data.get("remediation_type", {}).value if isinstance(diagnosis_data.get(
                                "remediation_type"), Enum) else str(diagnosis_data.get("remediation_type", "")),
                            outcome=result_data.get("outcome", ""),
                            details=entry
                        )
                        actions.append(action)
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning("Failed to parse audit log entry", error=str(entry), exception=str(e))
                        continue

        return actions
    except Exception as e:
        logger.error("Failed to get historical actions", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve historical actions")

@app.get("/api/current-issues", response_model=List[CurrentIssue])
async def get_current_issues():
    """Get current issues being monitored."""
    try:
        watcher = get_watcher()
        diagnosis_engine = get_diagnosis_engine()
        # In a real implementation, we would get current issues from the watcher
        # For now, we can attempt to get real issues, but fall back to empty if there are connection issues
        issues = []
        try:
            # Get recent symptoms from watcher
            for symptom in watcher.watch_pods():
                # Diagnose the symptom
                diagnosis = diagnosis_engine.diagnose(symptom)
                if diagnosis:
                    # Convert to CurrentIssue model
                    issue = CurrentIssue(
                        symptom=symptom.model_dump(),
                        diagnosis=diagnosis.model_dump(),
                        suggested_fix=diagnosis.remediation_type.value if hasattr(diagnosis.remediation_type, 'value') else str(diagnosis.remediation_type),
                        risk_level=diagnosis.risk_level.value if hasattr(diagnosis.risk_level, 'value') else str(diagnosis.risk_level),
                        requires_approval=diagnosis.requires_approval
                    )
                    issues.append(issue)
                # Limit to prevent overload
                if len(issues) >= 10:
                    break
        except Exception as e:
            logger.warning("Could not get real current issues, using empty list", error=str(e))
            issues = []

        return issues
    except Exception as e:
        logger.error("Failed to get current issues", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve current issues")

@app.get("/api/pending-approvals", response_model=List[PendingAction])
async def get_pending_approvals():
    """Get actions requiring human approval."""
    try:
        policy_engine = get_policy_engine()
        pending_actions = policy_engine.get_pending_actions()
        # Convert to PendingAction models
        result = []
        for action_id, action_data in pending_actions.items():
            pending_action = PendingAction(
                action_id=action_id,
                symptom=action_data.get("symptom", {}),
                diagnosis=action_data.get("diagnosis", {}),
                suggested_remediation=action_data.get("suggested_remediation", {}),
                risk_level=action_data.get("risk_level", "unknown"),
                timestamp=action_data.get("timestamp", "")
            )
            result.append(pending_action)
        return result
    except Exception as e:
        logger.error("Failed to get pending approvals", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve pending approvals")

@app.post("/api/approve/{action_id}", response_model=ApproveRejectResponse)
async def approve_action(action_id: str):
    """Approve a pending action."""
    try:
        policy_engine = get_policy_engine()
        success = policy_engine.approve_action(action_id)
        if success:
            # Emit an event via WebSocket that the action was approved
            event = {
                "event_type": "fix_applied",  # More accurate event type
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "resource": {"namespace": "unknown", "kind": "Action", "name": action_id},
                "severity": "low",
                "payload": {
                    "action_id": action_id,
                    "approved_by": "operator",
                    "approved_at": datetime.utcnow().isoformat() + "Z"
                }
            }
            # Broadcast the approval event
            await manager.broadcast(event)
            return ApproveRejectResponse(
                success=True,
                message="Action approved successfully",
                action_id=action_id
            )
        else:
            return ApproveRejectResponse(
                success=False,
                message="Failed to approve action",
                action_id=action_id
            )
    except Exception as e:
        logger.error("Failed to approve action", action_id=action_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to approve action")

@app.post("/api/reject/{action_id}", response_model=ApproveRejectResponse)
async def reject_action(action_id: str):
    """Reject a pending action."""
    try:
        policy_engine = get_policy_engine()
        success = policy_engine.reject_action(action_id)
        if success:
            # Emit an event via WebSocket that the action was rejected
            event = {
                "event_type": "fix_rejected",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "resource": {"namespace": "unknown", "kind": "Action", "name": action_id},
                "severity": "low",
                "payload": {
                    "action_id": action_id,
                    "rejected_by": "operator",
                    "rejected_at": datetime.utcnow().isoformat() + "Z"
                }
            }
            # Broadcast the rejection event
            await manager.broadcast(event)
            return ApproveRejectResponse(
                success=True,
                message="Action rejected successfully",
                action_id=action_id
            )
        else:
            return ApproveRejectResponse(
                success=False,
                message="Failed to reject action",
                action_id=action_id
            )
    except Exception as e:
        logger.error("Failed to reject action", action_id=action_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to reject action")

# Function to be called by the main agent to emit real events
async def emit_event(event_type: str, resource: dict, severity: str, payload: dict):
    """Emit a real event to all connected WebSocket clients."""
    event = {
        "event_type": event_type,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "resource": resource,
        "severity": severity,
        "payload": payload
    }
    await manager.broadcast(event)
    logger.info("Broadcasted real event", event_type=event_type, resource=resource)

# Expose the emit function for use by the main agent
app.state.emit_event = emit_event

# Startup event to begin generating mock events (commented out for real events)
# @app.on_event("startup")
# async def startup_event():
#     """Start background tasks when the application starts."""
#     logger.info("Starting K8s Remediation Dashboard")
#     # Start the mock event generator in the background
#     asyncio.create_task(generate_mock_events())
#     logger.info("Mock event generator started")

# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    """Clean up when the application shuts down."""
    logger.info("Shutting down K8s Remediation Dashboard")

# Background task to emit real cluster state and pending actions
async def emit_cluster_state_and_pending_actions():
    """Emit real cluster state and pending actions periodically."""
    while True:
        try:
            # Emit cluster state
            cluster_state = await get_cluster_state()
            await manager.broadcast({
                "event_type": "cluster_state_update",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "payload": cluster_state
            })

            # Emit pending actions
            policy_engine = get_policy_engine()
            pending_actions = policy_engine.get_pending_actions()
            pending_actions_list = []
            for action_id, action_data in pending_actions.items():
                pending_actions_list.append({
                    "action_id": action_id,
                    "symptom": action_data.get("symptom", {}),
                    "diagnosis": action_data.get("diagnosis", {}),
                    "suggested_remediation": action_data.get("suggested_remediation", {}),
                    "risk_level": action_data.get("risk_level", "unknown"),
                    "timestamp": action_data.get("timestamp", "")
                })
            await manager.broadcast({
                "event_type": "pending_actions_update",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "payload": {
                    "pending_actions": pending_actions_list,
                    "count": len(pending_actions_list)
                }
            })

            # Wait for 5 seconds before next update
            await asyncio.sleep(5)
        except Exception as e:
            logger.error("Error in emit_cluster_state_and_pending_actions", error=str(e))
            await asyncio.sleep(5)  # Wait before retrying


async def get_cluster_state():
    """Get the current state of the Kubernetes cluster."""
    try:
        from kubernetes import client, config as k8s_config
        # Use shared settings or load settings
        settings = get_settings()
        if settings.kubeconfig_path:
            k8s_config.load_kube_config(config_file=settings.kubeconfig_path)
        else:
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()

        v1 = client.CoreV1Api()
        apps_v1 = client.AppsV1Api()

        # Get nodes
        nodes = v1.list_node()
        node_info = []
        for node in nodes.items:
            node_info.append({
                "name": node.metadata.name,
                "status": "Ready" if any(c.type == "Ready" and c.status == "True" for c in node.status.conditions) else "NotReady",
                "roles": [label.split("/")[-1] for label in node.metadata.labels.keys() if label.startswith("node-role.kubernetes.io/")] or ["<none>"],
                "age": node.metadata.creation_timestamp.isoformat() if node.metadata.creation_timestamp else ""
            })

        # Get namespaces
        namespaces = v1.list_namespace()
        namespace_info = []
        for ns in namespaces.items:
            namespace_info.append({
                "name": ns.metadata.name,
                "status": ns.status.phase,
                "age": ns.metadata.creation_timestamp.isoformat() if ns.metadata.creation_timestamp else ""
            })

        # Get pod summary across all namespaces (or just watched namespaces)
        # We'll get pods in all namespaces for simplicity, but could filter by settings.watcher.namespaces
        pods = v1.list_pod_for_all_namespaces(watch=False)
        pod_summary = {
            "total": len(pods.items),
            "running": len([p for p in pods.items if p.status.phase == "Running"]),
            "pending": len([p for p in pods.items if p.status.phase == "Pending"]),
            "failed": len([p for p in pods.items if p.status.phase == "Failed"]),
            "succeeded": len([p for p in pods.items if p.status.phase == "Succeeded"]),
            "unknown": len([p for p in pods.items if p.status.phase == "Unknown"])
        }

        # Get deployment summary
        deployments = apps_v1.list_deployment_for_all_namespaces(watch=False)
        deployment_summary = {
            "total": len(deployments.items),
            "available": len([d for d in deployments.items if d.status.available_replicas is not None and d.status.available_replicas > 0])
        }

        return {
            "nodes": node_info,
            "namespaces": namespace_info,
            "pod_summary": pod_summary,
            "deployment_summary": deployment_summary,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
    except Exception as e:
        logger.error("Failed to get cluster state", error=str(e))
        return {
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }


def get_settings():
    """Get settings shared with the agent or load if not available."""
    if shared_settings is not None:
        return shared_settings
    # Fallback to creating a new one (should not happen in normal operation)
    from k8s_agent.config import load_settings
    return load_settings()


# Startup event to begin generating real events
@app.on_event("startup")
async def startup_event():
    """Start background tasks when the application starts."""
    logger.info("Starting K8s Remediation Dashboard")
    # Start the background task for emitting cluster state and pending actions
    asyncio.create_task(emit_cluster_state_and_pending_actions())
    logger.info("Cluster state and pending actions emitter started")