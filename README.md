# Kubernetes Remediation Agent

A guardrailed, auditable Kubernetes remediation system that watches cluster state, diagnoses failures, and executes fixes through an independent policy layer.

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Kubernetes cluster access (kubeconfig or in-cluster)
- Anthropic API key

### Installation

```bash
# Clone the repository
cd my-ai-agent

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e .

# Copy and configure environment
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

### Configuration

Edit `config/agent.yaml` to customize:

- **Namespaces to monitor** (currently: `default`)
- **Dry-run mode** (enabled by default for safety)
- **Rate limits** (5 actions/min, 20/hour)
- **Restart count threshold** (minimum 3 restarts before action)

### Run the Agent

```bash
# Start in dry-run mode (safe - no actual changes)
python src/agent.py

# The agent will:
# 1. Watch pods in configured namespaces
# 2. Detect failures (CrashLoopBackOff, ImagePullBackOff, OOMKilled)
# 3. Diagnose using rules or Claude LLM fallback
# 4. Log proposed remediations to logs/audit.jsonl
```

## 🎯 Focused Scope (MVP)

This implementation targets **3 common failure modes**:

1. **CrashLoopBackOff** - Detects OOM, panics, connection failures
2. **ImagePullBackOff** - Identifies auth issues, missing tags, timeouts  
3. **OOMKilled** - Automatically suggests memory increases

## 🏗️ Architecture

```
Watcher → Diagnosis Engine → Policy Engine → Executor → Audit Logger
            ├─ Rule Matcher (fast path)
            └─ LLM Fallback (Claude API)
```

### Components

- **[watcher.py](src/k8s_agent/watcher.py)** - Monitors pod failures via Kubernetes API
- **[diagnosis/engine.py](src/k8s_agent/diagnosis/engine.py)** - Orchestrates diagnosis
  - **[rules.py](src/k8s_agent/diagnosis/rules.py)** - Fast rule-based pattern matching
  - **[llm_fallback.py](src/k8s_agent/diagnosis/llm_fallback.py)** - Claude-powered analysis for complex cases
- **[policy.py](src/k8s_agent/policy.py)** - Authorization and rate limiting
- **[executor.py](src/k8s_agent/executor.py)** - Executes remediations (restart pod, increase memory, rollback)
- **[audit.py](src/k8s_agent/audit.py)** - Structured JSONL audit logs

## 🛡️ Safety Features

- ✅ **Dry-run first** - Enabled by default, no changes until you opt-in
- ✅ **Independent policy layer** - Authorization separate from diagnosis
- ✅ **Rate limiting** - Prevents cascading fixes (configurable)
- ✅ **Full audit trail** - Every decision logged to `logs/audit.jsonl`
- ✅ **Approval gates** - High-risk actions require manual approval
- ✅ **Rule-based fast path** - LLM only called for complex/unknown failures

## 📊 Example Output

```json
{
  "event_type": "diagnosis_completed",
  "diagnosis": {
    "symptom": {
      "namespace": "default",
      "pod_name": "myapp-7d8f9c-xkj2p",
      "failure_type": "CrashLoopBackOff",
      "restart_count": 5
    },
    "root_cause": "Container exceeded memory limit and was killed by Kubernetes",
    "remediation_type": "increase_memory",
    "remediation_params": {"memory_increase_factor": 1.5},
    "risk_level": "medium",
    "confidence": 1.0,
    "diagnosed_by": "rule_engine"
  },
  "timestamp": "2026-08-25T03:01:57.918Z"
}
```

## 🔧 Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run type checking
mypy src/

# Run linter
ruff check src/

# Run tests (when added)
pytest
```

## 📝 Configuration Reference

### Remediation Types

- `restart_pod` - Delete pod (controller recreates it)
- `increase_memory` - Scale memory limits by factor
- `rollback_deployment` - Revert to previous deployment revision
- `no_action` - Log diagnosis but take no action

### Risk Levels

- `low` - Safe, reversible (e.g., restart pod)
- `medium` - Some impact (e.g., memory changes)
- `high` - Significant impact (e.g., rollback)

## 🚦 Next Steps

1. **Test in dev cluster** - Run in dry-run mode, review audit logs
2. **Tune rules** - Adjust pattern matching in [rules.py](src/k8s_agent/diagnosis/rules.py)
3. **Enable auto-remediation** - Set `dry_run_only: false` for low-risk actions
4. **Add more failure patterns** - Extend rule matchers as needed
5. **SIEM integration** - Forward `logs/audit.jsonl` to your logging system

## ⚠️ Production Checklist

Before deploying to production:

- [ ] Test all remediation types in staging
- [ ] Configure appropriate rate limits
- [ ] Set up audit log forwarding to SIEM
- [ ] Review and adjust approval gates
- [ ] Monitor Claude API usage/costs
- [ ] Set up alerts for authorization denials
- [ ] Document incident response procedures

## 📚 Tech Stack

- **Python 3.11** with type hints
- **kubernetes** client library
- **anthropic** SDK for Claude API
- **pydantic** for configuration/validation
- **structlog** for structured logging

## 🤝 Contributing

This is an MVP implementation. Areas for improvement:

- Add comprehensive tests
- Implement more remediation types
- Add webhook for manual approval UI
- Support for StatefulSets, DaemonSets
- Prometheus metrics export
- Leader election for HA deployment

## 📄 License

MIT
