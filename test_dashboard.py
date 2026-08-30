#!/usr/bin/env python3
"""
Test script to validate dashboard functionality.
This script tests the dashboard components without actually starting the server.
"""

import sys
import os
from pathlib import Path

# Add src to path so we can import modules
sys.path.insert(0, str(Path(__file__).parent / "src"))

def test_imports():
    """Test that all required modules can be imported."""
    print("Testing imports...")

    try:
        from k8s_agent.dashboard.api import app
        print("✓ Dashboard API imports successfully")
    except Exception as e:
        print(f"✗ Dashboard API import failed: {e}")
        return False

    try:
        from k8s_agent.dashboard.models import HistoricalAction, CurrentIssue, PendingAction, ApproveRejectResponse
        print("✓ Dashboard models import successfully")
    except Exception as e:
        print(f"✗ Dashboard models import failed: {e}")
        return False

    try:
        from k8s_agent.policy import PolicyEngine
        print("✓ Policy engine imports successfully")
    except Exception as e:
        print(f"✗ Policy engine import failed: {e}")
        return False

    try:
        from k8s_agent.config import load_settings
        print("✓ Config loader imports successfully")
    except Exception as e:
        print(f"✗ Config loader import failed: {e}")
        return False

    return True

def test_policy_engine_pending_actions():
    """Test that policy engine pending actions functionality works."""
    print("\nTesting policy engine pending actions...")

    try:
        from k8s_agent.policy import PolicyEngine
        from k8s_agent.config import Settings
        from k8s_agent.types import Diagnosis, Symptom, FailureType, RemediationType, RiskLevel

        # Create a minimal settings object with required API key
        import os
        os.environ["ANTHROPIC_API_KEY"] = "test-key-for-testing"
        settings = Settings()

        # Create policy engine
        policy_engine = PolicyEngine(settings)

        # Check that pending actions tracking exists
        assert hasattr(policy_engine, '_pending_actions'), "Policy engine missing _pending_actions attribute"
        assert isinstance(policy_engine._pending_actions, dict), "_pending_actions should be a dict"

        # Check that methods exist
        assert hasattr(policy_engine, 'get_pending_actions'), "Missing get_pending_actions method"
        assert hasattr(policy_engine, 'approve_action'), "Missing approve_action method"
        assert hasattr(policy_engine, 'reject_action'), "Missing reject_action method"

        print("✓ Policy engine pending actions functionality verified")
        return True
    except Exception as e:
        print(f"✗ Policy engine pending actions test failed: {e}")
        return False

def test_dashboard_models():
    """Test that dashboard models work correctly."""
    print("\nTesting dashboard models...")

    try:
        from k8s_agent.dashboard.models import HistoricalAction, CurrentIssue, PendingAction, ApproveRejectResponse

        # Test HistoricalAction
        historical_action = HistoricalAction(
            timestamp="2026-08-29T10:00:00Z",
            event_type="remediation_executed",
            pod_name="test-pod",
            namespace="default",
            remediation_type="restart",
            outcome="success",
            details={"test": "data"}
        )
        assert historical_action.pod_name == "test-pod"
        print("✓ HistoricalAction model works")

        # Test CurrentIssue
        current_issue = CurrentIssue(
            symptom={"pod_name": "test-pod", "namespace": "default"},
            diagnosis={"root_cause": "CrashLoopBackOff", "confidence": 0.9},
            suggested_fix="Restart pod",
            risk_level="low",
            requires_approval=False
        )
        assert current_issue.suggested_fix == "Restart pod"
        print("✓ CurrentIssue model works")

        # Test PendingAction
        pending_action = PendingAction(
            action_id="test-action-123",
            symptom={"pod_name": "test-pod", "namespace": "default"},
            diagnosis={"root_cause": "CrashLoopBackOff", "confidence": 0.9},
            suggested_remediation={"remediation_type": "restart"},
            risk_level="low",
            timestamp="2026-08-29T10:00:00Z"
        )
        assert pending_action.action_id == "test-action-123"
        print("✓ PendingAction model works")

        # Test ApproveRejectResponse
        response = ApproveRejectResponse(
            success=True,
            message="Action approved",
            action_id="test-action-123"
        )
        assert response.success == True
        print("✓ ApproveRejectResponse model works")

        return True
    except Exception as e:
        print(f"✗ Dashboard models test failed: {e}")
        return False

def main():
    """Run all tests."""
    print("Running dashboard functionality tests...\n")

    tests = [
        test_imports,
        test_policy_engine_pending_actions,
        test_dashboard_models
    ]

    passed = 0
    total = len(tests)

    for test in tests:
        if test():
            passed += 1

    print(f"\nResults: {passed}/{total} tests passed")

    if passed == total:
        print("✓ All tests passed! Dashboard functionality appears to be working correctly.")
        return 0
    else:
        print("✗ Some tests failed. Please check the implementation.")
        return 1

if __name__ == "__main__":
    sys.exit(main())