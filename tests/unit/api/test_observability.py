import pytest
from fastapi.testclient import TestClient
from src.main import app
from src.core.rate_limit import RateLimiter
from src.core.metrics import metrics
from src.composition import injector
from unittest.mock import MagicMock


@pytest.fixture
def disable_webhook_auth(monkeypatch):
    """Disable webhook authentication for testing."""
    from src.api import webhooks
    mock_settings = MagicMock()
    mock_settings.WEBHOOK_SECRET = ""
    monkeypatch.setattr(webhooks, "settings", mock_settings)


def test_metrics_endpoint(env_setup):
    """Verifica se o endpoint de métricas retorna um resumo válido."""
    with TestClient(app) as client:
        response = client.get("/metrics")
        assert response.status_code == 200
        data = response.json()
        assert "uptime_seconds" in data
        assert "total_requests" in data
        assert "total_tokens" in data


def test_rate_limiter_blocking(env_setup, disable_webhook_auth):
    """Verifica se o rate limiter bloqueia após atingir o limite."""
    # O limiter está no DI. Precisamos de o obter dentro do contexto do app se possível, 
    # ou simplesmente criar um para testar a lógica da classe, 
    # mas aqui queremos testar a INTEGRAÇÃO com o app.
    with TestClient(app) as client:
        limiter = injector.get(RateLimiter)
        limiter.reset()
        # O limite configurado nas settings (default) é 10 por minuto.
    
        # Consumimos o limite
        for _ in range(10):
            assert limiter.is_allowed() is True
        
        # O 11º deve ser negado
        assert limiter.is_allowed() is False
        
        # O endpoint deve retornar 429
        valid_payload = {
            "eventType": "git.pullrequest.created",
            "resourceVersion": "1.0",
            "resource": {
                "pullRequestId": 123,
                "status": "active",
                "title": "T",
                "repository": {
                    "id": "r1", "name": "n1",
                    "project": {"name": "p1"}
                }
            }
        }
        response = client.post("/webhook/orchestrate", json=valid_payload) 
        assert response.status_code == 429
        assert "Rate limit exceeded" in response.json()["detail"]
    
    print("\n[OK] Rate Limiter and Metrics validated.")
