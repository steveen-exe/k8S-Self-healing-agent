#!/bin/bash
# Test script to run K8s Remediation Agent with minikube

set -e

echo "=========================================="
echo "K8s Remediation Agent - Minikube Test Run"
echo "=========================================="

# Check if minikube is installed
if ! command -v minikube &> /dev/null; then
    echo "❌ minikube is not installed"
    echo "Install with: curl -LO https://github.com/kubernetes/minikube/releases/latest/download/minikube-linux-amd64"
    exit 1
fi

# Check if kubectl is installed
if ! command -v kubectl &> /dev/null; then
    echo "❌ kubectl is not installed"
    echo "Install with: curl -LO https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
    exit 1
fi

echo "✓ Prerequisites found"
echo ""

# Start minikube
echo "🚀 Starting minikube..."
minikube start --driver=docker || echo "⚠️  minikube already running"
echo "✓ minikube started"
echo ""

# Set up kubectl context
echo "📍 Setting kubectl context..."
kubectl config use-context minikube
echo "✓ Context set to minikube"
echo ""

# Create test namespace
echo "📦 Creating test namespace..."
kubectl create namespace test-remediation 2>/dev/null || echo "⚠️  Namespace already exists"
echo "✓ Namespace ready"
echo ""

# Deploy a test pod that will fail
echo "🧪 Deploying test pod (will simulate OOMKilled)..."
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: crash-test-pod
  namespace: test-remediation
spec:
  containers:
  - name: test-container
    image: busybox:1.35
    command: ['sh', '-c', 'sleep 30 && exit 1']
    resources:
      limits:
        memory: "32Mi"
      requests:
        memory: "16Mi"
  restartPolicy: OnFailure
EOF
echo "✓ Test pod deployed"
echo ""

# Wait for pod to be created
echo "⏳ Waiting for pod to be ready..."
sleep 5
kubectl get pod crash-test-pod -n test-remediation
echo ""

# Display cluster info
echo "📊 Cluster Information:"
echo "========================"
echo "Cluster Version:"
kubectl version --short
echo ""
echo "Nodes:"
kubectl get nodes -o wide
echo ""
echo "Namespaces:"
kubectl get namespaces
echo ""
echo "Pods in test-remediation:"
kubectl get pods -n test-remediation -o wide
echo ""

# Show environment info
echo "🔧 Environment Configuration:"
echo "=============================="
echo "Minikube Profile: $(minikube profile list | head -1)"
echo "Docker Driver: docker"
echo ""

# Check API connectivity
echo "🔌 Testing Kubernetes API..."
if kubectl cluster-info &> /dev/null; then
    echo "✓ Kubernetes API is accessible"
else
    echo "❌ Kubernetes API is not accessible"
    exit 1
fi
echo ""

# Show instructions for running the agent
echo "✅ Minikube setup complete!"
echo ""
echo "📋 Next Steps to run the agent:"
echo "================================"
echo ""
echo "1. In a new terminal, set up Python environment:"
echo "   cd /home/user/my-ai-agent"
echo "   source venv/bin/activate  # if using venv"
echo ""
echo "2. Run the agent (from src/):"
echo "   export PYTHONPATH=/home/user/my-ai-agent/src:/home/user/my-ai-agent"
echo "   python -m k8s_agent.main"
echo ""
echo "3. Monitor the test pod:"
echo "   kubectl get pod crash-test-pod -n test-remediation -w"
echo ""
echo "4. View logs:"
echo "   kubectl logs crash-test-pod -n test-remediation"
echo ""
echo "5. Access the dashboard:"
echo "   Open http://localhost:8000 in your browser"
echo ""

echo "🧹 To clean up:"
echo "==============="
echo "kubectl delete namespace test-remediation"
echo "minikube stop"
echo ""
