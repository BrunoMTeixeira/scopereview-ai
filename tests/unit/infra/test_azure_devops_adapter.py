import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from src.infra.azure_devops import AzureDevOpsClient
import httpx

@pytest.fixture
def ado_client():
    return AzureDevOpsClient(
        organization="test-org",
        pat="test-pat"
    )

@pytest.mark.anyio
async def test_get_pr_details_success(ado_client):
    """Testa recuperao de PR com sucesso (retorna dicionário)."""
    with patch.object(ado_client._client, 'get', new_callable=AsyncMock) as mock_get:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "title": "Test PR",
            "description": "Desc",
            "createdBy": {"displayName": "User"},
            "lastMergeSourceCommit": {"commitId": "123"},
            "lastMergeTargetCommit": {"commitId": "456"}
        }
        mock_get.return_value = mock_response
        
        pr = await ado_client.get_pr_details("repo", 123, "proj")
        
        assert pr["title"] == "Test PR"
        assert pr["author"] == "User"

@pytest.mark.anyio
async def test_get_pr_details_error_handling(ado_client):
    """Testa comportamento quando ocorre erro de rede (deve retornar None)."""
    with patch.object(ado_client._client, 'get', new_callable=AsyncMock) as mock_get:
        # Simular uma exceção de rede que é capturada pelo client
        mock_get.side_effect = httpx.RequestError("Timeout")
        
        pr = await ado_client.get_pr_details("repo", 999, "proj")
        assert pr is None

@pytest.mark.anyio
async def test_post_comment_success(ado_client):
    """Testa publicao de comentrio."""
    with patch.object(ado_client._client, 'post', new_callable=AsyncMock) as mock_post:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 201
        mock_post.return_value = mock_response
        
        await ado_client.post_comment("repo", 123, "proj", "test comment")
        assert mock_post.called

        mock_post.reset_mock()
        await ado_client.post_pr_status("repo", 123, "proj", "succeeded", "OK")
        assert mock_post.called

@pytest.mark.anyio
async def test_get_work_items_empty(ado_client):
    """Testa recuperao de WIs quando no existem (método correto: get_work_items)."""
    with patch.object(ado_client._client, 'get', new_callable=AsyncMock) as mock_get:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"value": []}
        mock_get.return_value = mock_response
        
        wis = await ado_client.get_work_items("repo", 123, "proj")
        assert len(wis) == 0
