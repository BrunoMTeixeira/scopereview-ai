"""
End-to-End Tests: FastAPI Endpoints
"""

import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoint:
    """Testes do endpoint /health"""

    def test_health_returns_200(self, env_setup):
        """Health endpoint deve retornar 200 OK"""
        from src.main import app

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200

    def test_health_returns_ok_status(self, env_setup):
        """Health endpoint deve retornar status 'ok'"""
        from src.main import app

        with TestClient(app) as client:
            response = client.get("/health")
            data = response.json()
            assert data["status"] == "ok"

    def test_health_returns_version(self, env_setup):
        """Health endpoint deve retornar versão"""
        from src.main import app

        with TestClient(app) as client:
            response = client.get("/health")
            data = response.json()
            assert "version" in data
            assert data["version"] == "2.0.0"


class TestDocsEndpoint:
    """Testes do endpoint /api/docs"""

    def test_docs_endpoint_accessible(self, env_setup):
        """Endpoint docs deve ser acessível"""
        from src.main import app

        client = TestClient(app)
        response = client.get("/api/docs")

        assert response.status_code == 200

    def test_openapi_schema_available(self, env_setup):
        """Schema OpenAPI deve estar disponível"""
        from src.main import app

        client = TestClient(app)
        response = client.get("/api/openapi.json")

        assert response.status_code == 200
        data = response.json()
        assert "openapi" in data


class TestWebhookEndpoint:
    """Testes do webhook /webhook/orchestrate"""

    def test_webhook_requires_post(self, env_setup):
        """Webhook deve exigir POST"""
        from src.main import app

        client = TestClient(app)
        response = client.get("/webhook/orchestrate")

        assert response.status_code == 405  # Method Not Allowed

    def test_webhook_rejects_invalid_json(self, env_setup):
        """Webhook deve rejeitar JSON inválido"""
        from src.main import app

        client = TestClient(app)
        response = client.post("/webhook/orchestrate", data="invalid json")

        assert response.status_code == 422
