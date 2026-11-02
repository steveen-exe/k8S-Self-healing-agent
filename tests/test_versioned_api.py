from fastapi.testclient import TestClient

from k8s_agent.config import Settings
from k8s_agent.dashboard import api


def test_versioned_metrics_is_available():
    response = TestClient(api.app).get("/api/v1/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")


def test_versioned_operator_api_requires_configuration(monkeypatch):
    monkeypatch.setattr(
        api,
        "shared_settings",
        Settings(ANTHROPIC_API_KEY="test-key", REMEDIATION_API_TOKEN=None),
    )
    response = TestClient(api.app).post("/api/v1/actions/example/approve")

    assert response.status_code == 503


def test_grafana_webhook_requires_secret_configuration(monkeypatch):
    monkeypatch.setattr(
        api,
        "shared_settings",
        Settings(ANTHROPIC_API_KEY="test-key", GRAFANA_WEBHOOK_SECRET=None),
    )
    response = TestClient(api.app).post(
        "/api/v1/alerts/grafana",
        json={"status": "firing", "fingerprint": "fp-1"},
    )

    assert response.status_code == 503
