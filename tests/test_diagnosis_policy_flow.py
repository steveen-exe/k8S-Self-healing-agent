"""Integration tests for diagnosis → policy → execution flow."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
import json

from k8s_agent.types import (
    Symptom, Diagnosis, FailureType, RemediationType, RiskLevel,
    RemediationResult
)
from k8s_agent.diagnosis.llm_fallback import LLMDiagnoser
from k8s_agent.policy import PolicyEngine
from k8s_agent.executor import Executor
from k8s_agent.config import Settings, RateLimitConfig, LLMConfig, PolicyConfig, WatcherConfig


# Test fixtures
@pytest.fixture
def mock_settings():
    """Create mock settings."""
    settings = Mock(spec=Settings)
    settings.llm = Mock(spec=LLMConfig)
    settings.llm.model = "test-model"
    settings.llm.temperature = 0.7
    settings.llm.max_tokens = 1000
    settings.llm.timeout_seconds = 30

    settings.policy = Mock(spec=PolicyConfig)
    settings.policy.dry_run_only = False
    settings.policy.auto_approve_low_risk = False

    rate_limit = Mock(spec=RateLimitConfig)
    rate_limit.max_actions_per_minute = 10
    rate_limit.max_actions_per_hour = 100
    settings.policy.rate_limit = rate_limit
    settings.policy.get = Mock(return_value=0.7)  # min_confidence_threshold

    settings.watcher = Mock(spec=WatcherConfig)
    settings.watcher.namespaces = ["default"]

    # Add API key attributes for LLM client
    settings.omniroute_api_key = None
    settings.omniroute_base_url = None
    settings.anthropic_api_key = "test-api-key"

    return settings


@pytest.fixture
def sample_symptom():
    """Create a sample pod symptom."""
    return Symptom(
        namespace="default",
        pod_name="test-pod-abc123",
        container_name="app",
        failure_type=FailureType.CRASH_LOOP_BACKOFF,
        message="Pod keeps restarting",
        restart_count=5,
        logs="Error: OutOfMemory\nError: OutOfMemory\n",
    )


@pytest.fixture
def sample_diagnosis(sample_symptom):
    """Create a sample diagnosis."""
    return Diagnosis(
        symptom=sample_symptom,
        root_cause="Memory leak in application",
        remediation_type=RemediationType.INCREASE_MEMORY,
        remediation_params={"memory_increase_factor": 1.5},
        risk_level=RiskLevel.MEDIUM,
        confidence=0.85,
        reasoning="Pod restarted 5 times with OOM errors",
        requires_approval=True,
        diagnosed_by="llm_enhanced",
        suggested_verification_steps=[
            "Check pod logs after restart",
            "Monitor memory usage for 5 minutes"
        ],
        prevention_recommendations=[
            "Add memory leak detection to CI/CD",
            "Implement memory limits in pod spec"
        ]
    )


# LLM Parsing Tests
class TestLLMDiagnosisParsing:
    """Test LLM response parsing and validation."""

    def test_valid_llm_response(self, mock_settings, sample_symptom):
        """Test parsing of valid LLM response."""
        diagnoser = LLMDiagnoser(mock_settings)

        valid_response = {
            "root_cause": "Memory leak",
            "remediation_type": "increase_memory",
            "remediation_params": {"memory_increase_factor": 1.5},
            "risk_level": "medium",
            "confidence": 0.85,
            "reasoning": "5 restarts with OOM",
            "requires_approval": True,
            "suggested_verification_steps": ["Check logs"],
            "prevention_recommendations": ["Add limits"]
        }

        with patch.object(diagnoser.client, 'chat_completion') as mock_client:
            mock_client.return_value = {
                "choices": [{"message": {"content": json.dumps(valid_response)}}]
            }

            result = diagnoser.diagnose(sample_symptom)

            assert result is not None
            assert result.root_cause == "Memory leak"
            assert result.remediation_type == RemediationType.INCREASE_MEMORY
            assert result.confidence == 0.85
            assert result.risk_level == RiskLevel.MEDIUM

    def test_invalid_remediation_type(self, mock_settings, sample_symptom):
        """Test rejection of invalid remediation type."""
        diagnoser = LLMDiagnoser(mock_settings)

        invalid_response = {
            "root_cause": "Test",
            "remediation_type": "invalid_action",
            "remediation_params": {},
            "risk_level": "low",
            "confidence": 0.8,
            "reasoning": "Test",
            "requires_approval": False
        }

        with patch.object(diagnoser.client, 'chat_completion') as mock_client:
            mock_client.return_value = {
                "choices": [{"message": {"content": json.dumps(invalid_response)}}]
            }

            result = diagnoser.diagnose(sample_symptom)
            assert result is None

    def test_invalid_risk_level(self, mock_settings, sample_symptom):
        """Test rejection of invalid risk level."""
        diagnoser = LLMDiagnoser(mock_settings)

        invalid_response = {
            "root_cause": "Test",
            "remediation_type": "restart_pod",
            "remediation_params": {},
            "risk_level": "critical",
            "confidence": 0.8,
            "reasoning": "Test",
            "requires_approval": False
        }

        with patch.object(diagnoser.client, 'chat_completion') as mock_client:
            mock_client.return_value = {
                "choices": [{"message": {"content": json.dumps(invalid_response)}}]
            }

            result = diagnoser.diagnose(sample_symptom)
            assert result is None

    def test_invalid_confidence(self, mock_settings, sample_symptom):
        """Test rejection of out-of-range confidence."""
        diagnoser = LLMDiagnoser(mock_settings)

        invalid_response = {
            "root_cause": "Test",
            "remediation_type": "restart_pod",
            "remediation_params": {},
            "risk_level": "low",
            "confidence": 1.5,
            "reasoning": "Test",
            "requires_approval": False
        }

        with patch.object(diagnoser.client, 'chat_completion') as mock_client:
            mock_client.return_value = {
                "choices": [{"message": {"content": json.dumps(invalid_response)}}]
            }

            result = diagnoser.diagnose(sample_symptom)
            assert result is None

    def test_invalid_memory_factor(self, mock_settings, sample_symptom):
        """Test rejection of invalid memory_increase_factor."""
        diagnoser = LLMDiagnoser(mock_settings)

        invalid_response = {
            "root_cause": "OOM",
            "remediation_type": "increase_memory",
            "remediation_params": {"memory_increase_factor": 15},
            "risk_level": "low",
            "confidence": 0.8,
            "reasoning": "Test",
            "requires_approval": False
        }

        with patch.object(diagnoser.client, 'chat_completion') as mock_client:
            mock_client.return_value = {
                "choices": [{"message": {"content": json.dumps(invalid_response)}}]
            }

            result = diagnoser.diagnose(sample_symptom)
            assert result is None

    def test_malformed_json(self, mock_settings, sample_symptom):
        """Test handling of malformed JSON from LLM."""
        diagnoser = LLMDiagnoser(mock_settings)

        with patch.object(diagnoser.client, 'chat_completion') as mock_client:
            mock_client.return_value = {
                "choices": [{"message": {"content": "{ invalid json"}}]
            }

            result = diagnoser.diagnose(sample_symptom)
            assert result is None

    def test_missing_required_field(self, mock_settings, sample_symptom):
        """Test rejection of response missing required field."""
        diagnoser = LLMDiagnoser(mock_settings)

        invalid_response = {
            "root_cause": "Test",
            "remediation_type": "restart_pod",
            "risk_level": "low",
            "confidence": 0.8,
            # Missing "reasoning"
            "requires_approval": False
        }

        with patch.object(diagnoser.client, 'chat_completion') as mock_client:
            mock_client.return_value = {
                "choices": [{"message": {"content": json.dumps(invalid_response)}}]
            }

            result = diagnoser.diagnose(sample_symptom)
            assert result is None


# Policy Engine Tests
class TestPolicyEngine:
    """Test policy engine authorization and risk assessment."""

    def test_confidence_threshold_rejection(self, mock_settings, sample_diagnosis):
        """Test that low confidence diagnoses are rejected."""
        mock_settings.policy.get = Mock(return_value=0.7)
        policy = PolicyEngine(mock_settings)

        sample_diagnosis.confidence = 0.5

        authorized, reason = policy.authorize(sample_diagnosis)
        assert not authorized
        assert "confidence" in reason.lower()

    def test_low_risk_auto_approval(self, mock_settings, sample_diagnosis):
        """Test auto-approval of low-risk actions."""
        mock_settings.policy.auto_approve_low_risk = True
        mock_settings.policy.get = Mock(return_value=0.5)
        policy = PolicyEngine(mock_settings)

        sample_diagnosis.risk_level = RiskLevel.LOW
        sample_diagnosis.confidence = 0.9
        sample_diagnosis.requires_approval = False

        authorized, reason = policy.authorize(sample_diagnosis)
        assert authorized

    def test_high_risk_requires_approval(self, mock_settings, sample_diagnosis):
        """Test that high-risk actions require approval."""
        mock_settings.policy.auto_approve_low_risk = True
        mock_settings.policy.get = Mock(return_value=0.5)
        policy = PolicyEngine(mock_settings)

        sample_diagnosis.risk_level = RiskLevel.HIGH
        sample_diagnosis.confidence = 0.95

        authorized, reason = policy.authorize(sample_diagnosis)
        assert not authorized
        assert "approval" in reason.lower()

    def test_policy_decision_structure(self, mock_settings, sample_diagnosis):
        """Test structure of policy decision."""
        mock_settings.policy.get = Mock(return_value=0.7)
        policy = PolicyEngine(mock_settings)

        decision = policy.get_policy_decision(sample_diagnosis)

        assert "decision" in decision
        assert "risk_score" in decision
        assert "risk_level" in decision
        assert "reasons" in decision
        assert "required_approval" in decision
        assert "policy_rule" in decision

    def test_rate_limiting(self, mock_settings, sample_diagnosis):
        """Test rate limit enforcement."""
        rate_limit = Mock()
        rate_limit.max_actions_per_minute = 1
        rate_limit.max_actions_per_hour = 100
        mock_settings.policy.rate_limit = rate_limit
        mock_settings.policy.get = Mock(return_value=0.5)
        mock_settings.policy.auto_approve_low_risk = True

        policy = PolicyEngine(mock_settings)

        sample_diagnosis.risk_level = RiskLevel.LOW
        sample_diagnosis.requires_approval = False
        sample_diagnosis.confidence = 0.9

        # First action should be approved
        authorized1, _ = policy.authorize(sample_diagnosis)
        assert authorized1

        # Second action should be rate limited
        authorized2, reason = policy.authorize(sample_diagnosis)
        assert not authorized2
        assert "rate" in reason.lower()

    def test_dry_run_mode(self, mock_settings, sample_diagnosis):
        """Test dry-run mode always denies."""
        mock_settings.policy.dry_run_only = True
        mock_settings.policy.get = Mock(return_value=0.5)
        policy = PolicyEngine(mock_settings)

        authorized, reason = policy.authorize(sample_diagnosis)
        assert not authorized
        assert "dry-run" in reason.lower()

    def test_pending_action_tracking(self, mock_settings, sample_diagnosis):
        """Test that pending actions are tracked."""
        mock_settings.policy.auto_approve_low_risk = False
        mock_settings.policy.get = Mock(return_value=0.5)
        policy = PolicyEngine(mock_settings)

        sample_diagnosis.risk_level = RiskLevel.HIGH

        authorized, reason = policy.authorize(sample_diagnosis)
        assert not authorized

        pending = policy.get_pending_actions()
        assert len(pending) > 0

    def test_action_approval(self, mock_settings, sample_diagnosis):
        """Test action approval workflow."""
        mock_settings.policy.auto_approve_low_risk = False
        mock_settings.policy.get = Mock(return_value=0.5)
        policy = PolicyEngine(mock_settings)

        sample_diagnosis.risk_level = RiskLevel.HIGH

        # Get pending action
        authorized, reason = policy.authorize(sample_diagnosis)
        action_id = reason.split("Action ID: ")[1]

        # Verify pending
        pending = policy.get_pending_actions()
        assert action_id in pending

        # Approve action
        result = policy.approve_action(action_id)
        assert result is True

        # Verify no longer pending
        pending = policy.get_pending_actions()
        assert action_id not in pending

    def test_action_rejection(self, mock_settings, sample_diagnosis):
        """Test action rejection workflow."""
        mock_settings.policy.auto_approve_low_risk = False
        mock_settings.policy.get = Mock(return_value=0.5)
        policy = PolicyEngine(mock_settings)

        sample_diagnosis.risk_level = RiskLevel.HIGH

        # Get pending action
        authorized, reason = policy.authorize(sample_diagnosis)
        action_id = reason.split("Action ID: ")[1]

        # Reject action
        result = policy.reject_action(action_id)
        assert result is True

        # Verify no longer pending
        pending = policy.get_pending_actions()
        assert action_id not in pending


# Executor Tests
class TestExecutor:
    """Test remediation executor."""

    def test_restart_pod_dry_run(self, mock_settings, sample_diagnosis):
        """Test dry-run pod restart - dry run should not call Kubernetes API."""
        with patch('k8s_agent.executor.client.CoreV1Api'), \
             patch('k8s_agent.executor.client.AppsV1Api'):
            executor = Executor(mock_settings)
            result = executor.execute(sample_diagnosis, dry_run=True)

        assert result.dry_run is True
        assert result.success is True
        assert "[DRY RUN]" in result.output

    def test_no_action_type(self, mock_settings, sample_diagnosis):
        """Test no-action remediation type."""
        sample_diagnosis.remediation_type = RemediationType.NO_ACTION
        with patch('k8s_agent.executor.client.CoreV1Api'), \
             patch('k8s_agent.executor.client.AppsV1Api'):
            executor = Executor(mock_settings)
            result = executor.execute(sample_diagnosis, dry_run=True)

        assert result.success is True
        assert "manual intervention" in result.output.lower()

    def test_executor_error_handling(self, mock_settings, sample_diagnosis):
        """Test executor error handling with mocked API call failure."""
        # Change to RESTART_POD so the mock exception will be triggered
        sample_diagnosis.remediation_type = RemediationType.RESTART_POD
        with patch('k8s_agent.executor.client.CoreV1Api') as mock_v1, \
             patch('k8s_agent.executor.client.AppsV1Api'):
            mock_v1.return_value.delete_namespaced_pod.side_effect = Exception("API error")
            executor = Executor(mock_settings)
            result = executor.execute(sample_diagnosis, dry_run=False)

        assert result.success is False
        assert result.error is not None
        assert "API error" in result.error


# Integration Tests
class TestDiagnosisPolicyExecutionFlow:
    """Test end-to-end diagnosis → policy → execution flow."""

    def test_complete_flow_low_risk_action(self, mock_settings, sample_diagnosis):
        """Test complete flow for low-risk action."""
        mock_settings.policy.auto_approve_low_risk = True
        mock_settings.policy.get = Mock(return_value=0.5)

        policy = PolicyEngine(mock_settings)
        sample_diagnosis.risk_level = RiskLevel.LOW
        sample_diagnosis.confidence = 0.9
        sample_diagnosis.requires_approval = False

        decision = policy.get_policy_decision(sample_diagnosis)
        assert decision["decision"] == "ALLOW_AUTO"
        assert decision["required_approval"] is False

    def test_complete_flow_high_risk_action(self, mock_settings, sample_diagnosis):
        """Test complete flow for high-risk action requiring approval."""
        mock_settings.policy.get = Mock(return_value=0.5)
        policy = PolicyEngine(mock_settings)
        sample_diagnosis.risk_level = RiskLevel.HIGH

        decision = policy.get_policy_decision(sample_diagnosis)
        assert decision["decision"] == "REQUIRE_APPROVAL"
        assert decision["required_approval"] is True
        assert "action_id" in decision
        assert decision["action_id"] is not None

    def test_approval_workflow_end_to_end(self, mock_settings, sample_diagnosis):
        """Test complete approval workflow."""
        mock_settings.policy.auto_approve_low_risk = False
        mock_settings.policy.get = Mock(return_value=0.5)
        policy = PolicyEngine(mock_settings)
        sample_diagnosis.risk_level = RiskLevel.HIGH

        # Step 1: Get policy decision which creates pending action
        decision = policy.get_policy_decision(sample_diagnosis)
        action_id = decision["action_id"]
        assert action_id is not None

        # Step 2: Verify action is pending
        pending = policy.get_pending_actions()
        assert action_id in pending

        # Step 3: Approve action
        approved = policy.approve_action(action_id)
        assert approved is True

        # Step 4: Verify action is no longer pending
        pending = policy.get_pending_actions()
        assert action_id not in pending

    def test_medium_risk_with_auto_approve_disabled(self, mock_settings, sample_diagnosis):
        """Test medium risk with auto-approve disabled."""
        mock_settings.policy.auto_approve_low_risk = False
        mock_settings.policy.get = Mock(return_value=0.5)
        policy = PolicyEngine(mock_settings)

        sample_diagnosis.risk_level = RiskLevel.MEDIUM
        sample_diagnosis.confidence = 0.9

        decision = policy.get_policy_decision(sample_diagnosis)
        assert decision["decision"] == "REQUIRE_APPROVAL"

    def test_multiple_pending_actions(self, mock_settings, sample_diagnosis):
        """Test tracking multiple pending actions."""
        mock_settings.policy.auto_approve_low_risk = False
        mock_settings.policy.get = Mock(return_value=0.5)
        policy = PolicyEngine(mock_settings)

        sample_diagnosis.risk_level = RiskLevel.HIGH

        # Create multiple pending actions
        decision1 = policy.get_policy_decision(sample_diagnosis)
        action_id_1 = decision1["action_id"]

        decision2 = policy.get_policy_decision(sample_diagnosis)
        action_id_2 = decision2["action_id"]

        # Both should be pending
        pending = policy.get_pending_actions()
        assert action_id_1 in pending
        assert action_id_2 in pending

        # Approve first
        policy.approve_action(action_id_1)
        pending = policy.get_pending_actions()
        assert action_id_1 not in pending
        assert action_id_2 in pending

        # Reject second
        policy.reject_action(action_id_2)
        pending = policy.get_pending_actions()
        assert action_id_2 not in pending


# Risk Assessment Tests
class TestRiskAssessment:
    """Test risk assessment calculations."""

    def test_time_of_day_risk(self, mock_settings):
        """Test time-based risk assessment."""
        mock_settings.policy.get = Mock(return_value=0.5)
        policy = PolicyEngine(mock_settings)

        # Create dummy diagnosis for testing
        symptom = Symptom(
            namespace="default",
            pod_name="test-pod",
            container_name="app",
            failure_type=FailureType.CRASH_LOOP_BACKOFF,
            message="Test",
            restart_count=1,
        )
        diagnosis = Diagnosis(
            symptom=symptom,
            root_cause="Test",
            remediation_type=RemediationType.RESTART_POD,
            risk_level=RiskLevel.LOW,
            confidence=0.9,
            reasoning="Test",
            diagnosed_by="test"
        )

        risk_score = policy._assess_time_of_day_risk()
        assert 0.0 <= risk_score <= 1.0

    def test_frequency_risk(self, mock_settings, sample_diagnosis):
        """Test frequency-based risk assessment."""
        mock_settings.policy.get = Mock(return_value=0.5)
        policy = PolicyEngine(mock_settings)

        # High restart count should increase risk
        sample_diagnosis.symptom.restart_count = 10
        risk_score = policy._assess_frequency_risk(sample_diagnosis)
        assert risk_score > 0.7

    def test_resource_impact_risk(self, mock_settings, sample_diagnosis):
        """Test resource impact risk assessment."""
        mock_settings.policy.get = Mock(return_value=0.5)
        policy = PolicyEngine(mock_settings)

        # Different remediation types should have different risk scores
        sample_diagnosis.remediation_type = RemediationType.RESTART_POD
        restart_risk = policy._assess_resource_impact_risk(sample_diagnosis)

        sample_diagnosis.remediation_type = RemediationType.ROLLBACK_DEPLOYMENT
        rollback_risk = policy._assess_resource_impact_risk(sample_diagnosis)

        assert rollback_risk > restart_risk


# Confidence and Threshold Tests
class TestConfidenceHandling:
    """Test confidence threshold handling."""

    def test_boundary_confidence_values(self, mock_settings, sample_diagnosis):
        """Test confidence at boundary values (0.0, 1.0)."""
        mock_settings.policy.get = Mock(return_value=0.7)
        policy = PolicyEngine(mock_settings)

        # Test at lower boundary
        sample_diagnosis.confidence = 0.0
        authorized, _ = policy.authorize(sample_diagnosis)
        assert not authorized

        # Test at upper boundary
        sample_diagnosis.confidence = 1.0
        sample_diagnosis.risk_level = RiskLevel.LOW
        sample_diagnosis.requires_approval = False
        mock_settings.policy.auto_approve_low_risk = True
        authorized, _ = policy.authorize(sample_diagnosis)
        assert authorized

    def test_confidence_just_below_threshold(self, mock_settings, sample_diagnosis):
        """Test confidence just below threshold."""
        threshold = 0.7
        mock_settings.policy.get = Mock(return_value=threshold)
        policy = PolicyEngine(mock_settings)

        sample_diagnosis.confidence = threshold - 0.01
        authorized, reason = policy.authorize(sample_diagnosis)
        assert not authorized

    def test_confidence_just_above_threshold(self, mock_settings, sample_diagnosis):
        """Test confidence just above threshold."""
        threshold = 0.7
        mock_settings.policy.get = Mock(return_value=threshold)
        mock_settings.policy.auto_approve_low_risk = True
        policy = PolicyEngine(mock_settings)

        sample_diagnosis.confidence = threshold + 0.01
        sample_diagnosis.risk_level = RiskLevel.LOW
        sample_diagnosis.requires_approval = False
        authorized, _ = policy.authorize(sample_diagnosis)
        assert authorized


# Memory Factor Validation Tests
class TestMemoryFactorValidation:
    """Test memory increase factor validation."""

    def test_valid_memory_factors(self, mock_settings, sample_symptom):
        """Test various valid memory factors."""
        diagnoser = LLMDiagnoser(mock_settings)

        valid_factors = [0.1, 0.5, 1.0, 1.5, 2.0, 5.0, 10.0]

        for factor in valid_factors:
            response = {
                "root_cause": "OOM",
                "remediation_type": "increase_memory",
                "remediation_params": {"memory_increase_factor": factor},
                "risk_level": "low",
                "confidence": 0.8,
                "reasoning": "Test",
                "requires_approval": False
            }

            with patch.object(diagnoser.client, 'chat_completion') as mock_client:
                mock_client.return_value = {
                    "choices": [{"message": {"content": json.dumps(response)}}]
                }

                result = diagnoser.diagnose(sample_symptom)
                assert result is not None, f"Factor {factor} should be valid"

    def test_invalid_memory_factors(self, mock_settings, sample_symptom):
        """Test various invalid memory factors."""
        diagnoser = LLMDiagnoser(mock_settings)

        invalid_factors = [0.0, -1.0, 10.1, 15.0, 100.0]

        for factor in invalid_factors:
            response = {
                "root_cause": "OOM",
                "remediation_type": "increase_memory",
                "remediation_params": {"memory_increase_factor": factor},
                "risk_level": "low",
                "confidence": 0.8,
                "reasoning": "Test",
                "requires_approval": False
            }

            with patch.object(diagnoser.client, 'chat_completion') as mock_client:
                mock_client.return_value = {
                    "choices": [{"message": {"content": json.dumps(response)}}]
                }

                result = diagnoser.diagnose(sample_symptom)
                assert result is None, f"Factor {factor} should be invalid"
