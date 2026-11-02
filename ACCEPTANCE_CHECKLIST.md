# Manual & End-to-End Acceptance Test Checklist

Use this checklist to verify the end-to-end functionality of the Kubernetes Self-Healing Remediation Agent prior to deploying to a production cluster.

---

## 1. Environment & Setup Verification

- [ ] Python 3.13 virtual environment activated (`source venv/bin/activate`).
- [ ] Dependencies installed via `pip install -e ".[dev]"`.
- [ ] Kubeconfig or Minikube cluster active and accessible (`kubectl get nodes`).
- [ ] Run `bash test.sh` and verify 35 tests pass cleanly with zero failures.

---

## 2. Dry-Run Remediation Acceptance

- [ ] Ensure `config/agent.yaml` has `policy.dry_run_only: true`.
- [ ] Launch agent: `PYTHONPATH=src python3 src/agent.py`.
- [ ] Create a failing pod in the target namespace (e.g., `kubectl apply -f crash-pod.yaml`).
- [ ] Observe logs for symptom detection and diagnosis generation.
- [ ] Open dashboard at `http://localhost:8000` and confirm pending approval item appears.
- [ ] Click **Approve** in the dashboard UI.
- [ ] Verify dashboard notification displays approval success.
- [ ] Observe agent logs claim and execute the approved action in `[DRY RUN]` mode.
- [ ] Confirm pod status in Kubernetes cluster is **NOT** modified (`kubectl get pods`).
- [ ] Verify audit entry appended to `logs/audit.jsonl` with `dry_run: true`.

---

## 3. Real Execution Acceptance (Optional Minikube Test)

- [ ] Update test configuration or parameter to `dry_run: false`.
- [ ] Inject a test pod failure (e.g., CrashLoopBackOff pod).
- [ ] Approve the remediation via the dashboard UI.
- [ ] Verify the agent claims and executes real pod deletion or scaling operation.
- [ ] Confirm Kubernetes resource updates as expected (`kubectl get pods -w`).
- [ ] Verify dashboard receives `fix_applied` event and audit log records execution outcome.

---

## 4. Rejection Acceptance

- [ ] Inject a test pod failure requiring human approval.
- [ ] Click **Reject** in the dashboard UI.
- [ ] Confirm item is removed from pending approvals.
- [ ] Verify agent main loop does **NOT** call executor for the rejected action ID.
- [ ] Confirm no cluster mutations occur.

---

## 5. Resilience & Edge Cases

- [ ] **Symptom Deduplication**: Verify repeated watch cycles do not generate duplicate pending approval items for the same symptom during the 300s cooldown window.
- [ ] **Cached Current Issues**: Query `/api/current-issues` repeatedly and verify responses are served instantly from cache without running live cluster scans.
- [ ] **WebSocket Auto-Reconnect**: Restart the agent while the dashboard browser tab is open; confirm WebSocket status updates to disconnected and automatically reconnects once restarted.
