#!/bin/bash
set -e

echo "Running K8s Remediation Agent Tests"
echo "===================================="

export PYTHONPATH=src

echo "Running diagnosis & policy flow tests..."
python3 -m pytest -xvs tests/test_diagnosis_policy_flow.py

echo ""
echo "Running dashboard tests..."
python3 -m pytest -xvs test_dashboard.py

echo ""
echo "Running LLM endpoint & full flow tests..."
python3 -m pytest -xvs test_llm_and_flow.py

echo ""
echo "All tests passed!"