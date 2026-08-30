# Kubernetes Remediation Agent with Dashboard

A Kubernetes remediation agent that automatically detects and fixes common cluster issues, now with a web dashboard for monitoring and human oversight.

## Features

- **Automatic Detection**: Watches for failing pods in specified namespaces
- **Intelligent Diagnosis**: Uses LLM-assisted analysis to identify root causes
- **Policy-Based Authorization**: Configurable remediation policies with approval workflows
- **Execution Engine**: Safely executes remediation actions (with dry-run support)
- **Audit Logging**: Comprehensive logging for compliance and debugging
- **Web Dashboard**: Real-time monitoring and human approval interface

## Dashboard

The agent now includes an embedded web dashboard that provides:

- **Historical Actions**: View past remediation attempts from the audit log
- **Current Issues**: See currently failing pods being monitored
- **Pending Approvals**: Review and approve/reject actions requiring human oversight
- **Real-time Updates**: Frontend polls APIs every 30 seconds for fresh data

Access the dashboard at `http://localhost:8000` when the agent is running.

## Architecture

```
Main Agent Loop
    │
    ├── Watcher → Detects failing pods
    ├── DiagnosisEngine → Analyzes symptoms and suggests remediations
    ├── PolicyEngine → Checks authorization (with pending actions tracking)
    ├── Executor → Performs remediation actions
    └── AuditLogger → Logs all activities for compliance
        
Dashboard (FastAPI Server)
    │
    ├── GET /api/historical-actions → Reads from audit log
    ├── GET /api/current-issues → Gets current failing pods
    ├── GET /api/pending-approvals → Returns actions needing approval
    ├── POST /api/approve/{id} → Approves pending action
    ├── POST /api/reject/{id} → Rejects pending action
    └── GET / → Serves HTML/JS frontend
```

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Or install individually:
pip install fastapi uvicorn python-multipart kubernetes anthropic pyyaml structlog python-json-logger pydantic
```

## Configuration

Create `config/agent.yaml` based on the example structure. The agent will look for this file on startup.

**Note**: When using OmniRoute, the `llm.model` value must include a provider prefix (e.g., "anthropic/claude-sonnet-5-20241022"). Refer to the OmniRoute documentation for the correct format.

## Usage

```bash
# Run the agent (dashboard starts automatically)
python3 src/agent.py

# Access dashboard at http://localhost:8000
```

## API Endpoints

- `GET /` - Dashboard HTML interface
- `GET /health` - Health check
- `GET /api/historical-actions` - List past remediation actions
- `GET /api/current-issues` - Show currently monitored failing pods
- `GET /api/pending-approvals` - List actions requiring human approval
- `POST /api/approve/{action_id}` - Approve a pending action
- `POST /api/reject/{action_id}` - Reject a pending action

## Testing

Run the validation script:
```bash
python3 test_dashboard.py
```

## Memory & Persistence

Important facts and decisions can be preserved across system sessions using the Claude memory system. To store information for future reference:

Simply ask: "Remember that [specific fact you want to preserve]"

Examples:
- "Remember that I prefer using Sonnet 4 for coding tasks"
- "Remember that my Kubernetes cluster is in the 'staging' namespace"
- "Remember that I want all remediations to run in dry-run mode by default"

These memories are stored as files in:
```
/home/user/.claude-omniroute/projects/-home-user-my-ai-agent/memory/
```

And indexed in `MEMORY.md` for easy retrieval.

## Dashboard Screenshots

*(To be added)*

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

MIT