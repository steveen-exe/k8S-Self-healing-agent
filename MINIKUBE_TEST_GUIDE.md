# K8s Remediation Agent - Minikube Test Guide

## Overview
This guide walks you through testing the K8s Remediation Agent with a local minikube cluster.

## Prerequisites

### Required Tools
- **minikube** - Local Kubernetes cluster
- **kubectl** - Kubernetes command-line tool
- **docker** - Container runtime (minikube driver)
- **Python 3.11+** - Agent runtime
- **venv** - Python virtual environment

### Installation

#### macOS (using Homebrew)
```bash
brew install minikube kubectl docker
```

#### Linux (Ubuntu/Debian)
```bash
# minikube
curl -LO https://github.com/kubernetes/minikube/releases/latest/download/minikube-linux-amd64
sudo install minikube-linux-amd64 /usr/local/bin/minikube

# kubectl
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

# Docker (if not installed)
sudo apt-get install docker.io
```

## Step-by-Step Test Procedure

### 1. Start Minikube

```bash
# Start minikube with docker driver
minikube start --driver=docker

# Verify it's running
minikube status
kubectl cluster-info
```

Expected output:
```
minikube: Running
kubelet: Running
apiserver: Running
```

### 2. Run Setup Script

```bash
cd /home/user/my-ai-agent
chmod +x setup_minikube_test.sh
./setup_minikube_test.sh
```

This script will:
- ✅ Verify prerequisites
- ✅ Create test namespace: `test-remediation`
- ✅ Deploy test pod: `crash-test-pod`
- ✅ Show cluster information
- ✅ Verify Kubernetes API connectivity

### 3. Set Up Python Environment

```bash
cd /home/user/my-ai-agent

# Activate virtual environment
source venv/bin/activate

# Verify dependencies are installed
pip install -r requirements.txt
```

### 4. Configure Agent

Create/update `.env` file:
```bash
# .env file already exists, verify:
cat .env
```

Expected content:
```
OMNIROUTE_API_KEY=sk-4dda364fd0661d65-2443ca-483f9c7e
OMNIROUTE_BASE_URL=http://localhost:20128/v1
LLM__MODEL=my-claude-copy
ANTHROPIC_API_KEY=dummy-key-for-validation-only
```

### 5. Run the Agent

In one terminal (agent process):
```bash
cd /home/user/my-ai-agent
export PYTHONPATH=/home/user/my-ai-agent/src:/home/user/my-ai-agent
python -m k8s_agent.main
```

Expected output:
```
INFO - Starting K8s Remediation Agent
INFO - Initializing Kubernetes client
INFO - Watcher started
INFO - Monitoring namespaces...
```

### 6. Run Dashboard (Optional, in another terminal)

```bash
cd /home/user/my-ai-agent
python run_dashboard.py
```

Access at: `http://localhost:8000`

### 7. Monitor Test Pod (in a third terminal)

```bash
# Watch pod status in real-time
kubectl get pod crash-test-pod -n test-remediation -w

# View pod events
kubectl describe pod crash-test-pod -n test-remediation

# View logs
kubectl logs crash-test-pod -n test-remediation
```

## What the Agent Will Do

The agent monitors the cluster and when it detects:

1. **Pod Failure** - `crash-test-pod` will exit with status 1
2. **Diagnosis** - Agent analyzes the failure reason
3. **Policy Decision** - Agent determines remediation action based on policy
4. **Remediation** - If approved, agent applies fix (e.g., restart pod)
5. **Audit Log** - All actions logged to `logs/audit.log`

## Expected Behavior

### Timeline

| Time | Event | Expected Output |
|------|-------|-----------------|
| T+0 | Pod deployed | Pod shows `Running` |
| T+30s | Pod command exits | Pod shows `Error` or `CrashLoopBackOff` |
| T+31s | Agent detects failure | Log: `Detected pod failure` |
| T+32s | Agent diagnoses issue | Log: `Diagnosis complete: Exit code 1` |
| T+33s | Policy decision | Log: `Policy decision: ALLOW_AUTO or REQUIRE_APPROVAL` |
| T+34s | Remediation applied | Pod restarts or policy waits for approval |

### Files to Check

**Agent Logs:**
```bash
tail -f logs/agent.log
```

**Audit Log:**
```bash
tail -f logs/audit.log
```

**Dashboard Events:**
- Open http://localhost:8000
- Click "Recent Issues" tab
- View diagnosed issues and remediation actions
- Click "Action Centre" tab to see full action history

## Test Scenarios

### Scenario 1: Auto-Remediation
```bash
# Pod will crash and agent will auto-restart it
kubectl logs crash-test-pod -n test-remediation | tail
```

Expected: Pod restarts automatically within 30-60 seconds

### Scenario 2: Policy Approval Required
Edit agent configuration to require approval:
```bash
# Modify src/k8s_agent/policy.py
# Set min_confidence_for_auto_fix higher
```

Expected: Agent waits for approval in dashboard

### Scenario 3: Multiple Pod Failures
```bash
# Deploy multiple failing pods
for i in {1..3}; do
  kubectl run fail-$i --image=alpine --restart=Never \
    -n test-remediation -- sh -c 'exit 1'
done
```

Expected: Agent diagnoses all pods and applies policies

## Troubleshooting

### Minikube won't start
```bash
# Check Docker is running
docker ps

# Reset minikube
minikube delete
minikube start --driver=docker
```

### Kubernetes API not accessible
```bash
# Verify kubeconfig
kubectl config current-context

# Switch to minikube context
kubectl config use-context minikube
```

### Agent can't connect to Kubernetes
```bash
# Check kubeconfig location
echo $KUBECONFIG

# Use default location
unset KUBECONFIG
kubectl config view
```

### Pod not showing up
```bash
# Check pod status
kubectl get pod -A

# Describe the pod
kubectl describe pod crash-test-pod -n test-remediation

# Check events
kubectl get events -n test-remediation
```

## Cleanup

```bash
# Delete test namespace
kubectl delete namespace test-remediation

# Stop minikube
minikube stop

# Optional: Delete minikube VM
minikube delete
```

## Success Criteria

✅ **Agent successfully running** if:
- ✅ Minikube starts without errors
- ✅ Test pod deployed successfully
- ✅ Agent detects pod failure
- ✅ Agent applies remediation
- ✅ Audit log shows actions
- ✅ Dashboard displays events

## Next Steps

1. **Verify cluster connectivity** - Run setup_minikube_test.sh
2. **Start the agent** - python -m k8s_agent.main
3. **Watch pod failure** - kubectl get pod crash-test-pod -n test-remediation -w
4. **Check agent logs** - tail -f logs/agent.log
5. **View in dashboard** - http://localhost:8000

## Additional Resources

- [Minikube Documentation](https://minikube.sigs.k8s.io/)
- [Kubectl Cheatsheet](https://kubernetes.io/docs/reference/kubectl/cheatsheet/)
- [K8s Remediation Agent Docs](./README.md)
