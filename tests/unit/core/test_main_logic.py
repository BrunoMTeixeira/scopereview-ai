import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from src.main import app


@pytest.fixture
def disable_webhook_auth(monkeypatch):
    """Disable webhook authentication for testing."""
    from src.api import webhooks
    mock_settings = MagicMock()
    mock_settings.WEBHOOK_SECRET = ""
    mock_settings.is_production = False
    monkeypatch.setattr(webhooks, "settings", mock_settings)


def test_health_endpoint(env_setup):
    """Testa o endpoint de sade dentro do contexto de lifespan."""
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_404_handling(env_setup):
    """Testa o comportamento de rota no encontrada."""
    with TestClient(app) as client:
        response = client.get("/this-does-not-exist")
        assert response.status_code == 404


def test_global_exception_handler_trigger(env_setup, disable_webhook_auth):
    """Testa se o tratador global captura um erro 500 forçado."""
    from src.composition import get_pipeline_orchestrator

    # IMPORTANTE: raise_server_exceptions=False para permitir que o app capture o erro
    with TestClient(app, raise_server_exceptions=False) as client:
        from unittest.mock import MagicMock, patch, PropertyMock
        with patch("src.api.webhooks.injector.get") as mock_get:
            mock_limiter = MagicMock()
            mock_limiter.is_allowed.return_value = True
            mock_get.return_value = mock_limiter
            
            # Force an error by corrupting the state
            client.app.state = MagicMock()
            type(client.app.state).pr_queue = PropertyMock(side_effect=RuntimeError("Forced Error"))
            payload = {
                "eventType": "git.pullrequest.created",
                "resourceVersion": "1.0",
                "resource": {
                    "pullRequestId": 1,
                    "status": "active",
                    "title": "Fix bug",
                    "repository": {
                        "id": "repo1",
                        "name": "my-repo",
                        "project": {"name": "proj1"}
                    }
                }
            }
            response = client.post("/webhook/orchestrate", json=payload)

            assert response.status_code == 500
            assert "Internal server error" in response.json()["error"]

    app.dependency_overrides.clear()
