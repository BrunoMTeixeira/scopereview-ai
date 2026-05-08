import base64
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi import Request, HTTPException, BackgroundTasks
from src.api.webhooks import check_webhook_secret, webhook_orchestrate
from src.models.webhooks import ADOWebhookPayload
from src.core.config import settings


def _mock_request(headers_map: dict) -> MagicMock:
    """Creates a mock Request with a headers.get that returns values from headers_map."""
    request = MagicMock(spec=Request)
    request.headers = MagicMock()
    request.headers.get = lambda key, default=None: headers_map.get(key, default)
    request.url = MagicMock()
    request.url.scheme = "https"
    return request


@pytest.mark.anyio
async def test_check_webhook_secret_success():
    """Testa autenticação bem-sucedida com segredo."""
    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.WEBHOOK_SECRET = "correct-password"
        mock_settings.is_production = False
        auth = base64.b64encode(b"user:correct-password").decode()
        request = _mock_request({
            "Authorization": f"Basic {auth}",
        })
        
        assert await check_webhook_secret(request) is True

@pytest.mark.anyio
async def test_check_webhook_secret_no_secret_required():
    """Testa quando não há segredo configurado (deve retornar True)."""
    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.WEBHOOK_SECRET = ""
        mock_settings.is_production = False
        request = _mock_request({})
        assert await check_webhook_secret(request) is True

@pytest.mark.anyio
async def test_check_webhook_secret_missing_header():
    """Testa quando falta o cabeçalho de autorização."""
    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.WEBHOOK_SECRET = "secret"
        mock_settings.is_production = False
        request = _mock_request({})
        
        with pytest.raises(HTTPException) as exc:
            await check_webhook_secret(request)
        assert exc.value.status_code == 401
        assert "Missing Authorization header" in exc.value.detail

@pytest.mark.anyio
async def test_check_webhook_secret_invalid_format():
    """Testa quando o cabeçalho não começa com 'Basic '."""
    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.WEBHOOK_SECRET = "secret"
        mock_settings.is_production = False
        request = _mock_request({
            "Authorization": "Bearer token",
        })
        
        with pytest.raises(HTTPException) as exc:
            await check_webhook_secret(request)
        assert exc.value.status_code == 401
        assert "Invalid Authorization header" in exc.value.detail

@pytest.mark.anyio
async def test_check_webhook_secret_wrong_credentials():
    """Testa credenciais erradas."""
    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.WEBHOOK_SECRET = "correct-secret"
        mock_settings.is_production = False
        auth = base64.b64encode(b"user:wrong-secret").decode()
        request = _mock_request({
            "Authorization": f"Basic {auth}",
        })
        
        with pytest.raises(HTTPException) as exc:
            await check_webhook_secret(request)
        assert exc.value.status_code == 401
        assert "Invalid credentials" in exc.value.detail

@pytest.mark.anyio
async def test_check_webhook_secret_parse_error():
    """Testa erro de parse (Base64 inválido)."""
    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.WEBHOOK_SECRET = "secret"
        mock_settings.is_production = False
        request = _mock_request({
            "Authorization": "Basic !@#$%^",
        })
        
        with pytest.raises(HTTPException) as exc:
            await check_webhook_secret(request)
        assert exc.value.status_code == 401

@pytest.mark.anyio
async def test_check_webhook_secret_decode_error():
    """Testa erro de descodificação (UTF-8 inválido)."""
    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.WEBHOOK_SECRET = "secret"
        mock_settings.is_production = False
        invalid_utf8 = base64.b64encode(b"\xff\xfe\xfd").decode()
        request = _mock_request({
            "Authorization": f"Basic {invalid_utf8}",
        })
        
        with pytest.raises(HTTPException) as exc:
            await check_webhook_secret(request)
        assert exc.value.status_code == 401

@pytest.mark.anyio
async def test_webhook_orchestrate_ignored_event():
    """Testa eventos que não suportamos (ex: git.push)."""
    with patch("src.api.webhooks.injector.get") as mock_get:
        mock_limiter = MagicMock()
        mock_limiter.is_allowed.return_value = True
        mock_get.return_value = mock_limiter
        payload = ADOWebhookPayload(
            eventType="git.push",
            resourceVersion="1.0",
            resource={
                "pullRequestId": 123,
                "status": "active",
                "title": "T",
                "repository": {
                    "id": "r1", "name": "n1",
                    "project": {"name": "p1"}
                }
            }
        )
        background_tasks = MagicMock(spec=BackgroundTasks)
        
        response = await webhook_orchestrate(payload, background_tasks)
        assert response.status_code == 200
        assert b"not supported" in response.body

@pytest.mark.anyio
async def test_webhook_orchestrate_inactive_pr():
    """Testa PRs que não estão ativos."""
    with patch("src.api.webhooks.injector.get") as mock_get:
        mock_limiter = MagicMock()
        mock_limiter.is_allowed.return_value = True
        mock_get.return_value = mock_limiter
        payload = ADOWebhookPayload(
            eventType="git.pullrequest.created",
            resourceVersion="1.0",
            resource={
                "pullRequestId": 123,
                "status": "completed",
                "title": "T",
                "repository": {
                    "id": "r1", "name": "n1",
                    "project": {"name": "p1"}
                }
            }
        )
        background_tasks = MagicMock(spec=BackgroundTasks)
        
        response = await webhook_orchestrate(payload, background_tasks)
        assert response.status_code == 200
        assert b"only 'active' PRs" in response.body

@pytest.mark.anyio
async def test_webhook_orchestrate_success():
    """Testa o caminho de sucesso (deve agendar a tarefa)."""
    from src.main import app
    from src.composition import get_pipeline_orchestrator
    from fastapi.testclient import TestClient

    mock_orch = MagicMock()
    app.dependency_overrides[get_pipeline_orchestrator] = lambda: mock_orch

    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.WEBHOOK_SECRET = ""
        mock_settings.is_production = False

        with patch("src.api.webhooks.injector.get") as mock_get:
            mock_limiter = MagicMock()
            mock_limiter.is_allowed.return_value = True
            mock_get.return_value = mock_limiter

            with TestClient(app) as client:
                payload = {
                    "eventType": "git.pullrequest.created",
                    "resourceVersion": "1.0",
                    "resource": {
                        "pullRequestId": 123,
                        "status": "active",
                        "title": "T",
                        "repository": {
                            "id": "repo-1", "name": "n1",
                            "project": {"name": "ProjectA"}
                        }
                    }
                }
                response = client.post("/webhook/orchestrate", json=payload)
                assert response.status_code == 202

    app.dependency_overrides.clear()

@pytest.mark.anyio
async def test_check_webhook_secret_payload_too_large():
    """Testa rejeição de payload excessivamente grande."""
    request = _mock_request({"Content-Length": str((2 * 1024 * 1024) + 1)})
    with pytest.raises(HTTPException) as exc:
        await check_webhook_secret(request)
    assert exc.value.status_code == 413
    assert "Payload too large" in exc.value.detail

@pytest.mark.anyio
async def test_check_webhook_secret_require_https_in_prod():
    """Testa se em produção o HTTPS é forçado."""
    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.is_production = True
        request = _mock_request({})
        request.url.scheme = "http"  # Not https
        with pytest.raises(HTTPException) as exc:
            await check_webhook_secret(request)
        assert exc.value.status_code == 403
        assert "HTTPS required" in exc.value.detail

@pytest.mark.anyio
async def test_check_webhook_secret_hmac_invalid():
    """Testa falha de validação HMAC."""
    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.WEBHOOK_SECRET = "secret"
        mock_settings.is_production = False
        request = _mock_request({
            "X-Hub-Signature-256": "sha256=invalidhash123",
        })
        request.body = AsyncMock(return_value=b"body content")
        
        with pytest.raises(HTTPException) as exc:
            await check_webhook_secret(request)
        assert exc.value.status_code == 401
        assert "Invalid HMAC signature" in exc.value.detail

@pytest.mark.anyio
async def test_check_webhook_secret_hmac_valid():
    """Testa sucesso de validação HMAC."""
    import hashlib
    import hmac
    
    secret = "my-secret-key"
    body = b"valid body content"
    expected_mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    
    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.WEBHOOK_SECRET = secret
        mock_settings.is_production = False
        request = _mock_request({
            "X-Hub-Signature-256": f"sha256={expected_mac}",
        })
        request.body = AsyncMock(return_value=body)
        
        assert await check_webhook_secret(request) is True
