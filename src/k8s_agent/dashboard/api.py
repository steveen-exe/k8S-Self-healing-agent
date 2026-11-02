"""FastAPI application for the Kubernetes remediation dashboard with WebSocket support."""

from fastapi import (
    FastAPI,
    HTTPException,
    BackgroundTasks,
    WebSocket,
    WebSocketDisconnect,
    Header,
)
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
from k8s_agent.metrics import metrics
from k8s_agent.dashboard.models import (
    HistoricalAction, CurrentIssue, PendingAction, ApproveRejectResponse,
    ClusterOverview, PodDetails, RecentIssuesResponse, RecentIssue,
    NodeInfo, NamespaceSummary, PodInfo, DeploymentInfo
    , GrafanaAlert, AlertIngestResponse
)

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

# Cache for current issues to avoid repeated diagnosis on each HTTP request
class CurrentIssueCache:
    """Thread-safe cache for current cluster issues."""
    def __init__(self, max_items: int = 50):
        self.max_items = max_items
        self.issues: List[dict] = []
        self._lock = asyncio.Lock()

    async def set_issues(self, issues: List[dict]) -> None:
        """Update the cached issues (called by agent)."""
        async with self._lock:
            self.issues = issues[:self.max_items]

    async def get_issues(self) -> List[dict]:
        """Get cached issues without running diagnosis."""
        async with self._lock:
            return list(self.issues)

    async def add_issue(self, issue: dict) -> None:
        """Add a single issue to the cache."""
        async with self._lock:
            self.issues = [issue] + self.issues[:self.max_items - 1]

# Global issue cache
issue_cache = CurrentIssueCache()

# Shared instances from main agent
shared_policy_engine: Optional[PolicyEngine] = None
shared_settings: Optional[Settings] = None
shared_audit_logger: Optional[AuditLogger] = None
shared_watcher: Optional[PodWatcher] = None
shared_diagnosis_engine: Optional[DiagnosisEngine] = None
_grafana_fingerprints: Set[str] = set()


def _settings_for_api() -> Settings:
    if shared_settings is not None:
        return shared_settings
    from k8s_agent.config import load_settings
    return load_settings()


def _require_api_token(authorization: str | None) -> None:
    """Require bearer authentication when an API token is configured."""
    expected = _settings_for_api().api_token
    if expected is None:
        raise HTTPException(
            status_code=503,
            detail="Operator API authentication is not configured",
        )
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


def _require_grafana_secret(secret: str | None) -> None:
    expected = _settings_for_api().grafana_webhook_secret
    if expected is None:
        raise HTTPException(
            status_code=503,
            detail="Grafana webhook authentication is not configured",
        )
    if secret != expected:
        raise HTTPException(status_code=401, detail="Invalid Grafana webhook secret")

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


@app.get("/metrics", include_in_schema=False)
async def metrics_endpoint():
    """Expose agent counters for Prometheus and Grafana."""
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(
        metrics.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )

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
        log_path = Path(audit_logger.audit_log_path)

        if log_path.exists():
            with open(log_path, "r") as f:
                lines = f.readlines()
                # Get last 'limit' lines
                for line in lines[-limit:]:
                    try:
                        entry = json.loads(line.strip())
                        # Convert audit entry to historical action format
                        diagnosis_data = entry.get("diagnosis") or {}
                        symptom_data = diagnosis_data.get("symptom") or {}
                        result_data = entry.get("result") or {}

                        remediation_type_val = diagnosis_data.get("remediation_type", "")
                        if isinstance(remediation_type_val, Enum):
                            remediation_type_str = remediation_type_val.value
                        elif isinstance(remediation_type_val, dict):
                            remediation_type_str = str(remediation_type_val.get("value", ""))
                        else:
                            remediation_type_str = str(remediation_type_val or "")

                        action = HistoricalAction(
                            timestamp=entry.get("timestamp", ""),
                            event_type=entry.get("event_type", ""),
                            pod_name=symptom_data.get("pod_name", ""),
                            namespace=symptom_data.get("namespace", ""),
                            remediation_type=remediation_type_str,
                            outcome=result_data.get("outcome", result_data.get("output", "logged")),
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
    """Get current issues from cache (maintained by agent, not computed on each request)."""
    try:
        # Return cached issues without running any diagnosis
        cached_issues = await issue_cache.get_issues()
        return cached_issues if cached_issues else []
    except Exception as e:
        logger.error("Failed to get current issues from cache", error=str(e))
        return []

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
                "event_type": "action_approved",
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


# Versioned API used by Grafana and future clients. Legacy routes above remain
# available for the embedded dashboard during migration.
@app.get("/api/v1/health")
async def versioned_health():
    return await health_check()


@app.get("/api/v1/metrics", include_in_schema=False)
async def versioned_metrics():
    return await metrics_endpoint()


@app.get("/api/v1/incidents", response_model=List[CurrentIssue])
async def versioned_incidents():
    return await get_current_issues()


@app.get("/api/v1/actions", response_model=List[PendingAction])
async def versioned_actions():
    return await get_pending_approvals()


@app.post("/api/v1/actions/{action_id}/approve", response_model=ApproveRejectResponse)
async def versioned_approve(
    action_id: str,
    authorization: str | None = Header(default=None),
):
    _require_api_token(authorization)
    return await approve_action(action_id)


@app.post("/api/v1/actions/{action_id}/reject", response_model=ApproveRejectResponse)
async def versioned_reject(
    action_id: str,
    authorization: str | None = Header(default=None),
):
    _require_api_token(authorization)
    return await reject_action(action_id)


@app.post("/api/v1/alerts/grafana", response_model=AlertIngestResponse)
async def ingest_grafana_alert(
    alert: GrafanaAlert,
    x_grafana_webhook_secret: str | None = Header(default=None),
):
    """Accept an authenticated Grafana alert without granting execution rights."""
    _require_grafana_secret(x_grafana_webhook_secret)
    fingerprint = alert.fingerprint or alert.rule_uid or alert.title or alert.message
    if not fingerprint:
        raise HTTPException(status_code=422, detail="Alert fingerprint or rule_uid is required")

    if alert.status.lower() == "resolved":
        _grafana_fingerprints.discard(fingerprint)
        return AlertIngestResponse(
            accepted=True,
            fingerprint=fingerprint,
            message="Resolved alert acknowledged",
        )

    if fingerprint in _grafana_fingerprints:
        return AlertIngestResponse(
            accepted=False,
            fingerprint=fingerprint,
            message="Duplicate alert suppressed",
        )

    _grafana_fingerprints.add(fingerprint)
    namespace = alert.labels.get("namespace", "unknown")
    pod_name = alert.labels.get("pod", alert.labels.get("workload", "unknown"))
    issue = {
        "symptom": {
            "namespace": namespace,
            "pod_name": pod_name,
            "container_name": alert.labels.get("container"),
            "failure_type": alert.labels.get("failure_type", "Unknown"),
            "message": alert.message or alert.title or "Grafana alert",
            "logs": None,
            "restart_count": 0,
            "last_state": {},
            "events": [],
        },
        "diagnosis": {
            "root_cause": alert.annotations.get("summary", alert.title or "Grafana alert"),
            "remediation_type": "no_action",
            "remediation_params": {},
            "risk_level": "high",
            "confidence": 0.0,
            "reasoning": "Awaiting agent diagnosis from Grafana alert context",
            "requires_approval": True,
            "diagnosed_by": "grafana",
        },
        "suggested_fix": "no_action",
        "risk_level": "high",
        "requires_approval": True,
    }
    await issue_cache.add_issue(issue)
    await manager.broadcast({
        "event_type": "incident_detected",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "resource": {"namespace": namespace, "kind": "Pod", "name": pod_name},
        "severity": "high",
        "payload": {"fingerprint": fingerprint, "source": "grafana"},
    })
    return AlertIngestResponse(
        accepted=True,
        fingerprint=fingerprint,
        message="Alert accepted and queued for diagnosis",
    )

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


# New API Endpoints for Observability Dashboard

@app.get("/api/cluster-overview", response_model=ClusterOverview)
async def get_cluster_overview():
    """Get cluster overview with nodes, namespaces, and pod/deployment summary."""
    try:
        cluster_state = await get_cluster_state()

        # Convert to ClusterOverview model
        nodes = [NodeInfo(**node) for node in cluster_state.get("nodes", [])]
        namespaces = [NamespaceSummary(**ns) for ns in cluster_state.get("namespaces", [])]
        pod_summary = cluster_state.get("pod_summary", {})
        deployment_summary = cluster_state.get("deployment_summary", {})

        return ClusterOverview(
            nodes=nodes,
            namespaces=namespaces,
            pod_summary=pod_summary,
            deployment_summary=deployment_summary,
            timestamp=datetime.utcnow().isoformat() + "Z"
        )
    except Exception as e:
        logger.error("Failed to get cluster overview", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve cluster overview")


@app.get("/api/namespace/{namespace}/pods", response_model=List[PodInfo])
async def get_namespace_pods(namespace: str):
    """Get all pods in a namespace with their status and resource info."""
    try:
        from kubernetes import client, config as k8s_config

        settings = get_settings()
        if settings.kubeconfig_path:
            k8s_config.load_kube_config(config_file=settings.kubeconfig_path)
        else:
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()

        v1 = client.CoreV1Api()
        pods = v1.list_namespaced_pod(namespace=namespace)

        result = []
        for pod in pods.items:
            # Calculate ready containers
            container_statuses = pod.status.container_statuses or []
            ready_count = sum(1 for cs in container_statuses if cs.ready)
            total_containers = len(container_statuses)

            # Get restart count
            restart_count = sum(cs.restart_count or 0 for cs in container_statuses)

            # Get resource requests/limits from containers
            resources = None
            if pod.spec.containers:
                first_container = pod.spec.containers[0]
                if first_container.resources:
                    resources = {
                        "cpu_request": first_container.resources.requests.get("cpu") if first_container.resources.requests else None,
                        "cpu_limit": first_container.resources.limits.get("cpu") if first_container.resources.limits else None,
                        "memory_request": first_container.resources.requests.get("memory") if first_container.resources.requests else None,
                        "memory_limit": first_container.resources.limits.get("memory") if first_container.resources.limits else None,
                    }

            # Calculate age
            age_str = ""
            if pod.metadata.creation_timestamp:
                age = datetime.utcnow() - pod.metadata.creation_timestamp.replace(tzinfo=None)
                days = age.days
                hours = age.seconds // 3600
                minutes = (age.seconds % 3600) // 60
                if days > 0:
                    age_str = f"{days}d{hours}h"
                elif hours > 0:
                    age_str = f"{hours}h{minutes}m"
                else:
                    age_str = f"{minutes}m"

            pod_info = PodInfo(
                name=pod.metadata.name,
                namespace=namespace,
                status=pod.status.phase or "Unknown",
                ready=f"{ready_count}/{total_containers}",
                restart_count=restart_count,
                age=age_str,
                node=pod.spec.node_name,
                resources=resources or {},
                container_statuses=[
                    {
                        "name": cs.name,
                        "ready": cs.ready,
                        "restart_count": cs.restart_count or 0,
                        "state": cs.state.to_dict() if cs.state else {}
                    }
                    for cs in container_statuses
                ]
            )
            result.append(pod_info)

        return result
    except Exception as e:
        logger.error("Failed to get namespace pods", namespace=namespace, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to retrieve pods for namespace {namespace}")


@app.get("/api/pod/{namespace}/{pod_name}/details", response_model=PodDetails)
async def get_pod_details(namespace: str, pod_name: str):
    """Get detailed information about a specific pod."""
    try:
        from kubernetes import client, config as k8s_config

        settings = get_settings()
        if settings.kubeconfig_path:
            k8s_config.load_kube_config(config_file=settings.kubeconfig_path)
        else:
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()

        v1 = client.CoreV1Api()
        pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)

        # Get pod info
        container_statuses = pod.status.container_statuses or []
        ready_count = sum(1 for cs in container_statuses if cs.ready)
        restart_count = sum(cs.restart_count or 0 for cs in container_statuses)

        age_str = ""
        if pod.metadata.creation_timestamp:
            age = datetime.utcnow() - pod.metadata.creation_timestamp.replace(tzinfo=None)
            days = age.days
            hours = age.seconds // 3600
            minutes = (age.seconds % 3600) // 60
            if days > 0:
                age_str = f"{days}d{hours}h"
            elif hours > 0:
                age_str = f"{hours}h{minutes}m"
            else:
                age_str = f"{minutes}m"

        pod_info = PodInfo(
            name=pod.metadata.name,
            namespace=namespace,
            status=pod.status.phase or "Unknown",
            ready=f"{ready_count}/{len(container_statuses)}",
            restart_count=restart_count,
            age=age_str,
            node=pod.spec.node_name,
            container_statuses=[
                {
                    "name": cs.name,
                    "ready": cs.ready,
                    "restart_count": cs.restart_count or 0,
                    "state": str(cs.state) if cs.state else ""
                }
                for cs in container_statuses
            ]
        )

        # Get pod logs
        logs = ""
        if pod.status.container_statuses:
            container = pod.status.container_statuses[0]
            if container.name:
                try:
                    logs = v1.read_namespaced_pod_log(
                        name=pod_name,
                        namespace=namespace,
                        container=container.name,
                        tail_lines=50
                    )
                except Exception as e:
                    logger.warning("Failed to fetch pod logs", pod=pod_name, error=str(e))
                    logs = f"Failed to fetch logs: {str(e)}"

        # Get pod events
        events = []
        try:
            event_list = v1.list_namespaced_event(
                namespace=namespace,
                field_selector=f"involvedObject.name={pod_name}"
            )
            events = [
                {
                    "type": event.type,
                    "reason": event.reason,
                    "message": event.message,
                    "timestamp": event.last_timestamp.isoformat() if event.last_timestamp else ""
                }
                for event in event_list.items
            ]
        except Exception as e:
            logger.warning("Failed to fetch pod events", pod=pod_name, error=str(e))

        # Get last error
        last_error = None
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                if cs.state and cs.state.terminated:
                    last_error = f"Container {cs.name} terminated: {cs.state.terminated.reason}"
                    break
                elif cs.state and cs.state.waiting:
                    last_error = f"Container {cs.name} waiting: {cs.state.waiting.reason}"
                    break

        return PodDetails(
            pod_info=pod_info,
            logs=logs,
            events=events,
            last_error=last_error
        )
    except Exception as e:
        logger.error("Failed to get pod details", namespace=namespace, pod=pod_name, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to retrieve pod details")


@app.get("/api/recent-issues", response_model=RecentIssuesResponse)
async def get_recent_issues(
    limit: int = 20,
    namespace: Optional[str] = None,
    severity: Optional[str] = None,
    hours: int = 24
):
    """Get recent issues from audit log (diagnosis, remediation, denials)."""
    try:
        audit_logger = get_audit_logger()
        issues = []

        if not audit_logger.audit_log_path.exists():
            return RecentIssuesResponse(
                issues=[],
                total_count=0,
                timestamp=datetime.utcnow().isoformat() + "Z"
            )

        # Read audit log and parse entries
        cutoff_time = datetime.utcnow().timestamp() - (hours * 3600)

        with open(audit_logger.audit_log_path, "r") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())

                    # Parse timestamp
                    timestamp_str = entry.get("timestamp", "")
                    try:
                        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00")).timestamp()
                    except:
                        timestamp = 0

                    # Skip if outside time range
                    if timestamp < cutoff_time:
                        continue

                    event_type = entry.get("event_type", "")

                    # Only include relevant event types
                    if event_type not in ["diagnosis_completed", "remediation_executed", "remediation_skipped", "authorization_denied"]:
                        continue

                    diagnosis_data = entry.get("diagnosis", {})
                    symptom_data = diagnosis_data.get("symptom", {})
                    result_data = entry.get("result", {})

                    ns = symptom_data.get("namespace", "")

                    # Apply namespace filter if specified
                    if namespace and ns != namespace:
                        continue

                    # Determine risk level
                    risk_level = diagnosis_data.get("risk_level", "unknown")
                    if isinstance(risk_level, dict):
                        risk_level = risk_level.get("value", "unknown")

                    # Apply severity filter if specified
                    if severity and risk_level != severity:
                        continue

                    # Determine decision/success
                    decision = None
                    success = None
                    if event_type == "remediation_executed":
                        decision = "EXECUTED"
                        success = result_data.get("success", False)
                    elif event_type == "remediation_skipped":
                        decision = "PENDING_APPROVAL"
                    elif event_type == "authorization_denied":
                        decision = "DENIED"
                    elif event_type == "diagnosis_completed":
                        decision = "DIAGNOSED"

                    # Create RecentIssue object
                    issue = RecentIssue(
                        timestamp=timestamp_str,
                        event_type=event_type,
                        namespace=ns,
                        pod_name=symptom_data.get("pod_name", ""),
                        container_name=symptom_data.get("container_name"),
                        failure_type=symptom_data.get("failure_type"),
                        root_cause=diagnosis_data.get("root_cause"),
                        remediation_type=diagnosis_data.get("remediation_type"),
                        confidence=diagnosis_data.get("confidence"),
                        risk_level=risk_level,
                        decision=decision,
                        success=success,
                        reasoning=diagnosis_data.get("reasoning"),
                        metadata=entry.get("metadata", {})
                    )
                    issues.append(issue)
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    logger.warning("Failed to parse audit log entry", error=str(e))
                    continue

        # Sort by timestamp (newest first)
        issues.sort(key=lambda x: x.timestamp, reverse=True)

        # Limit results
        total_count = len(issues)
        issues = issues[:limit]

        return RecentIssuesResponse(
            issues=issues,
            total_count=total_count,
            timestamp=datetime.utcnow().isoformat() + "Z"
        )
    except Exception as e:
        logger.error("Failed to get recent issues", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve recent issues")


# Startup event to begin generating real events
@app.on_event("startup")
async def startup_event():
    """Start background tasks when the application starts."""
    logger.info("Starting K8s Remediation Dashboard")
    # Start the background task for emitting cluster state and pending actions
    asyncio.create_task(emit_cluster_state_and_pending_actions())
    logger.info("Cluster state and pending actions emitter started")