# Dashboard/Ticketing System Plan for Kubernetes Remediation Agent

## Overview
Based on the user's requirements and codebase analysis, we need to create a dashboard that:
1. Shows historical actions performed by the model (from audit logs)
2. Displays current tickets/issues from the cluster (via agent's internal state/logs)
3. Shows suggested fixes
4. Provides an approve button for human review before execution
5. Runs as an embedded service within the existing agent
6. Uses Python for development (likely with FastAPI for the backend and simple HTML/JS for frontend)

## Architecture Decision
Since the user wants:
- Python development
- Embedded in the agent
- Pull data from agent's internal state/logs

We'll extend the existing agent with:
1. A FastAPI web server that runs alongside the main watch loop
2. Endpoints to serve dashboard data (historical actions, current issues)
3. A simple HTML/JS frontend that polls the API
4. Approve/reject endpoints that interact with the policy engine

## Implementation Plan

### Phase 1: Core Infrastructure
1. Add FastAPI dependencies to pyproject.toml
2. Create dashboard module with FastAPI app
3. Add HTTP server startup to main agent loop (runs in background thread)
4. Create basic endpoints for health check

### Phase 2: Data Endpoints
1. `/api/historical-actions` - Read from audit log file
2. `/api/current-issues` - Get from watcher/diagnosis engine (current failing pods)
3. `/api/pending-approvals` - Actions that require human approval
4. `/api/approve/{action_id}` - Approve a pending action
5. `/api/reject/{action_id}` - Reject a pending action

### Phase 3: Frontend
1. Simple HTML dashboard with:
   - Historical actions table
   - Current issues table
   - Pending approvals section with approve/reject buttons
2. Basic CSS for readability
3. JavaScript to poll APIs and update UI

### Phase 4: Integration
1. Modify policy engine to track pending actions
2. Add action ID generation and tracking
3. Connect approve/reject endpoints to policy engine
4. Ensure thread safety for shared state

## Detailed Component Design

### 1. Dashboard Module Structure
```
src/k8s_agent/
├── dashboard/
│   ├── __init__.py
│   ├── api.py          # FastAPI app and routes
│   ├── frontend.py     # HTML template serving
│   └── models.py       # Pydantic models for API
├── policy.py           # Enhanced to track pending actions
└── main.py             # Modified to start dashboard server
```

### 2. Key Changes Needed

#### In `src/k8s_agent/policy.py`:
- Add tracking for pending actions requiring approval
- Methods to get pending actions
- Methods to approve/reject actions by ID
- Thread-safe storage (using locks or queue)

#### In `src/k8s_agent/dashboard/api.py`:
```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import json
from pathlib import Path
from ..audit import AuditLogger
from ..watcher import PodWatcher
from ..diagnosis.engine import DiagnosisEngine
from ..policy import PolicyEngine

app = FastAPI(title="K8s Remediation Dashboard")

# Dependency injection - these would be shared with main agent
def get_settings():
    # Implementation to get shared settings
    pass

# Models
class HistoricalAction(BaseModel):
    timestamp: str
    event_type: str
    pod_name: str
    namespace: str
    remediation_type: Optional[str] = None
    outcome: str
    details: dict

class CurrentIssue(BaseModel):
    symptom: dict
    diagnosis: dict
    suggested_fix: str
    risk_level: str
    requires_approval: bool

class PendingAction(BaseModel):
    action_id: str
    symptom: dict
    diagnosis: dict
    suggested_remediation: dict
    risk_level: str
    timestamp: str

# Endpoints
@app.get("/api/historical-actions", response_model=List[HistoricalAction])
async def get_historical_actions():
    # Read from audit log file
    pass

@app.get("/api/current-issues", response_model=List[CurrentIssue])
async def get_current_issues():
    # Get current failing pods from watcher
    pass

@app.get("/api/pending-approvals", response_model=List[PendingAction])
async def get_pending_approvals():
    # Get from policy engine
    pass

@app.post("/api/approve/{action_id}")
async def approve_action(action_id: str):
    # Update policy engine to approve
    pass

@app.post("/api/reject/{action_id}")
async def reject_action(action_id: str):
    # Update policy engine to reject
    pass

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    # Serve HTML frontend
    pass
```

#### In `src/k8s_agent/main.py`:
- Import dashboard components
- Start FastAPI server in background thread when agent starts
- Share components (settings, watcher, etc.) between main loop and dashboard

### 3. Data Flow
1. Watcher detects failing pods → creates Symptom
2. DiagnosisEngine analyzes → creates Diagnosis
3. PolicyEngine checks authorization → if requires approval, adds to pending actions
4. Dashboard polls `/api/current-issues` and `/api/pending-approvals`
5. Human clicks approve → dashboard calls `/api/approve/{id}`
6. PolicyEngine approves action → main loop executes remediation
7. Executor performs action → AuditLogger logs result
8. Dashboard updates via polling

### 4. Implementation Steps

#### Step 1: Add Dependencies
Add to `pyproject.toml`:
```toml
fastapi = "^0.104.0"
uvicorn = "^0.24.0"
python-multipart = "^0.0.6"  # for form data
```

#### Step 2: Create Dashboard Module
Create the directory and files as outlined above.

#### Step 3: Enhance Policy Engine
Add pending action tracking to `policy.py`.

#### Step 4: Modify Main Agent
Update `agent.py` to start dashboard server.

#### Step 5: Create Frontend
Create simple HTML/JS dashboard.

#### Step 6: Test Integration
Verify that:
- Dashboard shows historical actions from audit log
- Dashboard shows current failing pods
- Dashboard shows pending approvals
- Approve/reject buttons work correctly
- Actions execute only after approval

## Benefits of This Approach
1. **Loose Coupling**: Dashboard runs separately but shares components
2. **Real-time**: Polling provides near-real-time updates
3. **Fallback Safe**: If dashboard fails, main agent continues
4. **Extensible**: Easy to add more endpoints/features
5. **Leverages Existing**: Uses current audit, watcher, diagnosis systems

## Estimated Effort
- Phase 1 (Infrastructure): 2-3 hours
- Phase 2 (Data Endpoints): 3-4 hours
- Phase 3 (Frontend): 2-3 hours
- Phase 4 (Integration): 2-3 hours
- Total: ~10-12 hours

## Next Steps
1. Confirm this approach matches user expectations
2. Begin implementation with Phase 1
3. Iterate with frequent testing