"""FastAPI application for the Kubernetes remediation dashboard."""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from typing import List, Dict, Optional
import json
from pathlib import Path
from datetime import datetime
import structlog

from k8s_agent.audit import AuditLogger
from k8s_agent.watcher import PodWatcher
from k8s_agent.diagnosis.engine import DiagnosisEngine
from k8s_agent.policy import PolicyEngine
from k8s_agent.config import Settings
from k8s_agent.dashboard.models import HistoricalAction, CurrentIssue, PendingAction, ApproveRejectResponse

logger = structlog.get_logger()

app = FastAPI(title="K8s Remediation Dashboard", version="0.1.0")


@app.get("/health")
async def health_check():
    """Health check endpoint for the dashboard."""
    return {"status": "healthy", "service": "k8s-remediation-dashboard"}


# Shared instances from main agent
shared_policy_engine: Optional[PolicyEngine] = None
shared_settings: Optional[Settings] = None
shared_audit_logger: Optional[AuditLogger] = None


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
                            remediation_type=diagnosis_data.get("remediation_type", {}).value if isinstance(diagnosis_data.get("remediation_type"), dict) and hasattr(diagnosis_data.get("remediation_type"), 'value') else diagnosis_data.get("remediation_type"),
                            outcome="success" if result_data.get("success", False) else "failed",
                            details=entry.get("metadata", {})
                        )
                        actions.append(action)
                    except (json.JSONDecodeError, KeyError, AttributeError) as e:
                        logger.warning("Failed to parse audit line", error=str(e), line=line[:100])
                        continue

        # Return most recent first
        return list(reversed(actions))
    except Exception as e:
        logger.error("Failed to get historical actions", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve historical actions")


@app.get("/api/current-issues", response_model=List[CurrentIssue])
async def get_current_issues():
    """Get current issues from the watcher/diagnosis engine."""
    try:
        watcher = get_watcher()
        diagnosis_engine = get_diagnosis_engine()
        policy_engine = get_policy_engine()

        issues = []

        # Get current symptoms from watcher (non-blocking check)
        # We'll get a limited number of symptoms to avoid blocking
        symptoms_checked = 0
        max_symptoms_to_check = 10

        for symptom in watcher.watch_pods():
            if symptoms_checked >= max_symptoms_to_check:
                break

            symptoms_checked += 1
            try:
                diagnosis = diagnosis_engine.diagnose(symptom)
                if diagnosis:
                    authorized, reason = policy_engine.authorize(diagnosis)
                    issue = CurrentIssue(
                        symptom={
                            "pod_name": symptom.pod_name,
                            "namespace": symptom.namespace,
                            "failure_type": symptom.failure_type.value if hasattr(symptom.failure_type, 'value') else str(symptom.failure_type)
                        },
                        diagnosis={
                            "root_cause": diagnosis.root_cause,
                            "confidence": diagnosis.confidence,
                            "remediation_type": diagnosis.remediation_type.value if hasattr(diagnosis.remediation_type, 'value') else str(diagnosis.remediation_type)
                        },
                        suggested_fix=f"Apply {diagnosis.remediation_type.value if hasattr(diagnosis.remediation_type, 'value') else str(diagnosis.remediation_type)} remediation",
                        risk_level=diagnosis.risk_level.value if hasattr(diagnosis.risk_level, 'value') else str(diagnosis.risk_level),
                        requires_approval=diagnosis.requires_approval
                    )
                    issues.append(issue)
            except Exception as e:
                logger.warning("Failed to diagnose symptom", error=str(e), symptom=getattr(symptom, 'pod_name', 'unknown'))
                continue

        return issues
    except Exception as e:
        logger.error("Failed to get current issues", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve current issues")


@app.get("/api/pending-approvals", response_model=List[PendingAction])
async def get_pending_approvals():
    """Get actions pending human approval."""
    try:
        policy_engine = get_policy_engine()
        pending_actions_data = policy_engine.get_pending_actions()

        pending_actions = []
        for action_data in pending_actions_data:
            # Convert internal format to PendingAction model
            action = PendingAction(
                action_id=action_data["action_id"],
                symptom=action_data["symptom"],
                diagnosis=action_data["diagnosis"],
                suggested_remediation=action_data["suggested_remediation"],
                risk_level=action_data["risk_level"],
                timestamp=action_data["timestamp"]
            )
            pending_actions.append(action)

        return pending_actions
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
            logger.info("Action approved via dashboard", action_id=action_id)
            return ApproveRejectResponse(
                success=True,
                message="Action approved successfully",
                action_id=action_id
            )
        else:
            raise HTTPException(status_code=404, detail="Action not found or already processed")
    except Exception as e:
        logger.error("Failed to approve action", error=str(e), action_id=action_id)
        raise HTTPException(status_code=500, detail="Failed to approve action")


@app.post("/api/reject/{action_id}", response_model=ApproveRejectResponse)
async def reject_action(action_id: str):
    """Reject a pending action."""
    try:
        policy_engine = get_policy_engine()
        success = policy_engine.reject_action(action_id)
        if success:
            logger.info("Action rejected via dashboard", action_id=action_id)
            return ApproveRejectResponse(
                success=True,
                message="Action rejected successfully",
                action_id=action_id
            )
        else:
            raise HTTPException(status_code=404, detail="Action not found or already processed")
    except Exception as e:
        logger.error("Failed to reject action", error=str(e), action_id=action_id)
        raise HTTPException(status_code=500, detail="Failed to reject action")


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the main dashboard HTML."""
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>K8s Remediation Dashboard</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                margin: 0;
                padding: 20px;
                background-color: #f5f5f5;
            }
            .container {
                max-width: 1200px;
                margin: 0 auto;
            }
            h1 {
                color: #333;
                text-align: center;
                margin-bottom: 30px;
            }
            .section {
                background: white;
                border-radius: 8px;
                padding: 20px;
                margin-bottom: 20px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            h2 {
                color: #555;
                margin-top: 0;
            }
            table {
                width: 100%;
                border-collapse: collapse;
                margin-top: 10px;
            }
            th, td {
                padding: 12px;
                text-align: left;
                border-bottom: 1px solid #ddd;
            }
            th {
                background-color: #f8f9fa;
                font-weight: 600;
            }
            tr:hover {
                background-color: #f1f1f1;
            }
            .status-success { color: #28a745; font-weight: bold; }
            .status-failed { color: #dc3545; font-weight: bold; }
            .status-pending { color: #ffc107; font-weight: bold; }
            .btn {
                padding: 6px 12px;
                margin: 2px;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-size: 14px;
            }
            .btn-approve {
                background-color: #28a745;
                color: white;
            }
            .btn-reject {
                background-color: #dc3545;
                color: white;
            }
            .btn-disabled {
                background-color: #6c757d;
                color: white;
                cursor: not-allowed;
            }
            .loading {
                text-align: center;
                padding: 20px;
                color: #666;
            }
            .empty-state {
                text-align: center;
                padding: 40px;
                color: #666;
            }
            .refresh-btn {
                background-color: #007bff;
                color: white;
                padding: 8px 16px;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                margin-bottom: 20px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Kubernetes Remediation Dashboard</h1>

            <button class="refresh-btn" onclick="location.reload()">Refresh Data</button>

            <div class="section">
                <h2>Historical Actions</h2>
                <div id="historical-actions" class="loading">Loading...</div>
            </div>

            <div class="section">
                <h2>Current Issues</h2>
                <div id="current-issues" class="loading">Loading...</div>
            </div>

            <div class="section">
                <h2>Pending Approvals</h2>
                <div id="pending-approvals" class="loading">Loading...</div>
            </div>
        </div>

        <script>
            // Fetch and display historical actions
            async function loadHistoricalActions() {
                try {
                    const response = await fetch('/api/historical-actions');
                    const actions = await response.json();

                    const container = document.getElementById('historical-actions');
                    if (actions.length === 0) {
                        container.innerHTML = '<div class="empty-state">No historical actions found</div>';
                        return;
                    }

                    let html = '<table><thead><tr><th>Timestamp</th><th>Pod</th><th>Namespace</th><th>Action</th><th>Outcome</th></tr></thead><tbody>';
                    actions.forEach(action => {
                        const outcomeClass = action.outcome === 'success' ? 'status-success' : 'status-failed';
                        html += `<tr>
                            <td>${new Date(action.timestamp).toLocaleString()}</td>
                            <td>${action.pod_name || 'N/A'}</td>
                            <td>${action.namespace || 'N/A'}</td>
                            <td>${action.remediation_type || action.event_type}</td>
                            <td class="${outcomeClass}">${action.outcome}</td>
                        </tr>`;
                    });
                    html += '</tbody></table>';
                    container.innerHTML = html;
                } catch (error) {
                    document.getElementById('historical-actions').innerHTML =
                        '<div class="empty-state">Error loading historical actions</div>';
                    console.error('Error loading historical actions:', error);
                }
            }

            // Fetch and display current issues
            async function loadCurrentIssues() {
                try {
                    const response = await fetch('/api/current-issues');
                    const issues = await response.json();

                    const container = document.getElementById('current-issues');
                    if (issues.length === 0) {
                        container.innerHTML = '<div class="empty-state">No current issues found</div>';
                        return;
                    }

                    let html = '<table><thead><tr><th>Pod</th><th>Namespace</th><th>Failure Type</th><th>Root Cause</th><th>Suggested Fix</th><th>Risk Level</th><th>Requires Approval</th></tr></thead><tbody>';
                    issues.forEach(issue => {
                        const riskClass = issue.risk_level.toLowerCase() === 'low' ? 'status-success' :
                                        issue.risk_level.toLowerCase() === 'medium' ? 'status-pending' : 'status-failed';
                        const approvalText = issue.requires_approval ? 'Yes' : 'No';
                        html += `<tr>
                            <td>${issue.symptom.pod_name || 'N/A'}</td>
                            <td>${issue.symptom.namespace || 'N/A'}</td>
                            <td>${issue.symptom.failure_type || 'N/A'}</td>
                            <td>${issue.diagnosis.root_cause || 'N/A'}</td>
                            <td>${issue.suggested_fix}</td>
                            <td class="${riskClass}">${issue.risk_level}</td>
                            <td>${approvalText}</td>
                        </tr>`;
                    });
                    html += '</tbody></table>';
                    container.innerHTML = html;
                } catch (error) {
                    document.getElementById('current-issues').innerHTML =
                        '<div class="empty-state">Error loading current issues</div>';
                    console.error('Error loading current issues:', error);
                }
            }

            // Fetch and display pending approvals
            async function loadPendingApprovals() {
                try {
                    const response = await fetch('/api/pending-approvals');
                    const pending = await response.json();

                    const container = document.getElementById('pending-approvals');
                    if (pending.length === 0) {
                        container.innerHTML = '<div class="empty-state">No pending approvals</div>';
                        return;
                    }

                    let html = '<table><thead><tr><th>Action ID</th><th>Pod</th><th>Namespace</th><th>Suggested Remediation</th><th>Risk Level</th><th>Actions</th></tr></thead><tbody>';
                    pending.forEach(action => {
                        const riskClass = action.risk_level.toLowerCase() === 'low' ? 'status-success' :
                                        action.risk_level.toLowerCase() === 'medium' ? 'status-pending' : 'status-failed';
                        html += `<tr>
                            <td>${action.action_id}</td>
                            <td>${action.symptom.pod_name || 'N/A'}</td>
                            <td>${action.symptom.namespace || 'N/A'}</td>
                            <td>${JSON.stringify(action.suggested_remediation)}</td>
                            <td class="${riskClass}">${action.risk_level}</td>
                            <td>
                                <button class="btn btn-approve" onclick="approveAction('${action.action_id}')">Approve</button>
                                <button class="btn btn-reject" onclick="rejectAction('${action.action_id}')">Reject</button>
                            </td>
                        </tr>`;
                    });
                    html += '</tbody></table>';
                    container.innerHTML = html;
                } catch (error) {
                    document.getElementById('pending-approvals').innerHTML =
                        '<div class="empty-state">Error loading pending approvals</div>';
                    console.error('Error loading pending approvals:', error);
                }
            }

            // Approve action
            async function approveAction(actionId) {
                try {
                    const response = await fetch(`/api/approve/${actionId}`, { method: 'POST' });
                    const result = await response.json();
                    if (result.success) {
                        alert('Action approved successfully');
                        loadPendingApprovals(); // Refresh pending approvals
                    } else {
                        alert('Failed to approve action: ' + result.message);
                    }
                } catch (error) {
                    alert('Error approving action');
                    console.error('Error approving action:', error);
                }
            }

            // Reject action
            async function rejectAction(actionId) {
                try {
                    const response = await fetch(`/api/reject/${actionId}`, { method: 'POST' });
                    const result = await response.json();
                    if (result.success) {
                        alert('Action rejected successfully');
                        loadPendingApprovals(); // Refresh pending approvals
                    } else {
                        alert('Failed to reject action: ' + result.message);
                    }
                } catch (error) {
                    alert('Error rejecting action');
                    console.error('Error rejecting action:', error);
                }
            }

            // Load data on page load
            document.addEventListener('DOMContentLoaded', () => {
                loadHistoricalActions();
                loadCurrentIssues();
                loadPendingApprovals();

                // Refresh every 30 seconds
                setInterval(() => {
                    loadHistoricalActions();
                    loadCurrentIssues();
                    loadPendingApprovals();
                }, 30000);
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)