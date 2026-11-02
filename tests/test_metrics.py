from k8s_agent.metrics import MetricsRegistry


def test_metrics_registry_renders_prometheus_text():
    registry = MetricsRegistry()
    registry.increment("actions-succeeded-total", 2)
    registry.set_gauge("queue_depth", 3)

    output = registry.render()

    assert "# TYPE k8s_remediation_actions_succeeded_total counter" in output
    assert "k8s_remediation_actions_succeeded_total 2" in output
    assert "# TYPE k8s_remediation_queue_depth gauge" in output
    assert "k8s_remediation_queue_depth 3" in output
