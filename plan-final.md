# Final Project Evaluation and Implementation Plan

**Project:** K8s Self-Healing Remediation Agent
**Repository:** `/home/user/my-ai-agent`
**Purpose of this file:** Handoff document for the next implementation model. This document is based on a repository-wide review of the source code, tests, launch scripts, configuration, and project documentation.

## 1. What This Project Is

This project is intended to be a guardrailed Kubernetes remediation agent. It should:

1. Watch Kubernetes pods for known failure conditions.
2. Convert failures into structured `Symptom` objects.
3. Diagnose the likely root cause using fast rule matching and an LLM fallback.
4. Propose a typed remediation such as restarting a pod, changing memory, scaling, updating an image, or rolling back a deployment.
5. Apply policy controls based on confidence, risk, rate limits, dry-run settings, and human approval requirements.
6. Execute only actions authorized by policy.
7. Log diagnoses, policy decisions, approvals, rejections, and execution outcomes for auditability.
8. Provide a FastAPI/WebSocket dashboard for current issues, historical actions, and operator approvals.

The central safety contract is:

> A diagnosis proposes an action. The policy engine decides whether it may run. An approval authorizes execution but does not itself mean execution succeeded. The executor result is the source of truth for remediation success or failure.

## 2. Existing Architecture

### Runtime

- `src/agent.py`: main watcher, diagnosis, policy, execution, audit, and dashboard startup loop.
- `src/k8s_agent/config.py`: Pydantic settings and YAML/environment configuration.
- `src/k8s_agent/watcher.py`: Kubernetes pod inspection and symptom generation.
- `src/k8s_agent/diagnosis/engine.py`: rule-first diagnosis with LLM fallback.
- `src/k8s_agent/diagnosis/rules.py`: known failure pattern rules.
- `src/k8s_agent/diagnosis/llm_fallback.py`: external LLM diagnosis path.
- `src/k8s_agent/policy.py`: confidence checks, risk scoring, rate limiting, pending approval state, and decisions.
- `src/k8s_agent/executor.py`: Kubernetes remediation operations.
- `src/k8s_agent/audit.py`: JSONL audit entries.
- `src/k8s_agent/types.py`: Pydantic domain models and enums.

### Dashboard

- `src/k8s_agent/dashboard/api.py`: FastAPI routes, WebSocket endpoint, shared dependency accessors, and approval endpoints.
- `src/k8s_agent/dashboard/models.py`: API response models.
- `src/k8s_agent/dashboard/static/index.html`: HTML shell and CDN-loaded React/Babel dependencies.
- `src/k8s_agent/dashboard/static/js/app.js`: current browser UI.
- `src/k8s_agent/dashboard/static/css/style.css`: dashboard styles.
- `run_dashboard.py`: standalone dashboard launcher.

### Tests and operations

- `tests/test_diagnosis_policy_flow.py`: diagnosis, policy, executor, and partial integration tests.
- `test_dashboard.py`: import/model/policy smoke tests, not a real API workflow suite.
- `test.sh`: currently only prints `hello`; it is not a test runner.
- `setup_minikube_test.sh`: creates a Minikube test setup but does not run the agent or verify remediation.
- `run_agent_wrapper.py`: starts `src/agent.py`, waits ten seconds, and terminates it; it is not a deterministic integration harness.

## 3. Original Intended Workflow

The desired end-to-end path is:

```text
Kubernetes pod failure
    -> PodWatcher creates Symptom
    -> DiagnosisEngine creates Diagnosis
    -> AuditLogger records diagnosis
    -> PolicyEngine returns ALLOW_AUTO, REQUIRE_APPROVAL, or DENY

ALLOW_AUTO
    -> Executor executes immediately
    -> AuditLogger records result
    -> Dashboard receives issue/execution event

REQUIRE_APPROVAL
    -> PolicyEngine stores pending action with action_id
    -> Dashboard shows action to operator
    -> Operator approves or rejects

APPROVE
    -> PolicyEngine atomically marks action executable
    -> Agent claims action exactly once
    -> Executor executes stored Diagnosis
    -> AuditLogger records actual result
    -> Dashboard receives success/failure event

REJECT
    -> Action is permanently rejected
    -> Executor is never called
    -> Dashboard receives rejection event
```

## 4. Problems Found in the Current Implementation

### P0: Approved actions never execute

`PolicyEngine.approve_action()` moves a pending action into `_approved_actions`, but there is no public claim/consume method and no agent loop that reads `_approved_actions`. The main agent skips the approval-required branch with `continue`.

Result: the dashboard can approve an action, but Kubernetes is never changed.

Required outcome: an approved action must be claimed and sent to `Executor.execute()` exactly once.

### P0: Dashboard and agent do not share the policy instance

`src/agent.py` imports `shared_policy_engine`, `shared_settings`, and `shared_audit_logger`, then assigns new local values to those imported names. This does not update the variables held by `dashboard.api`.

The API therefore falls back to constructing separate objects. Pending actions created by the agent may be invisible to the dashboard, and approval may update the wrong policy engine.

Required outcome: initialize shared runtime dependencies through the dashboard module or an explicit runtime context/configuration function.

### P0: Approval is falsely reported as execution

The approve endpoint broadcasts `fix_applied` immediately after moving the action to approved state. No executor call has occurred at that point.

Required outcome:

- Approval endpoint emits `approval_granted`.
- Executor success emits `fix_applied`.
- Executor failure emits `fix_failed`.
- The UI distinguishes approved, executing, succeeded, failed, and rejected.

### P1: Approval stores are not thread-safe

The FastAPI server and agent run in different threads but directly access pending, approved, and rejected dictionaries without locks.

Required outcome: protect state transitions and implement atomic approve/reject/claim operations. A single action must not be claimed twice.

### P1: The documented frontend is not implemented

The current `app.js` is a minimal tab shell. It opens a WebSocket, but it does not implement the documented API-backed pending approvals, historical actions, current issue loading, approval buttons, rejection buttons, execution status, or cluster resource views.

Required outcome: either implement the minimum approval dashboard or reduce documentation claims. The minimum UI must load pending actions, allow approve/reject, refresh state, and display actual execution outcomes.

### P1: Standalone dashboard semantics are unclear

`run_dashboard.py` starts FastAPI independently and cannot access the live agent's in-process policy engine. This is acceptable only as a read-only/demo dashboard unless state is moved to a shared durable service.

Required outcome: document this limitation, or make the dashboard and agent use a shared process/service architecture. For the current codebase, use in-process shared state when the embedded agent starts the dashboard.

### P1: Policy behavior is inconsistent and difficult to reason about

There are two authorization APIs, `authorize()` and `get_policy_decision()`, with duplicated pending-action creation logic. The rate-limit and approval semantics are not clearly separated. `get_policy_decision()` checks rate limits before creating an approval action, while `approve_action()` records an action before execution, and no clear single point defines when rate limits are consumed.

Required outcome: define one authoritative decision path and explicitly decide whether rate limits are consumed at authorization, approval, claim, or execution. The recommended choice is to reserve/check at execution claim and record the result after execution, while preventing repeated execution.

### P1: Default safety configuration prevents real execution

`PolicyConfig.dry_run_only` defaults to `True`. This is a good safety default, but it means a successful executor call may be a dry-run and `RemediationResult.executed` is false. Documentation must clearly distinguish simulated success from real cluster mutation.

Required outcome: preserve safe default, expose dry-run status in events/UI, and test both dry-run and real-execution paths.

### P1: Executor has unsafe or fragile resource assumptions

Several operations infer deployment names by splitting pod names. Kubernetes pod names are not a reliable deployment-to-pod mapping. Some operations read/modify resources before checking dry-run, and the rollback path shells out to `kubectl` rather than using a consistent client abstraction.

Required outcome:

- Resolve owner references consistently.
- Validate target namespace, pod, container, deployment, and parameters.
- Avoid mutating live objects in dry-run mode where possible.
- Use a controlled command/client abstraction for rollback.
- Add tests for malformed/missing owners, missing resources, invalid parameters, and dry-run side effects.

### P1: Watcher can repeatedly diagnose the same failure

`watch_pods()` lists pods on each cycle and yields any matching symptom. There is no deduplication, cooldown, or persisted symptom/action identity. A failing pod can create repeated diagnoses and repeated pending actions every watch cycle.

Required outcome: add a deduplication/cooldown policy keyed by namespace, pod, container, failure type, and relevant state; allow retries only under an explicit policy.

### P1: Dashboard current-issues endpoint performs a new cluster scan

The dashboard constructs a separate watcher and diagnosis engine for `/api/current-issues`. This can compete with the agent, repeat LLM calls, produce inconsistent results, and block an HTTP request on Kubernetes/LLM work.

Required outcome: expose agent-owned current issue state or use a bounded background refresh/cache. Do not run an independent diagnosis loop per HTTP request.

### P1: WebSocket event-loop bridging is unreliable

The agent creates or retrieves event loops from its worker thread and attempts to run dashboard coroutines directly. This is not a reliable way to submit work to the Uvicorn loop and can result in disconnected loops or missed events.

Required outcome: provide a thread-safe event queue/bridge owned by the dashboard runtime, or capture the server loop and use `asyncio.run_coroutine_threadsafe()` against that known loop.

### P1: Mutating API endpoints have no authentication or authorization

Anyone who can reach the dashboard can approve or reject remediation actions. The API also uses a generic operator identity.

Required outcome for this phase: document that the dashboard is trusted-local only and bind it safely by default. Before production exposure, add authentication, operator identity, authorization, CSRF protection as appropriate, and audit identity for approval/rejection.

### P2: Configuration and dependency issues

- `config.py` imports `BaseSettings` from `pydantic_settings`, but the dependency is not listed in `pyproject.toml` or `requirements.txt`.
- The settings require `ANTHROPIC_API_KEY` even though the project documentation discusses OmniRoute/fallback behavior. Startup behavior should be explicit when no LLM key is present and rules are sufficient.
- YAML merging uses shallow `copy(update=...)` behavior and should be tested for nested configuration correctness.
- Documentation refers to multiple entry points (`src/agent.py`, `k8s_agent.main`) that are not consistently present/aligned.

Required outcome: make dependency declarations, environment requirements, configuration precedence, and launch commands accurate and tested.

### P2: Test infrastructure is incomplete

- `test.sh` does not execute tests.
- `test_dashboard.py` is a print-based smoke script and does not test FastAPI endpoints.
- Existing approval tests verify dictionary movement but not execution.
- There are no tests for shared runtime identity, concurrency, event semantics, frontend behavior, deduplication, or launch configuration.
- Tests require `PYTHONPATH=src` unless packaging/import setup is corrected.

Required outcome: create a repeatable test command and make focused tests runnable in a clean environment.

### P2: Documentation overstates completion

Several documents describe a completed React dashboard, real-time workflow, and approval-to-execution path even though the current UI and runtime do not provide those guarantees.

Required outcome: update completion/status/architecture documents after implementation, and label deferred features honestly.

## 5. New Implementation Plan

### Phase 0: Establish a reproducible baseline

1. Add/fix the missing `pydantic-settings` dependency.
2. Make imports work through packaging or a documented test configuration.
3. Replace `test.sh` with the real test command.
4. Run current tests and record failures before functional changes.
5. Keep all existing user changes and avoid unrelated refactors.

### Phase 1: Make policy action lifecycle correct

1. Create a single internal action-record builder to remove duplicated pending-action code.
2. Add a lock protecting pending, approved, rejected, and claimed state.
3. Implement explicit states: `pending`, `approved`, `claimed`, `rejected`, `completed`, `failed`.
4. Make approval and rejection atomic and one-shot.
5. Add `claim_approved_action()` or equivalent, returning the stored action exactly once.
6. Store enough serialized data to reconstruct the original `Diagnosis` and `Symptom`.
7. Decide and document rate-limit accounting at claim/execution time.
8. Ensure public getters return snapshots, not mutable internal dictionaries.

### Phase 2: Correct shared runtime wiring

1. Add `configure_runtime(settings, policy_engine, audit_logger, ...)` to the dashboard API or create a runtime context object.
2. Call it from `agent.py` after component construction.
3. Remove local rebinding of imported globals.
4. Add tests asserting API accessors return the exact agent-created instances.
5. Keep standalone launcher behavior explicit: either read-only/fallback or connect to a real shared service.

### Phase 3: Integrate approval execution

1. Refactor execution into a helper that accepts a `Diagnosis`, executor, audit logger, and event publisher.
2. Keep the existing `ALLOW_AUTO` path using that helper.
3. Add an approved-action drain/claim step on every watch cycle, including cycles with no new symptoms.
4. Reconstruct and validate stored diagnoses before execution.
5. Execute each claimed action once.
6. Record results in audit logs and policy result history.
7. Emit `fix_applied` only for actual successful executor results and `fix_failed` for failures.
8. Handle malformed action records without killing the main loop.
9. Preserve dry-run state in result and event payloads.

### Phase 4: Correct API and event behavior

1. Make approve return approval status, not execution status.
2. Emit `approval_granted` and `approval_rejected` events.
3. Return clear 404/409-style behavior for unknown or already processed actions.
4. Implement a reliable thread-to-async event bridge.
5. Make all event payloads use consistent timestamp, resource, action ID, status, and error fields.
6. Add audit entries for approvals and rejections with operator identity.

### Phase 5: Stabilize watcher and dashboard data

1. Add symptom/action deduplication and cooldown behavior.
2. Maintain agent-owned current issue state or a bounded cache.
3. Prevent HTTP endpoints from triggering unlimited live diagnosis/LLM work.
4. Add bounded timeouts and clear unavailable-cluster responses.
5. Ensure audit parsing handles malformed lines and missing fields safely.

### Phase 6: Implement the minimum useful dashboard

1. Load pending approvals from the API.
2. Render diagnosis, risk, proposed remediation, namespace, pod, and timestamp.
3. Add approve/reject controls.
4. Refresh the pending list after mutation.
5. Show approval state separately from execution state.
6. Display actual success/failure/dry-run events.
7. Add historical action and current issue views only when backed by reliable data.
8. Add WebSocket reconnect handling.
9. Avoid claiming resource drill-down, polling frequency, or real-time behavior that is not implemented.

### Phase 7: Harden executor and configuration

1. Use Kubernetes owner references rather than pod-name string heuristics.
2. Validate all remediation parameters and bounds.
3. Verify dry-run does not make live API mutations.
4. Standardize rollback implementation and command error handling.
5. Test configuration precedence: defaults, environment, YAML, and explicit runtime settings.
6. Clarify LLM provider/key requirements and behavior when external diagnosis is unavailable.

### Phase 8: Documentation and operational acceptance

Update:

- `README.md`
- `PLAN_DASHBOARD.md`
- `ARCHITECTURE.md`
- `CODEBASE_WALKTHROUGH.md`
- `PROJECT_CONTEXT.md`
- `COMPLETION_SUMMARY.md`
- `DASHBOARD_ENHANCEMENT.md`
- `DASHBOARD_QUICK_START.md`
- `MINIKUBE_TEST_GUIDE.md`

Documentation must state:

- The canonical launch command.
- Required environment variables/dependencies.
- Safe defaults and how dry-run differs from live execution.
- Embedded versus standalone dashboard behavior.
- The exact approval lifecycle.
- Known production limitations, especially authentication.

## 6. Required Tests Before Completion

### Unit tests

- `Symptom`, `Diagnosis`, and `RemediationResult` validation.
- Rule diagnosis for every supported failure type.
- LLM response parsing, malformed JSON, invalid enum, invalid confidence, and invalid parameters.
- Confidence threshold decisions.
- Low/medium/high risk decisions.
- Dry-run denial/behavior.
- Rate limits per minute and hour.
- Pending action creation.
- Approval and rejection transitions.
- Unknown, duplicate, already approved, already rejected, and already claimed IDs.
- Atomic claim returns an action exactly once.
- Public state getters cannot mutate internal state.
- Concurrent lifecycle calls do not duplicate or lose actions.

### Policy-to-execution integration tests

- `ALLOW_AUTO` calls executor exactly once.
- `REQUIRE_APPROVAL` does not call executor before approval.
- Approval causes exactly one executor call.
- Rejection never calls executor.
- A second agent cycle cannot execute a claimed action again.
- Malformed approved payload is safely rejected/audited.
- Executor success records a successful audit result and policy result.
- Executor failure records a failed audit result and policy result.
- Dry-run result is visible and is not reported as live mutation.

### Shared runtime/API tests

- Agent initialization configures the exact same policy/settings/audit instances used by API accessors.
- Pending approvals endpoint reflects agent-created actions.
- Approve endpoint changes state and emits `approval_granted`, never `fix_applied`.
- Reject endpoint changes state and emits `approval_rejected`.
- Unknown ID returns a clear failure response.
- Already processed ID cannot be approved/rejected again.
- Execution path emits `fix_applied` only after success and `fix_failed` after failure.
- Event bridge works from a worker thread to the dashboard loop.
- Historical audit endpoint handles valid and malformed JSONL lines.

### Watcher/executor/config tests

- Watcher classifies supported and unsupported failure reasons.
- Restart threshold is honored.
- Repeated symptoms are deduplicated/cooldown-controlled.
- Missing logs/events do not crash diagnosis.
- Executor dry-run performs no live mutation.
- Executor handles missing pod, owner, deployment, container, resource limit, and invalid parameter cases.
- Deployment resolution uses owner references.
- YAML and environment configuration precedence is correct.
- Missing LLM credentials produce a clear controlled behavior.

### Frontend/manual acceptance tests

1. Start the embedded agent in a safe dry-run test configuration.
2. Create or inject a pending approval.
3. Open the dashboard and confirm the action appears.
4. Confirm approval changes the visible state to approved/waiting for execution.
5. Confirm the dashboard does not show `fix_applied` merely because approval was clicked.
6. Confirm the actual executor result produces success or failure status.
7. Reject another pending action and confirm it never executes.
8. Refresh the browser and confirm expected state reloads.
9. Test WebSocket disconnect and reconnect.
10. Confirm no browser console errors.

### Full-system/Minikube acceptance

- Start Minikube and create a deterministic failing workload.
- Run the documented agent command, not an undocumented alternate module.
- Verify the watcher detects the failure.
- Verify diagnosis and policy records are written.
- Verify approval-required action appears in the dashboard.
- Approve it and verify one executor attempt.
- Verify Kubernetes state, audit entry, and dashboard event agree.
- Test rejection and verify no Kubernetes mutation.
- Test dry-run and verify no Kubernetes mutation.
- Clean up namespace and cluster resources.

## 7. Commands the Next Model Should Establish

The repository currently does not provide a valid complete test script. After fixing the test entry point, the expected commands should be equivalent to:

```bash
python -m pytest -q
python -m ruff check .
python -m mypy src
```

The exact commands may be adapted to the installed environment, but they must be committed to a working script or documented canonical command. Until then, a focused local invocation may require:

```bash
PYTHONPATH=src python -m pytest -q
```

Do not report the project as passing merely because `test.sh` prints `hello` or because the dashboard smoke script imports successfully.

## 8. Scope Boundaries

### Included

- Correct approval-to-execution workflow.
- Accurate policy and event semantics.
- Shared runtime state.
- Thread-safe action lifecycle.
- Watcher deduplication/cooldown.
- Reliable API/dashboard behavior.
- Executor validation and dry-run correctness.
- Reproducible tests and documentation.

### Deferred unless explicitly requested

- Production authentication/SSO implementation.
- Durable database migration.
- Multi-agent or multi-cluster coordination.
- Large visual redesign.
- New remediation types.
- Advanced metrics/tracing.
- Automatic rollback after remediation failure.

The dashboard must be treated as a security-sensitive mutating control surface before exposure beyond a trusted local environment.

## 9. Definition of Done

The project is complete only when:

1. A failing pod can be detected and diagnosed.
2. Policy can safely allow, deny, or require approval.
3. An approval is visible to the operator and shared with the live agent.
4. Approval leads to exactly one execution attempt.
5. Rejection leads to zero execution attempts.
6. Dashboard events describe approval and execution separately.
7. Audit logs match actual behavior.
8. Dry-run is clearly distinguished from live execution.
9. Repeated watcher cycles do not create uncontrolled duplicate actions.
10. Tests cover the full workflow and run from a documented command.
11. The launch path, dependencies, configuration, and dashboard limitations are documented accurately.
12. No completion document claims functionality that is not actually implemented.
