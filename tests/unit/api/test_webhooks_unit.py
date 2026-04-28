import base64
import pytest
from unittest.mock import MagicMock, patch
from fastapi import Request, HTTPException, BackgroundTasks
from src.api.webhooks import check_webhook_secret, webhook_orchestrate
from src.models.webhooks import ADOWebhookPayload
from src.core.config import settings

@pytest.mark.anyio
async def test_check_webhook_secret_success():
    """Testa autenticação bem-sucedida com segredo."""
    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.WEBHOOK_SECRET = "correct-password"
        request = MagicMock(spec=Request)
        # Base64 de "user:correct-password"
        auth = base64.b64encode(b"user:correct-password").decode()
        request.headers.get.return_value = f"Basic {auth}"
        
        assert check_webhook_secret(request) is True

@pytest.mark.anyio
async def test_check_webhook_secret_no_secret_required():
    """Testa quando não há segredo configurado (deve retornar True)."""
    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.WEBHOOK_SECRET = ""
        request = MagicMock(spec=Request)
        assert check_webhook_secret(request) is True

@pytest.mark.anyio
async def test_check_webhook_secret_missing_header():
    """Testa quando falta o cabeçalho de autorização."""
    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.WEBHOOK_SECRET = "secret"
        request = MagicMock(spec=Request)
        request.headers.get.return_value = None
        
        with pytest.raises(HTTPException) as exc:
            check_webhook_secret(request)
        assert exc.value.status_code == 401
        assert "Missing Authorization header" in exc.value.detail

@pytest.mark.anyio
async def test_check_webhook_secret_invalid_format():
    """Testa quando o cabeçalho não começa com 'Basic '."""
    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.WEBHOOK_SECRET = "secret"
        request = MagicMock(spec=Request)
        request.headers.get.return_value = "Bearer token"
        
        with pytest.raises(HTTPException) as exc:
            check_webhook_secret(request)
        assert exc.value.status_code == 401
        assert "Invalid Authorization header" in exc.value.detail

@pytest.mark.anyio
async def test_check_webhook_secret_wrong_credentials():
    """Testa credenciais erradas."""
    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.WEBHOOK_SECRET = "correct-secret"
        request = MagicMock(spec=Request)
        # Base64 de "user:wrong-secret"
        auth = base64.b64encode(b"user:wrong-secret").decode()
        request.headers.get.return_value = f"Basic {auth}"
        
        with pytest.raises(HTTPException) as exc:
            check_webhook_secret(request)
        assert exc.value.status_code == 401
        assert "Invalid credentials" in exc.value.detail

@pytest.mark.anyio
async def test_check_webhook_secret_parse_error():
    """Testa erro de parse (Base64 inválido)."""
    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.WEBHOOK_SECRET = "secret"
        request = MagicMock(spec=Request)
        request.headers.get.return_value = "Basic !@#$%^"
        
        with pytest.raises(HTTPException) as exc:
            check_webhook_secret(request)
        assert exc.value.status_code == 401

@pytest.mark.anyio
async def test_check_webhook_secret_decode_error():
    """Testa erro de descodificação (UTF-8 inválido)."""
    with patch("src.api.webhooks.settings") as mock_settings:
        mock_settings.WEBHOOK_SECRET = "secret"
        request = MagicMock(spec=Request)
        # Bytes que não são UTF-8 válidos
        invalid_utf8 = base64.b64encode(b"\xff\xfe\xfd").decode()
        request.headers.get.return_value = f"Basic {invalid_utf8}"
        
        with pytest.raises(HTTPException) as exc:
            check_webhook_secret(request)
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
    with patch("src.api.webhooks.injector.get") as mock_get:
        mock_limiter = MagicMock()
        mock_limiter.is_allowed.return_value = True
        mock_get.return_value = mock_limiter
        payload = ADOWebhookPayload(
            eventType="git.pullrequest.created",
            resourceVersion="1.0",
            resource={
                "pullRequestId": 123,
                "status": "active",
                "title": "T",
                "repository": {
                    "id": "repo-1", "name": "n1",
                    "project": {"name": "ProjectA"}
                }
            }
        )
        
        background_tasks = MagicMock(spec=BackgroundTasks)
        
        with patch("src.api.webhooks.get_pipeline_orchestrator") as mock_get_orch:
            mock_orch = MagicMock()
            mock_get_orch.return_value = mock_orch
            
            response = await webhook_orchestrate(payload, background_tasks)
            
            assert response.status_code == 202
            assert background_tasks.add_task.called
