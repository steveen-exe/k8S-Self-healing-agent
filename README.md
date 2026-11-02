# Kubernetes Remediation Agent with Dashboard

A guardrailed Kubernetes self-healing agent that automatically detects, diagnoses, and remediates cluster failure conditions, paired with an embedded FastAPI web dashboard for real-time monitoring and human oversight.

## Core Safety Contract

> **Diagnosis** proposes an action → **Policy Engine** evaluates authorization → **Human Approval** (if required) marks action executable → **Executor** executes action → **Audit Log & Dashboard** record actual result.

## Features

- **Automatic Detection**: Watches Kubernetes pods in specified namespaces for CrashLoopBackOff, ImagePullBackOff, and OOMKilled errors.
- **Deduplication & Cooldown**: Intelligent symptom deduplication prevents alert storms and duplicate pending approvals.
- **Intelligent Diagnosis**: Fast rule-based diagnosis with LLM fallback capabilities.
- **Policy-Based Control**: Thread-safe policy engine supporting risk scoring, rate limiting, and explicit approval states (`pending`, `approved`, `claimed`, `rejected`, `completed`, `failed`).
- **Execution Engine**: Executes approved actions with safe defaults (`dry_run_only: true` by default).
- **Audit Logging**: Structured JSONL audit logging of all diagnoses, policy decisions, approvals, and execution results.
- **Durable Action State**: SQLite-backed approval and execution state survives agent restarts and prevents duplicate claims.
- **Prometheus Metrics**: `/metrics` exposes remediation counters and outcomes for Grafana dashboards.
- **Real-Time Dashboard**: Embedded FastAPI dashboard with WebSocket broadcasting, polling, and interactive approval controls.

## Installation & Setup

Set up a virtual environment and install all dependencies in editable mode:

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install project with dev dependencies
pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
```

## Running the Agent

Start the agent (the embedded dashboard starts automatically on port 8000):

```bash
# Activate venv
source venv/bin/activate

# Run agent with PYTHONPATH
PYTHONPATH=src python3 src/agent.py
```

Access the dashboard at `http://localhost:8000`.

## Running Tests

Run the complete test suite:

```bash
# Activate venv
source venv/bin/activate

# Run test runner
bash test.sh
```

Or run pytest directly:

```bash
PYTHONPATH=src pytest -xvs tests/test_diagnosis_policy_flow.py test_dashboard.py
```

## Dashboard API Endpoints

- `GET /` - Serves interactive HTML dashboard
- `GET /health` - Health check status
- `GET /metrics` - Prometheus metrics for Grafana scraping
- `GET /api/v1/incidents` - Versioned incident view for Grafana
- `GET /api/v1/actions` - Versioned pending-action view for Grafana
- `POST /api/v1/alerts/grafana` - Authenticated Grafana alert ingestion
- `POST /api/v1/actions/{action_id}/approve` - Authenticated operator approval
- `POST /api/v1/actions/{action_id}/reject` - Authenticated operator rejection
- `GET /ws` - WebSocket endpoint for real-time event updates
- `GET /api/pending-approvals` - List actions requiring operator approval
- `GET /api/current-issues` - Show cached active symptoms (maintained by agent)
- `GET /api/historical-actions` - Retrieve past audit log entries
- `POST /api/approve/{action_id}` - Approve pending action (emits `approval_granted`)
- `POST /api/reject/{action_id}` - Reject pending action (emits `approval_rejected`)

## Approval Lifecycle

1. **Detection**: Watcher detects failing pod → creates `Symptom`.
2. **Diagnosis**: Engine generates `Diagnosis` with remediation parameters and risk level.
3. **Policy Evaluation**: If action requires approval, policy stores pending action with `action_id`.
4. **Dashboard Approval**: Operator clicks Approve in dashboard → API updates state to `approved` and emits `approval_granted`.
5. **Execution Claim**: Main agent loop claims approved action exactly once via atomic `claim_approved_action()`.
6. **Execution**: Executor runs remediation against Kubernetes cluster.
7. **Result Broadcast**: Emits `fix_applied` on success or `fix_failed` on error, and appends to audit log.

## Environment Variables & Configuration

Configuration is loaded from `config/agent.yaml` or environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Key for Anthropic LLM diagnosis fallback | Optional |
| `OMNIROUTE_API_KEY` | Key for OmniRoute multi-model API | Optional |
| `OMNIROUTE_BASE_URL` | OmniRoute endpoint URL | Optional |
| `KUBECONFIG` / `kubeconfig_path` | Path to kubeconfig file | In-cluster config |

The default action workflow is persisted at `logs/actions.sqlite3`. Keep
`policy.dry_run_only: true` until live remediation has been validated in a
dedicated namespace.

The versioned operator API requires `REMEDIATION_API_TOKEN`, and the Grafana
webhook requires `GRAFANA_WEBHOOK_SECRET`. Configure both through a secret
manager or deployment environment; do not commit their values.

## Grafana and Prometheus

The repository includes a Docker Compose integration in
[`deploy/grafana/`](deploy/grafana/). Start the agent first so Prometheus can
scrape `http://localhost:8000/metrics`, then start Grafana and Prometheus:

```bash
source venv/bin/activate
PYTHONPATH=src python3 src/agent.py

cd deploy/grafana
GRAFANA_ADMIN_PASSWORD='use-a-new-local-password' docker compose up -d
```

Open Grafana at `http://localhost:3000`. The `Kubernetes Remediation Agent`
dashboard and Prometheus datasource are provisioned automatically.

This integration provides observability only. Approval actions still go
through the authenticated agent API; Grafana does not receive Kubernetes
credentials and does not execute cluster mutations directly.
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