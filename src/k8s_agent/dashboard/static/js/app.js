const { useState, useEffect, useRef } = React;

// Simple Lucide icons as SVG components
const TrashIcon = () => (
  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
  </svg>
);

const CheckIcon = () => (
  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
  </svg>
);

const RefreshIcon = () => (
  <svg className="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 1121.21 8H18" />
  </svg>
);

const ChartIcon = () => (
  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 100-18 9 9 0 000 18z" />
  </svg>
);

const ActivityIcon = () => (
  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 20h9m-9 0h-9m11-6v-4a1 1 0 00-1-1h-4a1 1 0 00-1-1v-2a1 1 0 011-1h6a1 1 0 011 1v2a1 1 0 001 1h3a1 1 0 011 1v4" />
  </svg>
);

const ShieldIcon = () => (
  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
  </svg>
);

function App() {
  const [events, setEvents] = useState([]);
  const [activeTab, setActiveTab] = useState("approvals");
  const [uptime, setUptime] = useState(0);
  const [isVoiceOpen, setIsVoiceOpen] = useState(false);
  const [voiceMessages, setVoiceMessages] = useState([
    { sender: "system", text: "Voice command seam initialized. Ready for command surface integration." }
  ]);
  const [voiceInput, setVoiceInput] = useState("");
  const [selectedIssue, setSelectedIssue] = useState(null);
  const [notes, setNotes] = useState({});
  const [isConnecting, setIsConnecting] = useState(true);
  const [metrics, setMetrics] = useState({
    totalActions: 0,
    successRate: 0,
    avgResponseTime: 0,
    activeIssues: 0,
    pendingApprovals: 0,
    riskDistribution: { low: 0, medium: 0, high: 0 }
  });
  const [chartData, setChartData] = useState({
    labels: [],
    data: []
  });
  const [isLoading, setIsLoading] = useState(false);

  // Filters state
  const [namespaceFilter, setNamespaceFilter] = useState("");
  const [severityFilter, setSeverityFilter] = useState("");
  const [outcomeFilter, setOutcomeFilter] = useState("");
  const [timeRange, setTimeRange] = useState("24h");

  const wsRef = useRef(null);
  const chartRef = useRef(null);

  // Command surface Dispatcher
  const dispatchCommand = (action) => {
    console.log("Dispatching UI Command:", action);
    const { type, payload } = action;

    switch (type) {
      case "APPROVE_ACTION":
        approveAction(payload.actionId);
        break;
      case "REJECT_ACTION":
        rejectAction(payload.actionId, payload.note);
        break;
      case "DRILL_ISSUE":
        setSelectedIssue(payload.issue);
        break;
      case "CLOSE_DRILL":
        setSelectedIssue(null);
        break;
      case "FILTER_NAMESPACE":
        setNamespaceFilter(payload.namespace);
        break;
      case "FILTER_SEVERITY":
        setSeverityFilter(payload.severity);
        break;
      case "FILTER_OUTCOME":
        setOutcomeFilter(payload.outcome);
        break;
      case "SET_TIME_RANGE":
        setTimeRange(payload.range);
        break;
      case "CLEAR_FILTERS":
        setNamespaceFilter("");
        setSeverityFilter("");
        setOutcomeFilter("");
        setTimeRange("24h");
        break;
      case "REFRESH_DATA":
        refreshMetrics();
        break;
      default:
        console.warn("Unknown command type:", type);
    }
  };

  // Connect to WebSocket on load
  useEffect(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/ws`;

    const connectWs = () => {
      setIsConnecting(true);
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log("WebSocket connected!");
        setIsConnecting(false);
        // Request initial data on connect
        refreshMetrics();
      };

      ws.onmessage = (event) => {
        try {
          const newEvent = JSON.parse(event.data);
          console.log("Received WebSocket Event:", newEvent);
          setEvents((prev) => [newEvent, ...prev]);
          // Update metrics based on new event
          updateMetricsFromEvent(newEvent);
        } catch (e) {
          console.error("Failed to parse WebSocket message:", event.data, e);
        }
      };

      ws.onclose = () => {
        console.log("WebSocket closed. Reconnecting in 3s...");
        setIsConnecting(true);
        setTimeout(connectWs, 3000);
      };

      ws.onerror = (err) => {
        console.error("WebSocket error:", err);
      };
    };

    connectWs();

    // Setup Agent Uptime/Heartbeat mock
    const timer = setInterval(() => {
      setUptime((prev) => prev + 1);
    }, 1000);

    // Refresh metrics every 30 seconds
    const metricsTimer = setInterval(() => {
      refreshMetrics();
    }, 30000);

    return () => {
      if (wsRef.current) wsRef.current.close();
      clearInterval(timer);
      clearInterval(metricsTimer);
    };
  }, []);

  const updateMetricsFromEvent = (event) => {
    setMetrics(prev => {
      const newMetrics = { ...prev };
      newMetrics.totalActions += 1;

      // Update success rate based on event type
      if (event.event_type === "fix_applied") {
        // Recalculate success rate (simplified)
        newMetrics.successRate = Math.min(100, newMetrics.successRate + 2);
      } else if (event.event_type === "fix_failed" || event.event_type === "fix_rejected") {
        newMetrics.successRate = Math.max(0, newMetrics.successRate - 1);
      }

      // Update risk distribution
      if (event.payload && event.payload.risk_level) {
        const risk = event.payload.risk_level;
        if (newMetrics.riskDistribution[risk] !== undefined) {
          newMetrics.riskDistribution[risk] += 1;
        }
      }

      return newMetrics;
    });
  };

  const refreshMetrics = () => {
    setIsLoading(true);
    // Simulate API call to get fresh metrics
    setTimeout(() => {
      // In a real app, this would fetch from /api/metrics endpoint
      setMetrics(prev => ({
        ...prev,
        activeIssues: events.filter(e => e.event_type === "issue_detected").length,
        pendingApprovals: events.filter(e => e.event_type === "fix_proposed" && !events.some(applied => (applied.event_type === "fix_applied" || applied.event_type === "fix_rejected") && applied.payload?.action_id === e.payload?.action_id)).length,
        avgResponseTime: Math.floor(Math.random() * 5) + 1 // Simulated
      }));
      setIsLoading(false);

      // Update chart data
      const labels = Array.from({length: 7}, (_, i) => {
        const date = new Date();
        date.setDate(date.getDate() - i);
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      }).reverse();

      const data = labels.map(() => Math.floor(Math.random() * 10) + 2);

      setChartData({ labels, data });
    }, 800);
  };

  const approveAction = async (actionId) => {
    setIsLoading(true);
    try {
      const response = await fetch(`/api/approve/${actionId}`, { method: "POST" });
      const data = await response.json();
      if (data.success) {
        // Optimistically add success applied event
        const mockApplyEvent = {
          event_type: "fix_applied",
          timestamp: new Date().toISOString(),
          resource: { namespace: "production", kind: "Pod", name: "auth-web-deployment" },
          severity: "low",
          payload: {
            action_id: actionId,
            message: "Applied automatic restart of pod",
            success: true
          }
        };
        setEvents((prev) => [mockApplyEvent, ...prev]);
        // Update metrics
        setMetrics(prev => ({
          ...prev,
          successRate: Math.min(100, prev.successRate + 3),
          pendingApprovals: Math.max(0, prev.pendingApprovals - 1)
        }));
      } else {
        alert("Failed to approve action: " + data.message);
      }
    } catch (e) {
      console.error(e);
      alert("Error approving action");
    } finally {
      setIsLoading(false);
    }
  };

  const rejectAction = async (actionId, note) => {
    setIsLoading(true);
    try {
      const response = await fetch(`/api/reject/${actionId}`, { method: "POST" });
      const data = await response.json();
      if (data.success) {
        // Optimistically add reject event
        const mockRejectEvent = {
          event_type: "fix_rejected",
          timestamp: new Date().toISOString(),
          resource: { namespace: "production", kind: "Pod", name: "auth-web-deployment" },
          severity: "low",
          payload: {
            action_id: actionId,
            reason: note || "No reason specified"
          }
        };
        setEvents((prev) => [mockRejectEvent, ...prev]);
        // Update metrics
        setMetrics(prev => ({
          ...prev,
          successRate: Math.max(0, prev.successRate - 2),
          pendingApprovals: Math.max(0, prev.pendingApprovals - 1)
        }));
      } else {
        alert("Failed to reject action: " + data.message);
      }
    } catch (e) {
      console.error(e);
      alert("Error rejecting action");
    } finally {
      setIsLoading(false);
    }
  };

  // Mock initial actions / populate template events if empty
  useEffect(() => {
    if (events.length === 0) {
      const initialMockEvents = [
        {
          event_type: "fix_proposed",
          timestamp: new Date(Date.now() - 50000).toISOString(),
          resource: { namespace: "production", kind: "Pod", name: "auth-web-5d6f8c4b9-abcde" },
          severity: "high",
          payload: {
            remediation_type: "increase_memory",
            reasoning: "Container was OOMKilled three times in 10 minutes. Diagnosis points to insufficient memory limits.",
            risk_level: "medium",
            changes: "resources.limits.memory: 256Mi -> 512Mi",
            action_id: "action-1092"
          }
        },
        {
          event_type: "issue_detected",
          timestamp: new Date(Date.now() - 100000).toISOString(),
          resource: { namespace: "production", kind: "Pod", name: "auth-web-5d6f8c4b9-abcde" },
          severity: "high",
          payload: {
            failure_type: "OOMKilled",
            restart_count: 3,
            logs: "FATAL ERROR: Ineffective mark-compacts near heap limit Allocation failed - JavaScript heap out of memory"
          }
        },
        {
          event_type: "fix_applied",
          timestamp: new Date(Date.now() - 300000).toISOString(),
          resource: { namespace: "staging", kind: "Pod", name: "nginx-ingress-controller-abcde" },
          severity: "low",
          payload: {
            remediation_type: "restart_pod",
            message: "Successfully deleted and restarted the pod.",
            success: true
          }
        },
        {
          event_type: "issue_detected",
          timestamp: new Date(Date.now() - 400000).toISOString(),
          resource: { namespace: "staging", kind: "Pod", name: "nginx-ingress-controller-abcde" },
          severity: "medium",
          payload: {
            failure_type: "CrashLoopBackOff",
            restart_count: 5,
            logs: "Error loading configuration: /etc/nginx/nginx.conf line 22."
          }
        },
        {
          event_type: "fix_applied",
          timestamp: new Date(Date.now() - 600000).toISOString(),
          resource: { namespace: "default", kind: "Deployment", name: "api-server" },
          severity: "low",
          payload: {
            remediation_type: "rollback_deployment",
            message: "Rolled back to previous stable version",
            success: true
          }
        }
      ];
      setEvents(initialMockEvents);
      // Initialize metrics based on mock data
      setMetrics({
        totalActions: 5,
        successRate: 80,
        avgResponseTime: 2,
        activeIssues: 2,
        pendingApprovals: 1,
        riskDistribution: { low: 2, medium: 2, high: 1 }
      });

      // Initialize chart data
      const labels = Array.from({length: 7}, (_, i) => {
        const date = new Date();
        date.setDate(date.getDate() - i);
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      }).reverse();

      const data = [3, 5, 2, 8, 4, 6, 7];

      setChartData({ labels, data });
    }
  }, [events]);

  // Derived states from in-memory events
  const pendingApprovals = events.filter(e => e.event_type === "fix_proposed" && !events.some(applied => (applied.event_type === "fix_applied" || applied.event_type === "fix_rejected") && applied.payload?.action_id === e.payload?.action_id));
  const activeIssues = events.filter(e => e.event_type === "issue_detected");
  const resolvedIssues = events.filter(e => e.event_type === "fix_applied" || e.event_type === "fix_rejected");

  // Filtering for compliance log
  const filteredEvents = events.filter(e => {
    if (namespaceFilter && e.resource.namespace !== namespaceFilter) return false;
    if (severityFilter && e.severity !== severityFilter) return false;
    if (outcomeFilter) {
      if (outcomeFilter === "success" && e.event_type !== "fix_applied") return false;
      if (outcomeFilter === "failed" && e.event_type !== "fix_failed") return false;
      if (outcomeFilter === "rejected" && e.event_type !== "fix_rejected") return false;
      if (outcomeFilter === "pending" && !(e.event_type === "fix_proposed" && !events.some(applied => (applied.event_type === "fix_applied" || applied.event_type === "fix_rejected") && applied.payload?.action_id === e.payload?.action_id))) return false;
    }

    // Time range filtering
    const now = new Date();
    let cutoffTime;
    switch (timeRange) {
      case "1h": cutoffTime = new Date(now.getTime() - 60 * 60 * 1000); break;
      case "6h": cutoffTime = new Date(now.getTime() - 6 * 60 * 60 * 1000); break;
      case "24h": cutoffTime = new Date(now.getTime() - 24 * 60 * 60 * 1000); break;
      case "7d": cutoffTime = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000); break;
      default: cutoffTime = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    }

    const eventTime = new Date(e.timestamp);
    return eventTime >= cutoffTime;
  });

  const getSeverityColor = (sev) => {
    switch (sev) {
      case "high": return "bg-red-100 text-red-800 border-red-200";
      case "medium": return "bg-yellow-100 text-yellow-800 border-yellow-200";
      case "low": return "bg-green-100 text-green-800 border-green-200";
      default: return "bg-gray-100 text-gray-800 border-gray-200";
    }
  };

  const getSeverityTextColor = (sev) => {
    switch (sev) {
      case "high": return "text-red-600";
      case "medium": return "text-yellow-600";
      case "low": return "text-green-600";
      default: return "text-gray-600";
    }
  };

  const formatUptime = (seconds) => {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;

    if (days > 0) return `${days}d ${hours}h ${mins}m`;
    if (hours > 0) return `${hours}h ${mins}m ${secs}s`;
    if (mins > 0) return `${mins}m ${secs}s`;
    return `${secs}s`;
  };

  // Mocking voice recognition/processing command
  const handleVoiceCommand = (e) => {
    e.preventDefault();
    if (!voiceInput.trim()) return;

    const commandText = voiceInput.toLowerCase();
    const userMsg = { sender: "user", text: voiceInput };
    setVoiceMessages((prev) => [...prev, userMsg]);
    setVoiceInput("");

    setTimeout(() => {
      if (commandText.includes("approve") && commandText.includes("1092")) {
        dispatchCommand({ type: "APPROVE_ACTION", payload: { actionId: "action-1092" } });
        setVoiceMessages((prev) => [...prev, { sender: "agent", text: "Approved proposed action 1092 out loud." }]);
      } else if (commandText.includes("filter") && commandText.includes("production")) {
        dispatchCommand({ type: "FILTER_NAMESPACE", payload: { namespace: "production" } });
        setVoiceMessages((prev) => [...prev, { sender: "agent", text: "Namespace filter applied: production" }]);
      } else if (commandText.includes("show") && commandText.includes("metrics")) {
        setActiveTab("metrics");
        setVoiceMessages((prev) => [...prev, { sender: "agent", text: "Switching to metrics dashboard" }]);
      } else if (commandText.includes("clear")) {
        dispatchCommand({ type: "CLEAR_FILTERS" });
        setVoiceMessages((prev) => [...prev, { sender: "agent", text: "All event logs and filters cleared." }]);
      } else if (commandText.includes("refresh")) {
        dispatchCommand({ type: "REFRESH_DATA" });
        setVoiceMessages((prev) => [...prev, { sender: "agent", text: "Refreshing dashboard data" }]);
      } else {
        setVoiceMessages((prev) => [...prev, { sender: "agent", text: `Unrecognized command seam input: "${commandText}". Valid options: "approve 1092", "filter production", "show metrics", "clear", "refresh"` }]);
      }
    }, 800);
  };

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col font-sans">
      {/* Header / Nav */}
      <header className="bg-gradient-to-br from-indigo-600 to-indigo-800 text-white shadow-md">
        <div className="max-w-7xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="w-5 h-5 bg-indigo-400 rounded-full animate-ping"></div>
            <h1 className="text-xl font-bold tracking-wide flex items-center gap-2">
              K8s Self-Healing Agent
              <span className="text-xs bg-indigo-700 px-2 py-0.5 rounded">v0.2.0</span>
            </h1>
          </div>
          <div className="flex items-center space-x-4">
            <span className="text-xs text-indigo-200">WS Connection: {isConnecting ? <span className="text-red-400 font-semibold">Connecting...</span> : <span className="text-indigo-400 font-semibold underline">CONNECTED</span>}</span>
            <div className="relative">
              <button
                onClick={() => setIsVoiceOpen(!isVoiceOpen)}
                className="bg-indigo-600 hover:bg-indigo-700 px-3 py-1.5 rounded text-sm font-semibold flex items-center space-x-2 transition"
              >
                <span>🎤 Voice Seam</span>
                {!isVoiceOpen && <span className="absolute -top-1 -right-1 w-2 h-2 bg-green-500 rounded-full animate-ping"></span>}
              </button>
              {isVoiceOpen && <div className="absolute -top-2 -right-2 w-4 h-4 bg-red-500 rounded-full animate-ping"></div>}
            </div>
          </div>
        </div>
      </header>

      {/* Main Grid Info */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 py-6 grid grid-cols-1 lg:grid-cols-4 gap-6">

        {/* Left Side: Summary Widget / Stats */}
        <div className="lg:col-span-1 space-y-6">
          <div className="bg-white rounded-xl shadow border border-gray-200 p-5 space-y-4">
            <h2 className="text-lg font-bold text-gray-900 border-b pb-2 flex items-center gap-2">
              <ShieldIcon className="w-4 h-4" /> Cluster Overview
            </h2>
            <div className="grid grid-cols-2 gap-4">
              <div className="bg-gray-50 rounded-lg p-3 text-center border">
                <span className="block text-xs uppercase tracking-wider font-semibold text-gray-500">Active Issues</span>
                <span className="text-2xl font-black text-slate-800">{activeIssues.length}</span>
              </div>
              <div className="bg-teal-50 rounded-lg p-3 text-center border border-teal-200">
                <span className="block text-xs uppercase tracking-wider font-semibold text-teal-600">Pending Approvals</span>
                <span className="text-2xl font-black text-teal-800">{pendingApprovals.length}</span>
              </div>
            </div>
            <div className="space-y-2 mt-4 text-sm text-gray-600">
              <div className="flex justify-between">
                <span>Agent Uptime:</span>
                <span className="font-semibold text-gray-800">{formatUptime(uptime)}</span>
              </div>
              <div className="flex justify-between">
                <span>Namespaces:</span>
                <span className="font-semibold text-gray-800">default, production, staging</span>
              </div>
              <div className="flex justify-between">
                <span>Active Channels:</span>
                <span className="font-semibold text-gray-800">WS Gateway v1</span>
              </div>
              <div className="flex justify-between">
                <span>Success Rate:</span>
                <span className="font-semibold text-gray-800">{metrics.successRate}%</span>
              </div>
            </div>
          </div>

          {/* Enhanced Metrics Cards */}
          <div className="bg-white rounded-xl shadow border border-gray-200 p-5">
            <h3 className="font-bold text-gray-900 mb-3 text-sm flex items-center gap-2">
              <ChartIcon className="w-4 h-4" /> Real-time Metrics
            </h3>
            <div className="stats-grid">
              <div className="stat-card primary">
                <div className="stat-icon">
                  <ActivityIcon className="w-5 h-5 text-indigo-600" />
                </div>
                <div className="stat-value">{metrics.totalActions}</div>
                <div className="stat-label">Total Actions</div>
                <div className="stat-trend positive">
                  <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M12 4l-2 8h9l-3-7zm-1 9V6l3 3H9z" />
                  </svg>
                  <span className="ml-1">+12%</span>
                </div>
              </div>
              <div className="stat-card success">
                <div className="stat-icon">
                  <CheckIcon className="w-5 h-5 text-green-600" />
                </div>
                <div className="stat-value">{metrics.successRate}%</div>
                <div className="stat-label">Success Rate</div>
                <div className="stat-trend positive">
                  <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M12 4l-2 8h9l-3-7zm-1 9V6l3 3H9z" />
                  </svg>
                  <span className="ml-1">+5%</span>
                </div>
              </div>
              <div className="stat-card warning">
                <div className="stat-icon">
                  <RefreshIcon className="w-5 h-5 text-yellow-600" />
                </div>
                <div className="stat-value">{metrics.avgResponseTime}s</div>
                <div className="stat-label">Avg Response</div>
                <div className="stat-trend negative">
                  <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M12 4l-2 8h9l-3-7zm-1 9V6l3 3H9z" />
                  </svg>
                  <span className="ml-1">-0.5s</span>
                </div>
              </div>
              <div className="stat-card info">
                <div className="stat-icon">
                  <ShieldIcon className="w-5 h-5 text-blue-600" />
                </div>
                <div className="stat-value">{metrics.pendingApprovals}</div>
                <div className="stat-label">Pending</div>
                <div className="stat-trend negative">
                  <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M12 4l-2 8h9l-3-7zm-1 9V6l3 3H9z" />
                  </svg>
                  <span className="ml-1">-2</span>
                </div>
              </div>
            </div>
          </div>

          {/* Risk Distribution */}
          <div className="bg-white rounded-xl shadow border border-gray-200 p-5">
            <h3 className="font-bold text-gray-900 mb-3 text-sm flex items-center gap-2">
              <ShieldIcon className="w-4 h-4" /> Risk Distribution
            </h3>
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-gray-600">Low Risk</span>
                <span className="text-sm font-semibold">{metrics.riskDistribution.low}</span>
              </div>
              <div className="w-full bg-gray-200 rounded-full h-2.5">
                <div className="bg-green-500 h-2.5 rounded-full" style={{ width: `${Math.min(100, (metrics.riskDistribution.low / Math.max(1, metrics.riskDistribution.low + metrics.riskDistribution.medium + metrics.riskDistribution.high)) * 100)}%` }}></div>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-gray-600">Medium Risk</span>
                <span className="text-sm font-semibold">{metrics.riskDistribution.medium}</span>
              </div>
              <div className="w-full bg-gray-200 rounded-full h-2.5">
                <div className="bg-yellow-500 h-2.5 rounded-full" style={{ width: `${Math.min(100, (metrics.riskDistribution.medium / Math.max(1, metrics.riskDistribution.low + metrics.riskDistribution.medium + metrics.riskDistribution.high)) * 100)}%` }}></div>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-gray-600">High Risk</span>
                <span className="text-sm font-semibold">{metrics.riskDistribution.high}</span>
              </div>
              <div className="w-full bg-gray-200 rounded-full h-2.5">
                <div className="bg-red-500 h-2.5 rounded-full" style={{ width: `${Math.min(100, (metrics.riskDistribution.high / Math.max(1, metrics.riskDistribution.low + metrics.riskDistribution.medium + metrics.riskDistribution.high)) * 100)}%` }}></div>
              </div>
            </div>
          </div>

          {/* Quick filter tooltips */}
          <div className="bg-white rounded-xl shadow border border-gray-200 p-5">
            <h3 className="font-bold text-gray-900 mb-3 text-sm flex items-center gap-2">
              <ActivityIcon className="w-4 h-4" /> Target Filters
            </h3>
            <div className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-gray-500 mb-1">Namespace</label>
                <select
                  value={namespaceFilter}
                  onChange={(e) => dispatchCommand({ type: "FILTER_NAMESPACE", payload: { namespace: e.target.value } })}
                  className="w-full text-sm bg-gray-50 border rounded p-1.5"
                >
                  <option value="">All Namespaces</option>
                  <option value="production">production</option>
                  <option value="staging">staging</option>
                  <option value="default">default</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-500 mb-1">Severity</label>
                <select
                  value={severityFilter}
                  onChange={(e) => dispatchCommand({ type: "FILTER_SEVERITY", payload: { severity: e.target.value } })}
                  className="w-full text-sm bg-gray-50 border rounded p-1.5"
                >
                  <option value="">All</option>
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-500 mb-1">Outcome</label>
                <select
                  value={outcomeFilter}
                  onChange={(e) => dispatchCommand({ type: "FILTER_OUTCOME", payload: { outcome: e.target.value } })}
                  className="w-full text-sm bg-gray-50 border rounded p-1.5"
                >
                  <option value="">All Outcomes</option>
                  <option value="success">Success</option>
                  <option value="failed">Failed</option>
                  <option value="rejected">Rejected</option>
                  <option value="pending">Pending Approval</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-500 mb-1">Time Range</label>
                <select
                  value={timeRange}
                  onChange={(e) => dispatchCommand({ type: "SET_TIME_RANGE", payload: { range: e.target.value } })}
                  className="w-full text-sm bg-gray-50 border rounded p-1.5"
                >
                  <option value="1h">Last Hour</option>
                  <option value="6h">Last 6 Hours</option>
                  <option value="24h">Last 24 Hours</option>
                  <option value="7d">Last 7 Days</option>
                </select>
              </div>
              <button
                onClick={() => dispatchCommand({ type: "CLEAR_FILTERS" })}
                className="w-full text-xs font-semibold border hover:bg-gray-55 bg-gray-100 text-gray-800 py-2 rounded text-center transition"
              >
                Clear all filters
              </div>
            </div>
          </div>
        </div>

        {/* Center/Right Feed Views */}
        <div className="lg:col-span-3 space-y-6">
          {/* Tab Selection */}
          <div className="flex border-b border-gray-200 rounded-xl overflow-hidden shadow-sm">
            <button
              onClick={() => setActiveTab("approvals")}
              className={`flex-1 py-3 px-6 font-bold text-sm border-b-2 tracking-wide transition ${activeTab === 'approvals' ? 'border-indigo-600 text-indigo-700 bg-indigo-50' : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50'}`}
            >
              Pending Approvals Queue ({pendingApprovals.length})
            </button>
            <button
              onClick={() => setActiveTab("issues")}
              className={`flex-1 py-3 px-6 font-bold text-sm border-b-2 tracking-wide transition ${activeTab === 'issues' ? 'border-indigo-600 text-indigo-700 bg-indigo-50' : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50'}`}
            >
              Live Issues Feed ({activeIssues.length})
            </button>
            <button
              onClick={() => setActiveTab("history")}
              className={`flex-1 py-3 px-6 font-bold text-sm border-b-2 tracking-wide transition ${activeTab === 'history' ? 'border-indigo-600 text-indigo-700 bg-indigo-50' : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50'}`}
            >
              Compliance / Audit Log ({events.length})
            </button>
            <button
              onClick={() => setActiveTab("metrics")}
              className={`flex-1 py-3 px-6 font-bold text-sm border-b-2 tracking-wide transition ${activeTab === 'metrics' ? 'border-indigo-600 text-indigo-700 bg-indigo-50' : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50'}`}
            >
              Analytics Dashboard
            </button>
          </div>

          {/* Pending Approvals queue tab */}
          {activeTab === "approvals" && (
            <div className="space-y-4">
              {pendingApprovals.length === 0 ? (
                <div className="bg-white rounded-xl shadow border border-gray-200 p-8 text-center text-gray-500 fade-in">
                  <div className="empty-state-icon">
                    <ActivityIcon className="w-12 h-12 text-gray-400" />
                  </div>
                  <h3 className="font-semibold text-gray-900 mb-2">No proposed fixes waiting</h3>
                  <p className="text-gray-600">All systems are operating normally. No remediation actions require approval at this time.</p>
                </div>
              ) : (
                pendingApprovals.map((e) => (
                  <div key={e.payload.action_id} className="bg-white rounded-xl shadow border-l-4 border-indigo-500 border-t border-b border-r p-6 space-y-4 relative fade-in slide-in">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2">
                        <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-indigo-100 text-indigo-800">
                          {e.payload.risk_level.toUpperCase()} RISK
                        </span>
                        <span className="text-sm font-semibold text-slate-700">{e.resource.namespace} / {e.resource.name}</span>
                      </div>
                      <span className="text-xs text-gray-400">{new Date(e.timestamp).toLocaleString()}</span>
                    </div>

                    <div className="space-y-2">
                      <h3 className="text-lg font-bold text-gray-900">Suggested Action: <code className="text-indigo-700 bg-gray-50 px-2 py-1 rounded">{e.payload.remediation_type}</code></h3>
                      <p className="text-sm text-gray-700">{e.payload.reasoning}</p>

                      {e.payload.changes && (
                        <div className="mt-3 bg-gray-50 rounded-lg p-3 text-xs font-mono text-gray-700 border">
                          <span className="block font-semibold text-gray-500 mb-1 border-b pb-1">Proposed YAML Diff / Config Change:</span>
                          {e.payload.changes}
                        </div>
                      )}
                    </div>

                    <div className="pt-2 flex items-center justify-between border-t mt-4">
                      {/* Reason input for rejected action */}
                      <input
                        type="text"
                        placeholder="Add note for rejection..."
                        value={notes[e.payload.action_id] || ""}
                        onChange={(el) => setNotes({ ...notes, [e.payload.action_id]: el.target.value })}
                        className="text-xs bg-gray-50 border rounded p-2 w-1/2"
                      />
                      <div className="space-x-2 flex">
                        <button
                          onClick={() => dispatchCommand({ type: "REJECT_ACTION", payload: { actionId: e.payload.action_id, note: notes[e.payload.action_id] } })}
                          className="bg-red-50 hover:bg-red-100 text-red-700 border border-red-200 px-4 py-2 rounded text-xs font-bold transition flex items-center space-x-1"
                        >
                          <TrashIcon />
                          <span>Reject Fix</span>
                        </button>
                        <button
                          onClick={() => dispatchCommand({ type: "APPROVE_ACTION", payload: { actionId: e.payload.action_id } })}
                          className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded text-xs font-bold shadow transition flex items-center space-x-1"
                        >
                          <CheckIcon />
                          <span>Approve & Apply</span>
                        </button>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Live issue stream feed */}
          {activeTab === "issues" && (
            <div className="space-y-4">
              {activeIssues.length === 0 ? (
                <div className="bg-white rounded-xl shadow border border-gray-200 p-8 text-center text-gray-500 fade-in">
                  <div className="empty-state-icon">
                    <ShieldIcon className="w-12 h-12 text-gray-400" />
                  </div>
                  <h3 className="font-semibold text-gray-900 mb-2">No issues currently detected</h3>
                  <p className="text-gray-600">All monitored namespaces are healthy. The agent is actively watching for failure symptoms.</p>
                </div>
              ) : (
                activeIssues.map((e, index) => (
                  <div key={index} className="bg-white rounded-xl shadow border border-gray-200 overflow-hidden fade-in slide-in">
                    <div className={`px-5 py-3 border-b flex items-center justify-between ${getSeverityColor(e.severity)}`}>
                      <div className="flex items-center space-x-2">
                        <span className="font-bold text-sm uppercase">{e.severity} Severity</span>
                        <span className="text-sm font-semibold">{e.resource.namespace} / {e.resource.name}</span>
                      </div>
                      <span className="text-xs font-medium">{new Date(e.timestamp).toLocaleString()}</span>
                    </div>
                    <div className="p-5 space-y-3">
                      <div className="flex items-baseline justify-between">
                        <h4 className="font-semibold text-gray-900">Failure Detected: <span className="font-mono text-red-600 bg-red-50 px-1.5 py-0.5 rounded">{e.payload.failure_type}</span></h4>
                        <span className="text-xs text-gray-500">Restart count: <span className="font-bold text-gray-800">{e.payload.restart_count}</span></span>
                      </div>

                      {/* Diagnostic Logs */}
                      {e.payload.logs && (
                        <div className="bg-slate-900 text-slate-100 rounded-lg p-4 font-mono text-xs overflow-x-auto shadow-inner border border-slate-700 max-h-60">
                          <span className="block text-slate-500 font-semibold mb-2 border-b border-slate-800 pb-1 select-none">CONTAINER TAIL LOGS</span>
                          {e.payload.logs}
                        </div>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Compliance Audit Log list */}
          {activeTab === "history" && (
            <div className="bg-white rounded-xl shadow border border-gray-200 overflow-hidden">
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Timestamp</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Event Type</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Resource Path</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Severity</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Action / Outcome</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Details</th>
                    </tr>
                  </thead>
                  <tbody className="bg-white divide-y divide-gray-200 text-sm">
                    {filteredEvents.map((e, index) => (
                      <tr key={index} className="hover:bg-gray-50">
                        <td className="px-6 py-4 whitespace-nowrap text-xs text-gray-500">
                          {new Date(e.timestamp).toLocaleTimeString()}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap font-bold text-xs uppercase tracking-wide">
                          <span className={`px-2 py-1 rounded ${e.event_type.startsWith('fix_applied') ? 'bg-green-100 text-green-800' : e.event_type.startsWith('fix_rejected') ? 'bg-red-100 text-red-800' : e.event_type.startsWith('fix_proposed') ? 'bg-yellow-100 text-yellow-800' : 'bg-gray-100 text-gray-700'}`}>
                            {e.event_type.replace('_', ' ')}
                          </span>
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap font-mono text-xs text-gray-700">
                          {e.resource.namespace}/{e.resource.name}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap">
                          <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${getSeverityColor(e.severity)}`}>
                            {e.severity}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-gray-600 max-w-sm truncate text-xs">
                          {e.payload.remediation_type || e.payload.failure_type || 'N/A'}
                        </td>
                        <td className="px-6 py-4 text-gray-600 max-w-xs truncate text-xs">
                          {JSON.stringify(e.payload).length > 50 ? JSON.stringify(e.payload).substring(0, 50) + '...' : JSON.stringify(e.payload)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {filteredEvents.length === 0 && (
                <div className="bg-white rounded-xl shadow border border-gray-200 p-8 text-center text-gray-500">
                  <div className="empty-state-icon">
                    <ActivityIcon className="w-12 h-12 text-gray-400" />
                  </div>
                  <h3 className="font-semibold text-gray-900 mb-2">No events match current filters</h3>
                  <p className="text-gray-600">Try adjusting your filters or time range to see historical data.</p>
                </div>
              )}
            </div>
          )}

          {/* Analytics Dashboard */}
          {activeTab === "metrics" && (
            <div className="space-y-6">
              {/* Charts Row */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Action Trends Chart */}
                <div className="bg-white rounded-xl shadow border border-gray-200 p-5">
                  <h3 className="font-bold text-gray-900 mb-4 flex items-center gap-2">
                    <ChartIcon className="w-4 h-4" /> Action Trends (7 Days)
                  </h3>
                  <div className="chart-container">
                    <canvas id="trendsChart" ref={chartRef} width="400" height="200"></canvas>
                  </div>
                </div>

                {/* Issue Distribution */}
                <div className="bg-white rounded-xl shadow border border-gray-200 p-5">
                  <h3 className="font-bold text-gray-900 mb-4 flex items-center gap-2">
                    <ShieldIcon className="w-4 h-4" /> Issue Distribution
                  </h3>
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-gray-600">CrashLoopBackOff</span>
                      <span className="text-sm font-semibold">{activeIssues.filter(e => e.payload.failure_type === 'CrashLoopBackOff').length}</span>
                    </div>
                    <div className="w-full bg-gray-200 rounded-full h-3">
                      <div className="bg-red-500 h-3 rounded-full" style={{ width: `${Math.min(100, (activeIssues.filter(e => e.payload.failure_type === 'CrashLoopBackOff').length / Math.max(1, activeIssues.length)) * 100)}%` }}></div>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-gray-600">OOMKilled</span>
                      <span className="text-sm font-semibold">{activeIssues.filter(e => e.payload.failure_type === 'OOMKilled').length}</span>
                    </div>
                    <div className="w-full bg-gray-200 rounded-full h-3">
                      <div className="bg-orange-500 h-3 rounded-full" style={{ width: `${Math.min(100, (activeIssues.filter(e => e.payload.failure_type === 'OOMKilled').length / Math.max(1, activeIssues.length)) * 100)}%` }}></div>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-gray-600">ImagePullBackOff</span>
                      <span className="text-sm font-semibold">{activeIssues.filter(e => e.payload.failure_type === 'ImagePullBackOff').length}</span>
                    </div>
                    <div className="w-full bg-gray-200 rounded-full h-3">
                      <div className="bg-yellow-500 h-3 rounded-full" style={{ width: `${Math.min(100, (activeIssues.filter(e => e.payload.failure_type === 'ImagePullBackOff').length / Math.max(1, activeIssues.length)) * 100)}%` }}></div>
                    </div>
                  </div>
                </div>
              </div>

              {/* System Health */}
              <div className="bg-white rounded-xl shadow border border-gray-200 p-5">
                <h3 className="font-bold text-gray-900 mb-4 flex items-center gap-2">
                  <ActivityIcon className="w-4 h-4" /> System Health
                </h3>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-gray-600">Memory Usage</span>
                      <span className="text-sm font-semibold">45%</span>
                    </div>
                    <div className="w-full bg-gray-200 rounded-full h-2.5">
                      <div className="bg-blue-500 h-2.5 rounded-full" style={{ width: '45%' }}></div>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-gray-600">CPU Usage</span>
                      <span className="text-sm font-semibold">32%</span>
                    </div>
                    <div className="w-full bg-gray-200 rounded-full h-2.5">
                      <div className="bg-green-500 h-2.5 rounded-full" style={{ width: '32%' }}></div>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-gray-600">Network I/O</span>
                      <span className="text-sm font-semibold">1.2 MB/s</span>
                    </div>
                    <div className="w-full bg-gray-200 rounded-full h-2.5">
                      <div className="bg-indigo-500 h-2.5 rounded-full" style={{ width: '60%' }}></div>
                    </div>
                  </div>
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-gray-600">Disk Usage</span>
                      <span className="text-sm font-semibold">68%</span>
                    </div>
                    <div className="w-full bg-gray-200 rounded-full h-2.5">
                      <div className="bg-yellow-500 h-2.5 rounded-full" style={{ width: '68%' }}></div>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-gray-600">Uptime</span>
                      <span className="text-sm font-semibold">{formatUptime(uptime)}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-gray-600">Last Check</span>
                      <span className="text-sm font-semibold">{new Date().toLocaleTimeString()}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

      </main>

      {/* Voice Assistant Collapsible Side Panel (Reserved Seam) */}
      {isVoiceOpen && (
        <div className="fixed inset-y-0 right-0 w-80 bg-white shadow-2xl border-l flex flex-col z-50 transition-all duration-300">
          <div className="bg-indigo-800 text-white p-4 flex items-center justify-between">
            <h3 className="font-bold text-sm tracking-wide">Voice Seam / Command Panel</h3>
            <button
              onClick={() => setIsVoiceOpen(false)}
              className="text-indigo-200 hover:text-white font-bold"
            >
              ×
            </button>
          </div>

          <div className="flex-1 p-4 overflow-y-auto space-y-4 text-xs font-mono">
            {voiceMessages.map((m, i) => (
              <div key={i} className={`p-2.5 rounded-lg border ${m.sender === 'user' ? 'bg-indigo-50 border-indigo-100 self-end text-indigo-800' : m.sender === 'agent' ? 'bg-emerald-50 border-emerald-100 text-emerald-800' : 'bg-gray-50 border-gray-100 text-gray-600'}`}>
                <span className="block font-bold mb-1 opacity-75">{m.sender.toUpperCase()}:</span>
                {m.text}
              </div>
            ))}
          </div>

          <form onSubmit={handleVoiceCommand} className="p-4 border-t bg-gray-50">
            <input
              type="text"
              placeholder="Speak command here (e.g. approve 1092, show metrics, clear)..."
              value={voiceInput}
              onChange={(e) => setVoiceInput(e.target.value)}
              className="w-full text-xs p-2 border bg-white rounded shadow-sm focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </form>
        </div>
      )}
    </div>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);