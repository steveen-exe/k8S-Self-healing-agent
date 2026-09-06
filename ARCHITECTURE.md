# Kubernetes Remediation Agent Architecture

## Overview

The K8s Remediation Agent is a self-healing system that automatically diagnoses and remediates Kubernetes pod failures. It operates in a clearly-separated multi-stage pipeline:

```
Pod Watcher → Symptom Detection
    ↓
Diagnosis Engine (Rules → LLM Fallback)
    ↓
Policy Engine (Risk Assessment & Authorization)
    ↓
Executor (Remediation Action)
    ↓
Audit Logger (Compliance & History)
    ↓
Dashboard (Operator Visibility)
```

## Core Design Principles

### 1. Separation of Concerns

- **Watcher**: Kubernetes observation only. No decision-making.
- **Diagnosis Engine**: Root cause analysis only. No execution authority.
- **Policy Engine**: Authorization and risk assessment only. No diagnosis.
- **Executor**: Action execution only. No policy decisions.
- **Audit**: Immutable event log for compliance.

### 2. Defense in Depth

Each component validates its inputs independently:

- LLM responses are validated for enum values, confidence ranges, and parameter bounds
- Policy engine enforces confidence thresholds independently
- Executor validates that remediation types are implemented
- No component trusts upstream output without validation

### 3. Fail Closed

When in doubt, require approval:

- Malformed LLM responses → reject, don't guess
- Invalid enum values → reject, don't coerce
- Low confidence diagnoses → require approval
- Unknown remediation types → deny, don't attempt
- Policy evaluation errors → require approval, not auto-execution

## Components

### Pod Watcher (`watcher.py`)

Monitors Kubernetes cluster for pod failures.

**Input**: Kubernetes API
**Output**: Symptom objects
**State**: None (stateless observation)

**Failure Types Detected**:
- `CrashLoopBackOff`
- `OOMKilled`
- `ImagePullBackOff`
- `Pending` (extended)
- `Unknown`

**Observation Mechanism**:
- Watches pod events in configured namespaces (default: `["default"]`)
- Tracks restart count to filter transient failures
- Minimum restart threshold: 3 (configurable via `watcher.min_restart_count_threshold`)
- Watch interval: 30 seconds (configurable)

### Diagnosis Engine (`diagnosis/engine.py`)

Two-tier diagnosis system for efficient root cause analysis.

#### Fast Path: Rule Matcher (`diagnosis/rules.py`)

Pattern-based diagnosis for common failures:

- `OOMKilled` + high memory usage → `increase_memory`
- `CrashLoopBackOff` + image errors → `no_action` (manual intervention)
- `ImagePullBackOff` + registry errors → `no_action` (manual intervention)
- Restart patterns → `restart_pod` or `no_action`

**Characteristics**:
- Deterministic (same input = same diagnosis)
- Fast (milliseconds)
- High confidence (0.85-1.0)
- Limited to known patterns
- No external API calls

**Output**: Diagnosis object or None

#### Slow Path: LLM Fallback (`diagnosis/llm_fallback.py`)

Uses NVIDIA NIM API (OpenAI-compatible) for complex diagnosis of failures not matching rules.

**Input**: 
- Pod metadata (namespace, name, container)
- Last container state (JSON)
- Recent events (last 10, JSON)
- Pod logs (last 50 lines)

**Process**:
1. Build enhanced prompt with full context
2. Request structured JSON output
3. Parse and validate response
4. Return Diagnosis object

**Validation** (Defense in Depth):
- Enum values: `remediation_type`, `risk_level` ✓
- Ranges: `confidence` (0.0-1.0), `memory_factor` (0 < x ≤ 10) ✓
- Required fields: `root_cause`, `reasoning`, `verification_steps` ✓
- Malformed JSON → reject ✓
- Missing fields → reject ✓

**Output**: Diagnosis object with:
- `root_cause` (string)
- `remediation_type` (enum)
- `remediation_params` (dict)
- `risk_level` (enum: low/medium/high)
- `confidence` (0.0-1.0)
- `reasoning` (string)
- `suggested_verification_steps` (array)
- `prevention_recommendations` (array)
- `diagnosed_by` (string: "llm_enhanced" or "rule_engine")

**LLM Configuration**:
```yaml
llm:
  model: "meta/llama-3.2-11b-vision-instruct"  # Via NVIDIA NIM
  temperature: 0.0                              # Deterministic
  max_tokens: 4096
  timeout_seconds: 30
```

### Policy Engine (`policy.py`)

Independent authorization and risk assessment. **Critical principle**: LLM confidence ≠ execution safety.

**Key Principle**: The policy engine makes the final decision about whether to execute an action, independent of the diagnosis confidence. This ensures that even high-confidence diagnoses undergo risk assessment.

#### Risk Assessment (Multi-Factor)

**Enhanced Risk Score** (0.0-1.0, weighted sum):

1. **Base Risk** (40% weight): From diagnosis
   - `LOW`: restart_pod, add_replica → 0.25
   - `MEDIUM`: scale, memory, image → 0.5
   - `HIGH`: rollback → 0.75

2. **Time of Day** (15% weight):
   - 9am-5pm UTC: 0.7 (business hours, higher risk)
   - 10pm-6am UTC: 0.3 (off-hours, lower risk)
   - Other: 0.5

3. **Frequency** (15% weight): Based on restart count
   - ≥10 restarts: 0.8
   - 5-10 restarts: 0.6
   - 3-5 restarts: 0.4
   - <3 restarts: 0.2

4. **Recent Success Rate** (20% weight): Historical action success
   - Inverse: low success rate → high risk
   - Uses last 10 actions or all if fewer

5. **Resource Impact** (10% weight): Blast radius
   - restart_pod: 0.2 (low)
   - add_replica: 0.3
   - scale/memory/image: 0.4
   - rollback: 0.7 (high)

#### Decision Logic

```
if confidence < threshold (0.7):
    DENY
if enhanced_risk ≥ 0.65:
    REQUIRE_APPROVAL
if auto_approve_low_risk && enhanced_risk < 0.35:
    ALLOW_AUTO
if rate_limited:
    DENY
else:
    ALLOW_AUTO
```

#### Policy Decision Output

Structured decision object:
- `decision`: ALLOW_AUTO | REQUIRE_APPROVAL | DENY
- `risk_score`: float (0.0-1.0)
- `risk_level`: RiskLevel (LOW/MEDIUM/HIGH)
- `reasons`: array of strings (explanation)
- `required_approval`: bool
- `policy_rule`: str (which rule triggered decision)
- `constraints`: dict (context)
- `action_id`: UUID (for approval workflow, if required)

#### Rate Limiting

- Per-minute: 10 actions (configurable)
- Per-hour: 100 actions (configurable)
- Prevents runaway remediation loops
- Fails closed (denies when exceeded)

#### Approval Workflow

**Pending Actions Tracking**:
- `_pending_actions`: Dict of actions awaiting approval
- `_approved_actions`: Dict of approved actions
- `_rejected_actions`: Dict of rejected actions

**Methods**:
- `get_pending_actions()`: Returns dict of pending actions
- `approve_action(action_id)`: Move to approved
- `reject_action(action_id)`: Move to rejected

### Executor (`executor.py`)

Applies authorized remediations to Kubernetes cluster.

#### Supported Actions

| Type | Implementation | Reversible | Blast Radius | Risk |
|------|---|---|---|---|
| `restart_pod` | Delete pod (controller recreates) | ✓ | Single pod | LOW |
| `increase_memory` | Patch deployment limits | ✓ | New pod replicas | MEDIUM |
| `scale_deployment` | Patch replica count | ✓ | Multiple pods | MEDIUM |
| `add_replica` | +1 replica | ✓ | Single pod | LOW |
| `update_image_tag` | Patch container image | ✓ | Rolling deployment | MEDIUM |
| `rollback_deployment` | kubectl rollout undo | ✓ | All pods in deployment | HIGH |
| `no_action` | None | N/A | None | NONE |

#### Validation

- Pod must exist (read check)
- Deployment must be found via owner references
- Container must exist in deployment
- Memory limits must be defined
- Parameters must be in valid range

#### Dry-Run Support

- All actions support `--dry-run` flag
- Returns what would be executed
- No actual changes to cluster
- Output prefixed with `[DRY RUN]`

#### Error Handling

- Pod not found → error with context
- Deployment not found → error with context
- Container not found → error with context
- API errors → caught, logged, fail closed

**Kubernetes APIs Used**:
- `CoreV1Api`: Pod operations
- `AppsV1Api`: Deployment, ReplicaSet operations

### Audit Logger (`audit.py`)

Immutable event log for SIEM integration and compliance.

**Events**:
- `diagnosis_completed`: Diagnosis generated
- `remediation_executed`: Action applied
- `remediation_skipped`: Action skipped (approval pending)
- `authorization_denied`: Policy rejected action
- `authorization_approved`: Manual approval granted
- `authorization_rejected`: Manual approval denied

**Output Format**: JSON lines (one entry per line)

**Fields**:
```json
{
  "event_type": "remediation_executed",
  "timestamp": "2026-09-06T15:00:00Z",
  "diagnosis": { /* full diagnosis object */ },
  "result": { /* full result object */ },
  "approved_by": "operator@example.com",
  "metadata": {
    "pod": "test-pod-abc123",
    "namespace": "default",
    "action_id": "uuid"
  }
}
```

**Storage**:
- File-based: `/var/log/k8s-agent/audit.jsonl`
- Configurable via `audit_log_path`

### Dashboard (`dashboard/api.py`)

Web UI for operators to manage remediations and approvals.

**Features**:
- View current issues and diagnoses
- See pending approvals with full context
- Approve/reject actions
- Review historical actions
- Monitor remediation metrics and trends

**Real-Time Updates**: WebSocket-based push for live events

**API Endpoints** (typical):
- `GET /api/issues`: Current issues
- `GET /api/pending-approvals`: Pending actions
- `POST /api/approve/{action_id}`: Approve action
- `POST /api/reject/{action_id}`: Reject action
- `GET /api/history`: Historical remediations
- `GET /api/metrics`: Remediation metrics

## Remediation Types Deep Dive

### restart_pod

**Use Case**: Transient failures, memory leaks, stuck processes

**Risk**: LOW

**Reversibility**: ✓

**Parameters**: None

**Verification**: 
- Pod status returns Running
- No immediate restart

**Implementation**:
```python
v1.delete_namespaced_pod(pod_name, namespace)
# Kubernetes controller recreates the pod
```

### increase_memory

**Use Case**: OOMKilled with high memory usage

**Risk**: MEDIUM

**Reversibility**: ✓

**Parameters**: 
- `memory_increase_factor`: 1.0-10.0 (default 1.5)

**Validation**:
- Current limits must exist
- New value must be reasonable (≤ 10x original)
- Factor must be in range (0, 10]

**Implementation**:
```python
# Parse current memory (e.g., "512Mi")
new_memory = scale_memory("512Mi", 1.5)  # "768Mi"
# Patch deployment container limits
```

### update_image_tag

**Use Case**: ImagePullBackOff after bad deployment

**Risk**: MEDIUM

**Reversibility**: ✓

**Parameters**:
- `new_tag`: string (e.g., "v1.2.3" or "latest")

**Validation**:
- Container must exist in deployment
- Tag format not validated (cluster validates)

**Implementation**:
```python
current_image = "registry.io/app:broken"
new_image = f"{base_image}:{new_tag}"  # "registry.io/app:v1.2.3"
# Patch deployment container image
```

### rollback_deployment

**Use Case**: Deployment-related issues after recent updates

**Risk**: HIGH

**Reversibility**: ✓

**Parameters**: None

**Caveats**:
- May have data migration issues
- Requires manual review in production
- Requires approval by default

**Implementation**:
```python
subprocess.run(
    ["kubectl", "rollout", "undo", f"deployment/{name}"],
    capture_output=True
)
```

### scale_deployment

**Use Case**: Resource exhaustion, performance issues

**Risk**: MEDIUM

**Reversibility**: ✓

**Parameters**:
- `target_replicas`: integer (1-100, typical)

**Validation**:
- Must be positive integer
- Reasonable bounds (≤ 100)

**Implementation**:
```python
deployment.spec.replicas = target_replicas
apps_v1.patch_namespaced_deployment(...)
```

### add_replica

**Use Case**: Quick availability boost

**Risk**: LOW

**Reversibility**: ✓

**Parameters**: None

**Implementation**: Shorthand for `scale_deployment` with `target_replicas = current + 1`

### no_action

**Use Case**: Manual intervention needed, transient issues

**Risk**: NONE

**Reversibility**: N/A

**Reason**: Operator must handle manually (issue requires domain expertise or has unclear cause)

## Security Considerations

### Input Validation

- **Pod metadata**: Treated as untrusted (could be malicious)
- **Pod logs**: Escaped before prompt injection, logged fully
- **LLM output**: Enum validated, parameters type-checked, bounds-checked
- **Remediation params**: Validated before use (type, range, format)

### Authorization Boundaries

- LLM makes diagnosis; policy engine makes decision
- Executor only runs authorized actions
- Policy engine policy is independent of diagnosis confidence
- No capability for arbitrary command execution

### Rate Limiting

- Per-minute and per-hour limits
- Prevents runaway remediation loops
- Fails closed (denies on limit exceeded)

### Audit Trail

- All decisions logged immutably
- Approval chain tracked
- Manual rejects logged
- Failed remediations logged with error
- Operator actions recorded

## Configuration

See `config.py` for full configuration schema.

**Key Settings**:

```yaml
# Policy configuration
policy:
  dry_run_only: false                    # Audit-only mode
  auto_approve_low_risk: false           # Manual approval for all by default
  min_confidence_threshold: 0.7          # Reject low confidence diagnoses
  rate_limit:
    max_actions_per_minute: 10
    max_actions_per_hour: 100

# LLM configuration (NVIDIA NIM API)
llm:
  model: "meta/llama-3.2-11b-vision-instruct"
  temperature: 0.0                       # Deterministic
  max_tokens: 4096
  timeout_seconds: 30

# Watcher configuration
watcher:
  namespaces:
    - default
    - kube-system
  watch_interval_seconds: 30
  min_restart_count_threshold: 3         # Ignore pods with <3 restarts

# Logging
log_level: INFO
audit_log_path: /var/log/k8s-agent/audit.jsonl
```

**Environment Variables**:

```bash
# Required
ANTHROPIC_API_KEY=sk-...                 # API key (fallback provider)

# Optional
OMNIROUTE_API_KEY=...                    # NVIDIA NIM API key
OMNIROUTE_BASE_URL=...                   # NVIDIA NIM base URL
KUBECONFIG_PATH=...                      # K8s config path (default: in-cluster)
```

## Data Flow Examples

### Example 1: OOMKilled Pod (Fast Path)

```
1. Watcher detects pod with OOMKilled status, 5 restarts
   ↓
2. Rule Engine matches pattern → HIGH memory usage detected
   ↓
3. Diagnosis: increase_memory (1.5x), confidence=0.9, risk=MEDIUM
   ↓
4. Policy Engine: enhanced_risk=0.52 → REQUIRE_APPROVAL
   ↓
5. Dashboard shows pending approval
   ↓
6. Operator reviews: "Yes, out of memory. Approve."
   ↓
7. Executor patches deployment: "512Mi" → "768Mi"
   ↓
8. New pod starts with increased memory
```

### Example 2: ImagePullBackOff (Slow Path with LLM)

```
1. Watcher detects ImagePullBackOff, 2 restarts (below threshold, but persistent)
   ↓
2. Rule Engine: no match (transient, not in rules)
   ↓
3. LLM Fallback queries API with pod context
   ↓
4. LLM Response (validated):
   {
     "root_cause": "Invalid registry credentials or network issue",
     "remediation_type": "no_action",
     "confidence": 0.75,
     "risk_level": "low",
     "reasoning": "Network/credential issue requires manual investigation"
   }
   ↓
5. Policy Engine: dry_run_only=true → DENY
   ↓
6. Audit log: "diagnosis_completed, no_action, requires_manual_intervention"
```

### Example 3: Rate Limited

```
1. Watcher detects pod failures across cluster
   ↓
2-6. First diagnosis → policy → execution (normal flow)
   ↓
7. Second diagnosis arrives while rate limit counter active
   ↓
8. Policy Engine: rate_limit_check() → DENY "Rate limit exceeded"
   ↓
9. Audit log: "authorization_denied, rate_limit_exceeded"
   ↓
10. Operator dashboard shows: "Rate limited. 10/10 actions used this minute."
```

## Testing Strategy

### Unit Tests

- **LLM Response Parsing**: Valid/invalid JSON, enum validation, bounds checking
- **Policy Decisions**: Confidence checks, risk scoring, rate limits, approval workflow
- **Executor Actions**: Dry-run mode, parameter validation, error handling

### Integration Tests

- **Full Flow**: Diagnosis → policy → execution
- **Approval Workflows**: Pending → approved → executed
- **Error Recovery**: API failures, malformed responses, edge cases

### Production Testing

- **Dry-Run Mode**: `policy.dry_run_only: true` (no execution, audit only)
- **Manual Approval**: `policy.auto_approve_low_risk: false` (all require approval)
- **Limited Namespaces**: `watcher.namespaces: ["test"]` (isolated testing)

## Failure Modes and Recovery

### LLM Unavailable

- **Behavior**: Rule matcher still diagnoses common failures
- **Complex Failures**: No diagnosis, logged as warning
- **Recovery**: Automatic retries, fallback to manual approval

### Policy Engine Error

- **Behavior**: Action denied by default (fail closed)
- **Logging**: Error details included in audit
- **Recovery**: Operator reviews logs, may manually remediate

### Executor Error

- **Behavior**: Action logged as failed
- **Logging**: Error details and stack trace in audit
- **Recovery**: Operator reviews logs, may attempt different remediation

### Rate Limited

- **Behavior**: Action denied, reason logged
- **Alerting**: Operator notified via dashboard
- **Recovery**: Wait for rate limit window, or increase limits

## Monitoring and Observability

### Key Metrics

- **Diagnosis Confidence**: Histogram of LLM confidence scores
- **Policy Decisions**: Breakdown of ALLOW_AUTO vs REQUIRE_APPROVAL vs DENY
- **Remediation Success**: Rate by type, trend over time
- **LLM Response Time**: Percentiles (p50, p95, p99)
- **Action Latency**: Detection → authorization → execution

### Alerting

- **Policy Engine Denials**: Possible misconfiguration
- **LLM Errors**: API availability issues
- **Executor Failures**: Cluster API issues
- **Rate Limit Exhaustion**: Too many issues detected

### Logs

- **Format**: Structured JSON via structlog
- **Output**: File-based + stdout (configurable)
- **SIEM Integration**: Audit log in JSON lines format
- **Separation**: Operational logs separate from audit logs

**Structured Log Example**:
```json
{
  "timestamp": "2026-09-06T15:00:00Z",
  "level": "info",
  "event": "remediation_authorized",
  "pod": "app-xyz-123",
  "namespace": "default",
  "remediation_type": "increase_memory",
  "risk_level": "medium",
  "confidence": 0.85,
  "diagnosed_by": "llm_enhanced"
}
```

## Future Enhancements

1. **Multi-Cluster Support**: Manage multiple K8s clusters
   - Cluster-specific policies
   - Cross-cluster awareness

2. **Custom Policies**: Per-namespace or per-resource policies
   - Resource-specific thresholds
   - Team-level approval requirements

3. **Historical Analysis**: Learn from past remediation outcomes
   - Feedback loop: did the fix work?
   - Success rate tracking per remediation type

4. **Predictive Diagnostics**: Anticipate failures before they occur
   - Trend analysis
   - Anomaly detection

5. **Custom Remediation**: Support user-defined remediation types
   - Webhook integration
   - Custom scripts/operators

6. **Webhook Integration**: External policy engines (OPA, etc.)
   - Enterprise policy integration
   - Custom authorization logic

7. **Metrics Export**: Prometheus metrics endpoint
   - Standard monitoring integration
   - Grafana dashboards

## Project Structure

```
/home/user/my-ai-agent/
├── src/
│   ├── k8s_agent/
│   │   ├── __init__.py
│   │   ├── types.py              # Core domain types
│   │   ├── config.py             # Configuration management
│   │   ├── llm_client.py         # NVIDIA NIM API client
│   │   ├── watcher.py            # Pod watcher
│   │   ├── policy.py             # Policy engine
│   │   ├── executor.py           # Remediation executor
│   │   ├── audit.py              # Audit logger
│   │   ├── diagnosis/
│   │   │   ├── __init__.py
│   │   │   ├── engine.py         # Diagnosis coordinator
│   │   │   ├── rules.py          # Pattern-based rules
│   │   │   └── llm_fallback.py   # LLM fallback
│   │   └── dashboard/
│   │       ├── __init__.py
│   │       ├── api.py            # Web API
│   │       ├── models.py         # Dashboard data models
│   │       └── static/           # Frontend assets
│   └── agent.py                  # Main agent entry point
├── tests/
│   ├── __init__.py
│   └── test_diagnosis_policy_flow.py  # Integration tests
├── ARCHITECTURE.md               # This document
├── config.yaml                   # Configuration example
└── requirements.txt              # Python dependencies
```

## References

- **Kubernetes Python Client**: https://github.com/kubernetes-client/python
- **NVIDIA NIM API**: https://docs.nvidia.com/cloud/nim/
- **Pydantic**: https://docs.pydantic.dev/
- **Structlog**: https://www.structlog.org/
