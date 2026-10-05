import pytest
from fastapi.testclient import TestClient
from src.main import app
from src.core.di import injector
from unittest.mock import MagicMock, patch
from src.services.orchestrator import PipelineOrchestrator

# client = TestClient(app)  <-- REMOVED GLOBAL CLIENT

class MockOrchestrator:
    def process_pr_pipeline(self, pr_id: int, repo_id: str, project: str):
        pass

@pytest.fixture
def mock_orchestrator():
    mock = MagicMock(spec=PipelineOrchestrator)
    injector.override(PipelineOrchestrator, mock)
    return mock

@pytest.fixture
def disable_webhook_auth(monkeypatch):
    """Disable webhook authentication for testing."""
    from src.api import webhooks
    from unittest.mock import MagicMock
    mock_settings = MagicMock()
    mock_settings.WEBHOOK_SECRET = ""
    mock_settings.is_production = False
    monkeypatch.setattr(webhooks, "settings", mock_settings)

def test_webhook_blackbox_success(mock_orchestrator, env_setup, disable_webhook_auth):
    """
    Test Case: Caixa Preta (Black Box)
    Objectivo: Verificar se o endpoint aceita um payload válido do ADO e responde 202.
    """
    payload = {
        "eventType": "git.pullrequest.created",
        "resource": {
            "pullRequestId": 123,
            "status": "active",
            "title": "Fix bug",
            "repository": {
                "id": "repo-abc",
                "name": "my-repo",
                "project": {"name": "my-project"}
            }
        },
        "resourceVersion": "1.0"
    }
    
    # Execução (Input)
    with TestClient(app) as client:
        response = client.post("/webhook/orchestrate", json=payload)
    
        # Verificao (Output)
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["details"]["pr_id"] == 123
        
        # In async/worker model, the worker processes the queue asynchronously
        import time
        time.sleep(0.2)
        mock_orchestrator.process_pr_pipeline.assert_called_once_with(123, "repo-abc", "my-project")

def test_webhook_blackbox_invalid_payload(env_setup, disable_webhook_auth):
    """
    Test Case: Caixa Preta
    Objectivo: Verificar se o sistema rejeita payloads malformados (Pydantic validation).
    """
    payload = {"invalid": "data"}

    with TestClient(app) as client:
        response = client.post("/webhook/orchestrate", json=payload)

    assert response.status_code == 400  # Custom handler masks 422 to prevent info disclosure
    assert "error" in response.json()

def test_webhook_blackbox_wrong_status(mock_orchestrator, env_setup, disable_webhook_auth):
    """
    Test Case: Caixa Preta
    Objectivo: Verificar se PRs que não estão 'active' são ignorados.
    """
    payload = {
        "eventType": "git.pullrequest.created",
        "resource": {
            "pullRequestId": 999,
            "status": "completed",
            "title": "Done",
            "repository": {
                "id": "r", "name": "n", "project": {"name": "p"}
            }
        },
        "resourceVersion": "1.0"
    }

    with TestClient(app) as client:
        response = client.post("/webhook/orchestrate", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    mock_orchestrator.process_pr_pipeline.assert_not_called()
