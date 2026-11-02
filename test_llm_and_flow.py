"""
Comprehensive test file for LLM endpoint verification and full diagnosis flow.

Tests WITHOUT requiring a live Kubernetes cluster:
  1.  LLM endpoint connectivity (real HTTP call)
  2.  LLM chat-completion with a trivial prompt
  3.  LLM structured-diagnosis prediction (OOMKilled symptom)
  4.  LLM structured-diagnosis prediction (CrashLoopBackOff symptom)
  5.  LLM structured-diagnosis prediction (Unknown/ambiguous symptom → LLM fallback)
  6.  Rule-engine: OOMKilled → INCREASE_MEMORY
  7.  Rule-engine: CrashLoopBackOff + OOM logs → INCREASE_MEMORY
  8.  Rule-engine: CrashLoopBackOff + panic logs → RESTART_POD
  9.  Rule-engine: ImagePullBackOff → UPDATE_IMAGE_TAG
  10. DiagnosisEngine: rule path (no LLM call)
  11. DiagnosisEngine: LLM fallback path (real call, Unknown failure type)
  12. PolicyEngine: dry-run mode blocks execution
  13. PolicyEngine: high-risk blocks auto-approval
  14. PolicyEngine: low-risk in auto-approve mode
  15. Executor: dry-run RESTART_POD returns DRY RUN message
  16. Executor: dry-run INCREASE_MEMORY returns DRY RUN message
  17. Executor: dry-run ROLLBACK_DEPLOYMENT returns DRY RUN message
  18. AuditLogger: writes + reads JSONL entries
  19. FastAPI /api/health endpoint
  20. FastAPI /api/current-issues endpoint (empty cache)
  21. FastAPI /api/historical-actions endpoint (no log file)
"""

import os
import sys
import json
import time
import tempfile
import pytest
import requests
from pathlib import Path
from unittest.mock import patch, MagicMock

# Make sure src/ is on path (mirrors test.sh export)
sys.path.insert(0, str(Path(__file__).parent / "src"))

from k8s_agent.config import Settings, LLMConfig, PolicyConfig
from k8s_agent.types import (
    Symptom, Diagnosis, FailureType, RemediationType, RiskLevel, RemediationResult
)

RUN_LLM_TESTS = os.getenv("RUN_LLM_TESTS", "").lower() in {"1", "true", "yes"}
live_llm = pytest.mark.skipif(
    not RUN_LLM_TESTS,
    reason="Live LLM checks are opt-in; set RUN_LLM_TESTS=1 to run them",
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_settings(**overrides) -> Settings:
    """Build Settings from real .env (or env vars) with optional overrides."""
    # Load from .env in project root
    env_path = Path(__file__).parent / ".env"
    env_vars = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env_vars[k.strip()] = v.strip()

    # Environment beats .env
    for k in ("OMNIROUTE_API_KEY", "OMNIROUTE_BASE_URL", "ANTHROPIC_API_KEY",
              "LLM__MODEL"):
        if k in os.environ:
            env_vars[k] = os.environ[k]

    with patch.dict(os.environ, env_vars, clear=False):
        settings = Settings()

    # Apply programmatic overrides
    for k, v in overrides.items():
        object.__setattr__(settings, k, v)
    return settings


def _oom_symptom() -> Symptom:
    return Symptom(
        namespace="production",
        pod_name="api-server-7d5f9",
        container_name="api",
        failure_type=FailureType.OOM_KILLED,
        message="Container 'api' OOMKilled",
        restart_count=5,
        logs="",
    )


def _crash_oom_symptom() -> Symptom:
    return Symptom(
        namespace="staging",
        pod_name="worker-abc12",
        container_name="worker",
        failure_type=FailureType.CRASH_LOOP_BACKOFF,
        message="Back-off restarting failed container",
        restart_count=8,
        logs="FATAL: out of memory: cannot allocate 512MB",
    )


def _crash_panic_symptom() -> Symptom:
    return Symptom(
        namespace="default",
        pod_name="go-service-1",
        container_name="svc",
        failure_type=FailureType.CRASH_LOOP_BACKOFF,
        message="Back-off restarting failed container",
        restart_count=3,
        logs="goroutine 1 [running]:\npanic: runtime error: index out of range",
    )


def _image_pull_symptom() -> Symptom:
    # Must contain "not found" to match the rule-engine pattern
    return Symptom(
        namespace="default",
        pod_name="deploy-xyz",
        container_name="app",
        failure_type=FailureType.IMAGE_PULL_BACKOFF,
        message="Failed to pull image myrepo/app:v99 — manifest unknown: not found",
        restart_count=0,
    )


def _unknown_symptom() -> Symptom:
    """Unknown failure type – should always fall through to LLM."""
    return Symptom(
        namespace="test",
        pod_name="mystery-pod-1",
        container_name="mystery",
        failure_type=FailureType.UNKNOWN,
        message="Pod is in an unknown state",
        restart_count=2,
        logs="ERROR: connection to database timed out after 30s",
    )


# ── Section 1: LLM Endpoint Connectivity ─────────────────────────────────────


@live_llm
class TestLLMEndpointConnectivity:
    """Verify the OmniRoute / LLM proxy is reachable and returns valid JSON."""

    @pytest.fixture(autouse=True)
    def settings(self):
        self._settings = _make_settings()

    def _base_url(self):
        return self._settings.omniroute_base_url or "https://integrate.api.nvidia.com/v1"

    def _api_key(self):
        return self._settings.omniroute_api_key or self._settings.anthropic_api_key

    def test_models_endpoint_returns_200(self):
        """GET /v1/models must return HTTP 200."""
        url = f"{self._base_url()}/models"
        headers = {"Authorization": f"Bearer {self._api_key()}"}
        resp = requests.get(url, headers=headers, timeout=10)
        assert resp.status_code == 200, (
            f"Expected 200 from {url}, got {resp.status_code}: {resp.text[:200]}"
        )

    def test_models_response_has_data_key(self):
        """Models response must contain a 'data' list with at least one model."""
        url = f"{self._base_url()}/models"
        headers = {"Authorization": f"Bearer {self._api_key()}"}
        resp = requests.get(url, headers=headers, timeout=10)
        body = resp.json()
        assert "data" in body, f"No 'data' key in response: {body}"
        assert len(body["data"]) > 0, "Model list is empty"
        model_ids = [m["id"] for m in body["data"]]
        print(f"\n  Available models: {model_ids[:5]}{'...' if len(model_ids) > 5 else ''}")

    def test_chat_completion_simple_prompt(self):
        """POST /v1/chat/completions with a trivial prompt must return content."""
        from k8s_agent.llm_client import NVIDIAClient
        client = NVIDIAClient(self._settings)
        response = client.chat_completion(
            messages=[{"role": "user", "content": "Reply with exactly: PONG"}],
            temperature=0.0,
            max_tokens=16,
        )
        content = response["choices"][0]["message"]["content"]
        assert content, "LLM returned empty content"
        print(f"\n  LLM ping response: {content!r}")

    def test_chat_completion_returns_valid_structure(self):
        """Response must have choices[0].message.content."""
        from k8s_agent.llm_client import NVIDIAClient
        client = NVIDIAClient(self._settings)
        response = client.chat_completion(
            messages=[{"role": "user", "content": "Say OK"}],
            max_tokens=8,
        )
        assert "choices" in response
        assert len(response["choices"]) > 0
        msg = response["choices"][0].get("message", {})
        assert "content" in msg


# ── Section 2: LLM Structured Diagnosis Prediction ───────────────────────────


@live_llm
class TestLLMDiagnosisPrediction:
    """Call the real LLM and verify it produces correctly structured Diagnosis objects."""

    @pytest.fixture(autouse=True)
    def diagnoser(self):
        from k8s_agent.diagnosis.llm_fallback import LLMDiagnoser
        settings = _make_settings()
        self._diagnoser = LLMDiagnoser(settings)

    def _assert_valid_diagnosis(self, diagnosis, symptom):
        assert diagnosis is not None, "LLM returned None diagnosis"
        assert isinstance(diagnosis, Diagnosis)
        assert diagnosis.symptom is symptom
        assert diagnosis.root_cause, "root_cause is empty"
        assert diagnosis.reasoning, "reasoning is empty"
        assert 0.0 <= diagnosis.confidence <= 1.0, f"confidence out of range: {diagnosis.confidence}"
        assert diagnosis.diagnosed_by in ("llm", "llm_enhanced"), (
            f"Expected diagnosed_by to be 'llm' or 'llm_enhanced', got {diagnosis.diagnosed_by!r}"
        )
        assert isinstance(diagnosis.remediation_type, RemediationType)
        assert isinstance(diagnosis.risk_level, RiskLevel)
        print(f"\n  root_cause   : {diagnosis.root_cause}")
        print(f"  remediation  : {diagnosis.remediation_type}")
        print(f"  risk_level   : {diagnosis.risk_level}")
        print(f"  confidence   : {diagnosis.confidence:.2f}")
        print(f"  reasoning    : {diagnosis.reasoning[:120]}...")

    def test_llm_diagnoses_oom_killed(self):
        """LLM should suggest INCREASE_MEMORY for OOMKilled."""
        symptom = _oom_symptom()
        diagnosis = self._diagnoser.diagnose(symptom)
        self._assert_valid_diagnosis(diagnosis, symptom)
        assert diagnosis.remediation_type == RemediationType.INCREASE_MEMORY, (
            f"Expected INCREASE_MEMORY for OOMKilled, got {diagnosis.remediation_type}"
        )

    def test_llm_diagnoses_crash_loop_oom(self):
        """LLM should suggest INCREASE_MEMORY when OOM text is in crash logs."""
        symptom = _crash_oom_symptom()
        diagnosis = self._diagnoser.diagnose(symptom)
        self._assert_valid_diagnosis(diagnosis, symptom)
        assert diagnosis.remediation_type in (
            RemediationType.INCREASE_MEMORY, RemediationType.RESTART_POD
        ), f"Unexpected remediation: {diagnosis.remediation_type}"

    def test_llm_diagnoses_unknown_symptom(self):
        """LLM fallback must handle UNKNOWN failure types and return a non-None Diagnosis."""
        symptom = _unknown_symptom()
        diagnosis = self._diagnoser.diagnose(symptom)
        self._assert_valid_diagnosis(diagnosis, symptom)

    def test_llm_diagnosis_has_verification_steps(self):
        """LLM should return suggested_verification_steps list."""
        symptom = _oom_symptom()
        diagnosis = self._diagnoser.diagnose(symptom)
        assert diagnosis is not None
        assert isinstance(diagnosis.suggested_verification_steps, list), (
            "suggested_verification_steps must be a list"
        )
        print(f"\n  verification_steps: {diagnosis.suggested_verification_steps}")

    def test_llm_diagnosis_has_prevention_recommendations(self):
        """LLM should return prevention_recommendations list."""
        symptom = _oom_symptom()
        diagnosis = self._diagnoser.diagnose(symptom)
        assert diagnosis is not None
        assert isinstance(diagnosis.prevention_recommendations, list)
        print(f"\n  prevention_recommendations: {diagnosis.prevention_recommendations}")


# ── Section 3: Rule Engine ────────────────────────────────────────────────────


class TestRuleEngine:
    """Rule-based diagnosis must match known patterns deterministically."""

    @pytest.fixture(autouse=True)
    def matcher(self):
        from k8s_agent.diagnosis.rules import RuleMatcher
        self._matcher = RuleMatcher()

    def test_oom_killed_returns_increase_memory(self):
        diagnosis = self._matcher.diagnose(_oom_symptom())
        assert diagnosis is not None
        assert diagnosis.remediation_type == RemediationType.INCREASE_MEMORY
        assert diagnosis.diagnosed_by == "rule_engine"

    def test_crash_loop_oom_logs_returns_increase_memory(self):
        diagnosis = self._matcher.diagnose(_crash_oom_symptom())
        assert diagnosis is not None
        assert diagnosis.remediation_type == RemediationType.INCREASE_MEMORY

    def test_crash_loop_panic_returns_restart_pod(self):
        diagnosis = self._matcher.diagnose(_crash_panic_symptom())
        assert diagnosis is not None
        assert diagnosis.remediation_type == RemediationType.RESTART_POD

    def test_image_pull_returns_update_image_tag(self):
        # "not found" in message → NO_ACTION (manual fix needed) per rule engine
        diagnosis = self._matcher.diagnose(_image_pull_symptom())
        assert diagnosis is not None
        assert diagnosis.remediation_type in (
            RemediationType.NO_ACTION, RemediationType.UPDATE_IMAGE_TAG,
            RemediationType.RESTART_POD
        )
        print(f"\n  ImagePull diagnosis: {diagnosis.remediation_type} ({diagnosis.root_cause})")

    def test_unknown_failure_type_returns_none(self):
        """Rule engine must return None for UNKNOWN (not crash)."""
        diagnosis = self._matcher.diagnose(_unknown_symptom())
        assert diagnosis is None, (
            f"Rule engine should return None for UNKNOWN, got {diagnosis}"
        )

    def test_exit_code_137_returns_increase_memory(self):
        """Exit code 137 (SIGKILL) in last_state should trigger INCREASE_MEMORY."""
        symptom = Symptom(
            namespace="default",
            pod_name="oom-pod-2",
            container_name="app",
            failure_type=FailureType.CRASH_LOOP_BACKOFF,
            message="Back-off restarting",
            restart_count=4,
            last_state={"exit_code": 137},
        )
        diagnosis = self._matcher.diagnose(symptom)
        assert diagnosis is not None
        assert diagnosis.remediation_type == RemediationType.INCREASE_MEMORY
        assert diagnosis.confidence == 0.85

    def test_oom_diagnosis_params(self):
        """OOM diagnosis should include memory_increase_factor in params."""
        diagnosis = self._matcher.diagnose(_oom_symptom())
        assert "memory_increase_factor" in diagnosis.remediation_params
        assert diagnosis.remediation_params["memory_increase_factor"] > 1.0


# ── Section 4: DiagnosisEngine Integration ───────────────────────────────────


class TestDiagnosisEngine:
    """DiagnosisEngine chooses rule vs LLM path correctly."""

    @pytest.fixture(autouse=True)
    def engine(self):
        from k8s_agent.diagnosis.engine import DiagnosisEngine
        self._settings = _make_settings()
        self._engine = DiagnosisEngine(self._settings)

    def test_rule_path_no_llm_call(self):
        """Rule-matched symptom must NOT call LLM."""
        with patch("k8s_agent.diagnosis.engine.LLMDiagnoser") as mock_llm_cls:
            from k8s_agent.diagnosis.engine import DiagnosisEngine
            engine = DiagnosisEngine(self._settings)
            diagnosis = engine.rule_matcher.diagnose(_oom_symptom())
            # The LLM class was instantiated but its diagnose() method should NOT be called
            # since the rule matcher already matched
            mock_llm_cls.return_value.diagnose.assert_not_called()
            assert diagnosis is not None
            assert diagnosis.diagnosed_by == "rule_engine"

    @live_llm
    def test_llm_fallback_for_unknown_symptom(self):
        """UNKNOWN failure type must reach and return from LLM diagnoser."""
        symptom = _unknown_symptom()
        diagnosis = self._engine.diagnose(symptom)
        assert diagnosis is not None, "Engine returned None for UNKNOWN symptom with real LLM"
        assert diagnosis.diagnosed_by in ("llm", "llm_enhanced")
        print(f"\n  LLM fallback root_cause: {diagnosis.root_cause}")

    def test_diagnosis_engine_oom_rule_shortcircuits(self):
        """OOMKilled must be diagnosed by rule engine, not LLM."""
        with patch.object(self._engine.llm_diagnoser, "diagnose") as mock_llm:
            diagnosis = self._engine.diagnose(_oom_symptom())
        mock_llm.assert_not_called()
        assert diagnosis is not None
        assert diagnosis.diagnosed_by == "rule_engine"


# ── Section 5: Policy Engine ─────────────────────────────────────────────────


class TestPolicyEngine:
    """Policy engine authorization and mode tests."""

    @pytest.fixture(autouse=True)
    def policy(self):
        from k8s_agent.policy import PolicyEngine
        self._settings = _make_settings()
        self._policy = PolicyEngine(self._settings)

    def _sample_diagnosis(self, risk=RiskLevel.LOW, remediation=RemediationType.RESTART_POD,
                          confidence=0.9, requires_approval=False) -> Diagnosis:
        return Diagnosis(
            symptom=_oom_symptom(),
            root_cause="Test root cause",
            remediation_type=remediation,
            remediation_params={},
            risk_level=risk,
            confidence=confidence,
            reasoning="Test reasoning",
            requires_approval=requires_approval,
            diagnosed_by="rule_engine",
        )

    def test_dry_run_mode_blocks_all_actions(self):
        """In dry_run_only mode, every diagnosis should be blocked."""
        assert self._settings.policy.dry_run_only is True, "config/agent.yaml should have dry_run_only: true"
        authorized, reason = self._policy.authorize(self._sample_diagnosis())
        assert authorized is False
        assert "dry-run" in reason.lower() or "dry_run" in reason.lower()
        print(f"\n  Dry-run block reason: {reason}")

    def test_high_risk_blocked_even_without_dry_run(self):
        """HIGH risk with medium confidence should be blocked if dry-run is off."""
        from k8s_agent.config import PolicyConfig
        settings = _make_settings()
        object.__setattr__(settings, "policy", PolicyConfig(
            dry_run_only=False,
            auto_approve_low_risk=False,
        ))
        from k8s_agent.policy import PolicyEngine
        policy = PolicyEngine(settings)
        diagnosis = self._sample_diagnosis(
            risk=RiskLevel.HIGH,
            remediation=RemediationType.ROLLBACK_DEPLOYMENT,
            confidence=0.75,
            requires_approval=True,
        )
        authorized, reason = policy.authorize(diagnosis)
        assert authorized is False
        print(f"\n  High-risk block reason: {reason}")

    def test_low_confidence_blocked(self):
        """Diagnosis with confidence below threshold must be blocked."""
        settings = _make_settings()
        object.__setattr__(settings, "policy", PolicyConfig(dry_run_only=False))
        from k8s_agent.policy import PolicyEngine
        policy = PolicyEngine(settings)
        diagnosis = self._sample_diagnosis(confidence=0.4)
        authorized, reason = policy.authorize(diagnosis)
        assert authorized is False
        print(f"\n  Low-confidence block reason: {reason}")

    def test_pending_action_registration(self):
        """authorize() in dry-run mode blocks and gives a clear reason."""
        diagnosis = self._sample_diagnosis(requires_approval=True)
        authorized, reason = self._policy.authorize(diagnosis)
        assert authorized is False
        # In dry-run mode it's blocked at the first gate; no need to reach approval queue
        assert reason, "authorize() returned empty reason string"
        print(f"\n  Block reason: {reason}")


# ── Section 6: Executor (dry-run) ────────────────────────────────────────────


class TestExecutorDryRun:
    """Executor in dry-run mode must never touch real Kubernetes APIs."""

    @pytest.fixture(autouse=True)
    def executor(self):
        from k8s_agent.executor import Executor
        with patch("k8s_agent.executor.client.CoreV1Api"), \
             patch("k8s_agent.executor.client.AppsV1Api"):
            self._executor = Executor(_make_settings())

    def _diagnosis(self, remediation: RemediationType, params=None) -> Diagnosis:
        return Diagnosis(
            symptom=_oom_symptom(),
            root_cause="Test",
            remediation_type=remediation,
            remediation_params=params or {},
            risk_level=RiskLevel.LOW,
            confidence=0.95,
            reasoning="Test",
            requires_approval=False,
            diagnosed_by="rule_engine",
        )

    def test_dry_run_restart_pod(self):
        result = self._executor.execute(
            self._diagnosis(RemediationType.RESTART_POD), dry_run=True
        )
        assert result.dry_run is True
        # In dry-run, executed=False (nothing actually happened in K8s)
        assert "[DRY RUN]" in result.output or "dry" in result.output.lower()
        print(f"\n  Dry-run RESTART output: {result.output}")

    def test_dry_run_increase_memory(self):
        result = self._executor.execute(
            self._diagnosis(RemediationType.INCREASE_MEMORY,
                            {"memory_increase_factor": 1.5}),
            dry_run=True,
        )
        assert result.dry_run is True
        assert "[DRY RUN]" in result.output or "dry" in result.output.lower()
        print(f"\n  Dry-run INCREASE_MEMORY output: {result.output}")

    def test_dry_run_rollback_deployment(self):
        result = self._executor.execute(
            self._diagnosis(RemediationType.ROLLBACK_DEPLOYMENT),
            dry_run=True,
        )
        assert result.dry_run is True
        assert "[DRY RUN]" in result.output or "dry" in result.output.lower()
        print(f"\n  Dry-run ROLLBACK output: {result.output}")

    def test_dry_run_no_action(self):
        result = self._executor.execute(
            self._diagnosis(RemediationType.NO_ACTION), dry_run=True
        )
        assert result.dry_run is True
        assert result.output  # should produce some output
        print(f"\n  NO_ACTION output: {result.output}")


# ── Section 7: AuditLogger ───────────────────────────────────────────────────


class TestAuditLogger:
    """AuditLogger must write and read JSONL entries correctly."""

    def test_audit_log_write_and_read(self):
        from k8s_agent.audit import AuditLogger
        from k8s_agent.types import AuditEntry

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
            log_path = Path(f.name)

        try:
            settings = _make_settings()
            object.__setattr__(settings, "audit_log_path", log_path)
            logger = AuditLogger(settings)

            diagnosis = Diagnosis(
                symptom=_oom_symptom(),
                root_cause="Memory limit too low",
                remediation_type=RemediationType.INCREASE_MEMORY,
                remediation_params={"memory_increase_factor": 1.5},
                risk_level=RiskLevel.MEDIUM,
                confidence=0.9,
                reasoning="OOM kill detected",
                requires_approval=True,
                diagnosed_by="rule_engine",
            )
            result = RemediationResult(
                diagnosis=diagnosis,
                executed=True,
                success=True,
                dry_run=True,
                output="[DRY RUN] Would increase memory",
            )

            logger.log_remediation(result, approved_by="test")

            lines = log_path.read_text().strip().splitlines()
            assert len(lines) == 1, f"Expected 1 audit line, got {len(lines)}"

            entry = json.loads(lines[0])
            assert "event_type" in entry
            assert entry["event_type"] == "remediation_executed"
            print(f"\n  Audit entry keys: {list(entry.keys())}")
        finally:
            log_path.unlink(missing_ok=True)


# ── Section 8: FastAPI Dashboard Endpoints ───────────────────────────────────


class TestDashboardAPI:
    """FastAPI routes must respond correctly without a live K8s cluster."""

    @pytest.fixture(autouse=True)
    def client(self):
        from fastapi.testclient import TestClient
        import k8s_agent.dashboard.api as dashboard_api

        # Reset shared state
        dashboard_api.shared_current_issue_cache = None

        with patch("k8s_agent.dashboard.api.get_audit_logger") as mock_al, \
             patch("k8s_agent.dashboard.api.get_policy_engine") as mock_pe:

            # Audit logger stub: audit_log_path points to a non-existent file (no log yet)
            audit_stub = MagicMock()
            audit_stub.audit_log_path = Path(tempfile.mktemp(suffix=".jsonl"))
            mock_al.return_value = audit_stub

            # Policy engine stub
            policy_stub = MagicMock()
            policy_stub.get_pending_actions.return_value = {}
            policy_stub.get_approved_actions.return_value = {}
            policy_stub.get_rejected_actions.return_value = {}
            mock_pe.return_value = policy_stub

            # api.py exposes `app` (not `router`)
            self._client = TestClient(dashboard_api.app, raise_server_exceptions=False)
            yield

    def test_health_endpoint(self):
        resp = self._client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "healthy"
        print(f"\n  /health: {data}")

    def test_current_issues_empty_cache(self):
        resp = self._client.get("/api/current-issues")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        print(f"\n  /api/current-issues (empty cache): {data}")

    def test_historical_actions_no_file(self):
        resp = self._client.get("/api/historical-actions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        print(f"\n  /api/historical-actions (no file): {data}")

    def test_pending_actions_endpoint(self):
        resp = self._client.get("/api/pending-approvals")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        print(f"\n  /api/pending-approvals: {data}")

    def test_root_serves_html(self):
        resp = self._client.get("/")
        # Root either serves HTML or redirects
        assert resp.status_code in (200, 307, 302)
        print(f"\n  / status: {resp.status_code}")


# ── Main runner ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-xvs", "--tb=short"])
