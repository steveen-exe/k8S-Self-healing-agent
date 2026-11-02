const { useState, useEffect, useRef } = React;

// Simple test app to verify React is working
function App() {
  const [activeTab, setActiveTab] = useState("dashboard");
  const [isConnected, setIsConnected] = useState(false);
  const [pendingApprovals, setPendingApprovals] = useState([]);
  const [historicalActions, setHistoricalActions] = useState([]);
  const [stats, setStats] = useState({
    totalActions: 0,
    successRate: 0,
    pendingApprovals: 0,
    recentTrend: "unknown"
  });

  // Poll for pending approvals and historical actions
  useEffect(() => {
    const fetchData = async () => {
      try {
        const approvalsRes = await fetch("/api/pending-approvals");
        if (approvalsRes.ok) {
          const approvals = await approvalsRes.json();
          setPendingApprovals(approvals);
        }

        const actionsRes = await fetch("/api/historical-actions");
        if (actionsRes.ok) {
          const actions = await actionsRes.json();
          setHistoricalActions(actions);

          // Calculate stats
          const totalActions = actions.length;
          const successfulActions = actions.filter(a => a.success).length;
          const successRate = totalActions > 0 ? (successfulActions / totalActions * 100).toFixed(1) : 0;

          setStats({
            totalActions,
            successRate,
            pendingApprovals: approvals.length,
            recentTrend: "stable"
          });
        }
      } catch (err) {
        console.error("Failed to fetch data:", err);
      }
    };

    // Initial fetch
    fetchData();

    // Set up polling interval
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, []);

  // WebSocket connection with automatic reconnect
  useEffect(() => {
    let ws = null;
    let reconnectTimeout = null;
    let isComponentMounted = true;

    const connectWebSocket = () => {
      if (!isComponentMounted) return;

      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const wsUrl = `${protocol}//${window.location.host}/ws`;

      try {
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
          console.log("WebSocket connected!");
          if (isComponentMounted) {
            setIsConnected(true);
          }
        };

        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            console.log("Received WebSocket event:", data);

            if (isComponentMounted) {
              // Trigger a data refresh when an event is received
              fetch("/api/pending-approvals")
                .then(res => res.ok && res.json())
                .then(approvals => setPendingApprovals(approvals || []))
                .catch(err => console.error("Failed to refresh approvals:", err));

              fetch("/api/historical-actions")
                .then(res => res.ok && res.json())
                .then(actions => setHistoricalActions(actions || []))
                .catch(err => console.error("Failed to refresh actions:", err));
            }
          } catch (err) {
            console.error("Failed to parse WebSocket message:", err);
          }
        };

        ws.onerror = (err) => {
          console.error("WebSocket error:", err);
          if (isComponentMounted) {
            setIsConnected(false);
          }
        };

        ws.onclose = () => {
          console.log("WebSocket connection closed. Attempting reconnect in 3s...");
          if (isComponentMounted) {
            setIsConnected(false);
            reconnectTimeout = setTimeout(connectWebSocket, 3000);
          }
        };
      } catch (err) {
        console.error("Failed to create WebSocket connection:", err);
        if (isComponentMounted) {
          setIsConnected(false);
          reconnectTimeout = setTimeout(connectWebSocket, 5000);
        }
      }
    };

    connectWebSocket();

    return () => {
      isComponentMounted = false;
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
      if (ws) ws.close();
    };
  }, []);

  const [notification, setNotification] = useState(null);

  const showNotification = (message, type = "info") => {
    setNotification({ message, type });
    setTimeout(() => setNotification(null), 4000);
  };

  const handleApprove = async (actionId) => {
    try {
      const res = await fetch(`/api/approve/${actionId}`, { method: "POST" });
      if (res.ok) {
        setPendingApprovals(prev => prev.filter(a => a.action_id !== actionId));
        showNotification(`Action ${actionId.substring(0, 8)} approved successfully`, "success");
      } else {
        const data = await res.json().catch(() => ({}));
        showNotification(`Failed to approve action: ${data.detail || res.statusText}`, "error");
      }
    } catch (err) {
      console.error("Error approving action:", err);
      showNotification(`Error approving action: ${err.message}`, "error");
    }
  };

  const handleReject = async (actionId) => {
    try {
      const res = await fetch(`/api/reject/${actionId}`, { method: "POST" });
      if (res.ok) {
        setPendingApprovals(prev => prev.filter(a => a.action_id !== actionId));
        showNotification(`Action ${actionId.substring(0, 8)} rejected`, "info");
      } else {
        const data = await res.json().catch(() => ({}));
        showNotification(`Failed to reject action: ${data.detail || res.statusText}`, "error");
      }
    } catch (err) {
      console.error("Error rejecting action:", err);
      showNotification(`Error rejecting action: ${err.message}`, "error");
    }
  };

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#f3f4f6" }}>
      {/* Header */}
      <header style={{
        background: "linear-gradient(135deg, #4f46e5, #6366f1)",
        color: "white",
        padding: "1rem",
        boxShadow: "0 4px 6px rgba(0,0,0,0.1)"
      }}>
        <div style={{ maxWidth: "1200px", margin: "0 auto", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h1 style={{ margin: 0, fontSize: "1.5rem" }}>K8s Self-Healing Agent</h1>
          <div>
            <span style={{ marginRight: "1rem" }}>
              WebSocket: <strong style={{ color: isConnected ? "#86efac" : "#fca5a5" }}>
                {isConnected ? "✓ Connected" : "✗ Disconnected"}
              </strong>
            </span>
          </div>
        </div>
      </header>

      {/* Notification banner */}
      {notification && (
        <div style={{
          backgroundColor: notification.type === "error" ? "#fee2e2" : notification.type === "success" ? "#dcfce7" : "#e0e7ff",
          color: notification.type === "error" ? "#991b1b" : notification.type === "success" ? "#166534" : "#3730a3",
          padding: "0.75rem 1.5rem",
          textAlign: "center",
          fontWeight: "500",
          borderBottom: "1px solid rgba(0,0,0,0.1)"
        }}>
          {notification.message}
        </div>
      )}

      {/* Main Content */}
      <div style={{ maxWidth: "1200px", margin: "0 auto", padding: "2rem" }}>
        {/* Tab Navigation */}
        <div style={{ display: "flex", gap: "1rem", marginBottom: "2rem", borderBottom: "2px solid #e5e7eb", paddingBottom: "1rem" }}>
          {["dashboard", "approvals", "history"].map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              style={{
                padding: "0.5rem 1rem",
                background: activeTab === tab ? "#4f46e5" : "transparent",
                color: activeTab === tab ? "white" : "#666",
                border: "none",
                borderRadius: "0.375rem",
                cursor: "pointer",
                fontWeight: activeTab === tab ? "600" : "400",
                textTransform: "capitalize"
              }}
            >
              {tab}
              {tab === "approvals" && pendingApprovals.length > 0 && (
                <span style={{
                  marginLeft: "0.5rem",
                  background: "#ef4444",
                  color: "white",
                  borderRadius: "9999px",
                  padding: "0.125rem 0.5rem",
                  fontSize: "0.75rem",
                  fontWeight: "bold"
                }}>
                  {pendingApprovals.length}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        <div style={{
          background: "white",
          padding: "2rem",
          borderRadius: "0.5rem",
          boxShadow: "0 1px 3px rgba(0,0,0,0.1)"
        }}>
          {activeTab === "dashboard" && (
            <div>
              <h2 style={{ marginTop: 0 }}>Agent Status & Metrics</h2>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "1rem", marginTop: "1rem" }}>
                <div style={{ padding: "1rem", background: "#f9fafb", borderRadius: "0.375rem", border: "1px solid #e5e7eb" }}>
                  <div style={{ fontSize: "0.875rem", color: "#666", marginBottom: "0.5rem" }}>Total Actions</div>
                  <div style={{ fontSize: "2rem", fontWeight: "bold", color: "#1f2937" }}>{stats.totalActions}</div>
                </div>
                <div style={{ padding: "1rem", background: "#f9fafb", borderRadius: "0.375rem", border: "1px solid #e5e7eb" }}>
                  <div style={{ fontSize: "0.875rem", color: "#666", marginBottom: "0.5rem" }}>Success Rate</div>
                  <div style={{ fontSize: "2rem", fontWeight: "bold", color: "#059669" }}>{stats.successRate}%</div>
                </div>
                <div style={{ padding: "1rem", background: "#fef2f2", borderRadius: "0.375rem", border: "1px solid #fee2e2" }}>
                  <div style={{ fontSize: "0.875rem", color: "#666", marginBottom: "0.5rem" }}>Pending Approvals</div>
                  <div style={{ fontSize: "2rem", fontWeight: "bold", color: "#dc2626" }}>{stats.pendingApprovals}</div>
                </div>
                <div style={{ padding: "1rem", background: "#f9fafb", borderRadius: "0.375rem", border: "1px solid #e5e7eb" }}>
                  <div style={{ fontSize: "0.875rem", color: "#666", marginBottom: "0.5rem" }}>Recent Trend</div>
                  <div style={{ fontSize: "1rem", fontWeight: "bold", color: "#1f2937", textTransform: "capitalize" }}>{stats.recentTrend}</div>
                </div>
              </div>
              <div style={{ marginTop: "2rem", padding: "1rem", background: "#eff6ff", borderRadius: "0.375rem", borderLeft: "4px solid #3b82f6" }}>
                <p style={{ margin: 0, color: "#1e40af" }}>
                  <strong>Agent Status:</strong> {isConnected ? "🟢 Active" : "🔴 Disconnected"}
                </p>
              </div>
            </div>
          )}

          {activeTab === "approvals" && (
            <div>
              <h2 style={{ marginTop: 0 }}>Pending Approvals</h2>
              {pendingApprovals.length === 0 ? (
                <p style={{ color: "#6b7280", fontStyle: "italic" }}>No pending approvals</p>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                  {pendingApprovals.map((approval) => (
                    <div
                      key={approval.action_id}
                      style={{
                        padding: "1rem",
                        border: "1px solid #e5e7eb",
                        borderRadius: "0.375rem",
                        background: "#fafafa"
                      }}
                    >
                      <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: "1rem", alignItems: "start" }}>
                        <div>
                          <div style={{ fontWeight: "bold", marginBottom: "0.5rem" }}>
                            {approval.suggested_remediation?.remediation_type || "Unknown remediation"}
                          </div>
                          <div style={{ fontSize: "0.875rem", color: "#666", marginBottom: "0.25rem" }}>
                            <strong>Pod:</strong> {approval.symptom?.pod_name || "N/A"} (Namespace: {approval.symptom?.namespace || "N/A"})
                          </div>
                          <div style={{ fontSize: "0.875rem", color: "#666", marginBottom: "0.25rem" }}>
                            <strong>Risk Level:</strong> <span style={{
                              color: approval.risk_level === "HIGH" ? "#dc2626" : approval.risk_level === "MEDIUM" ? "#ea580c" : "#059669"
                            }}>{approval.risk_level || "Unknown"}</span>
                          </div>
                          <div style={{ fontSize: "0.875rem", color: "#666" }}>
                            <strong>Action ID:</strong> {approval.action_id?.substring(0, 8)}...
                          </div>
                          {approval.risk_factors && (
                            <div style={{ fontSize: "0.875rem", color: "#666", marginTop: "0.5rem" }}>
                              <strong>Risk Factors:</strong>
                              <ul style={{ margin: "0.25rem 0", paddingLeft: "1.25rem" }}>
                                {Object.entries(approval.risk_factors).map(([key, value]) => (
                                  <li key={key}>{key}: {(value * 100).toFixed(1)}%</li>
                                ))}
                              </ul>
                            </div>
                          )}
                        </div>
                        <div style={{ display: "flex", gap: "0.5rem" }}>
                          <button
                            onClick={() => handleApprove(approval.action_id)}
                            style={{
                              padding: "0.5rem 1rem",
                              background: "#059669",
                              color: "white",
                              border: "none",
                              borderRadius: "0.375rem",
                              cursor: "pointer",
                              fontWeight: "500",
                              fontSize: "0.875rem",
                              transition: "background 0.2s"
                            }}
                            onMouseOver={(e) => e.target.style.background = "#047857"}
                            onMouseOut={(e) => e.target.style.background = "#059669"}
                          >
                            ✓ Approve
                          </button>
                          <button
                            onClick={() => handleReject(approval.action_id)}
                            style={{
                              padding: "0.5rem 1rem",
                              background: "#dc2626",
                              color: "white",
                              border: "none",
                              borderRadius: "0.375rem",
                              cursor: "pointer",
                              fontWeight: "500",
                              fontSize: "0.875rem",
                              transition: "background 0.2s"
                            }}
                            onMouseOver={(e) => e.target.style.background = "#b91c1c"}
                            onMouseOut={(e) => e.target.style.background = "#dc2626"}
                          >
                            ✕ Reject
                          </button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {activeTab === "history" && (
            <div>
              <h2 style={{ marginTop: 0 }}>Action History</h2>
              {historicalActions.length === 0 ? (
                <p style={{ color: "#6b7280", fontStyle: "italic" }}>No historical actions</p>
              ) : (
                <div style={{ overflowX: "auto" }}>
                  <table style={{
                    width: "100%",
                    borderCollapse: "collapse",
                    fontSize: "0.875rem"
                  }}>
                    <thead>
                      <tr style={{ background: "#f3f4f6", borderBottom: "2px solid #e5e7eb" }}>
                        <th style={{ padding: "0.75rem", textAlign: "left", fontWeight: "600" }}>Timestamp</th>
                        <th style={{ padding: "0.75rem", textAlign: "left", fontWeight: "600" }}>Pod</th>
                        <th style={{ padding: "0.75rem", textAlign: "left", fontWeight: "600" }}>Remediation</th>
                        <th style={{ padding: "0.75rem", textAlign: "left", fontWeight: "600" }}>Status</th>
                        <th style={{ padding: "0.75rem", textAlign: "left", fontWeight: "600" }}>Approved By</th>
                      </tr>
                    </thead>
                    <tbody>
                      {historicalActions.map((action, idx) => (
                        <tr key={idx} style={{ borderBottom: "1px solid #e5e7eb" }}>
                          <td style={{ padding: "0.75rem" }}>
                            {new Date(action.timestamp).toLocaleString()}
                          </td>
                          <td style={{ padding: "0.75rem" }}>
                            {action.pod_name}
                          </td>
                          <td style={{ padding: "0.75rem" }}>
                            {action.remediation_type}
                          </td>
                          <td style={{ padding: "0.75rem" }}>
                            <span style={{
                              padding: "0.25rem 0.75rem",
                              borderRadius: "9999px",
                              fontSize: "0.75rem",
                              fontWeight: "600",
                              background: action.success ? "#d1fae5" : "#fee2e2",
                              color: action.success ? "#065f46" : "#991b1b"
                            }}>
                              {action.success ? "✓ Success" : "✕ Failed"}
                            </span>
                          </td>
                          <td style={{ padding: "0.75rem" }}>
                            {action.approved_by || "system"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
          )}

          {activeTab === "actions" && (
            <div>
              <h2>⚡ Action Centre</h2>
              <p>Track and manage remediation actions and approvals</p>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "1rem", marginTop: "1rem" }}>
                <div style={{ padding: "1rem", background: "#f9fafb", borderRadius: "0.375rem", borderTop: "4px solid #4f46e5" }}>
                  <div style={{ fontSize: "0.875rem", color: "#666" }}>Total Actions</div>
                  <div style={{ fontSize: "2rem", fontWeight: "bold", color: "#1f2937" }}>0</div>
                </div>
                <div style={{ padding: "1rem", background: "#f9fafb", borderRadius: "0.375rem", borderTop: "4px solid #eab308" }}>
                  <div style={{ fontSize: "0.875rem", color: "#666" }}>Pending Approvals</div>
                  <div style={{ fontSize: "2rem", fontWeight: "bold", color: "#ca8a04" }}>0</div>
                </div>
                <div style={{ padding: "1rem", background: "#f9fafb", borderRadius: "0.375rem", borderTop: "4px solid #10b981" }}>
                  <div style={{ fontSize: "0.875rem", color: "#666" }}>Executed</div>
                  <div style={{ fontSize: "2rem", fontWeight: "bold", color: "#059669" }}>0</div>
                </div>
                <div style={{ padding: "1rem", background: "#f9fafb", borderRadius: "0.375rem", borderTop: "4px solid #ef4444" }}>
                  <div style={{ fontSize: "0.875rem", color: "#666" }}>Denied</div>
                  <div style={{ fontSize: "2rem", fontWeight: "bold", color: "#dc2626" }}>0</div>
                </div>
              </div>
            </div>
          )}

          {activeTab === "approvals" && (
            <div>
              <h2>Pending Approvals</h2>
              <p>Actions waiting for operator approval</p>
              <p style={{ marginTop: "1rem", color: "#666" }}>No pending approvals</p>
            </div>
          )}

          {activeTab === "resources" && (
            <div>
              <h2>Resources</h2>
              <p>Hierarchical view of Kubernetes resources</p>
              <p style={{ marginTop: "1rem", color: "#666" }}>Loading resources...</p>
            </div>
          )}

          {activeTab === "issues" && (
            <div>
              <h2>Recent Issues</h2>
              <p>System decisions and remediation actions from audit log</p>
              <p style={{ marginTop: "1rem", color: "#666" }}>No recent issues found</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Render the app
ReactDOM.createRoot(document.getElementById("root")).render(<App />);
