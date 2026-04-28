import pytest
from unittest.mock import MagicMock, patch
from src.infra.azure_devops import AzureDevOpsClient
from requests import Response, RequestException

@pytest.fixture
def ado_client():
    return AzureDevOpsClient(
        organization="test-org",
        pat="test-pat"
    )

def test_get_pr_details_success(ado_client):
    """Testa recuperao de PR com sucesso (retorna dicionário)."""
    with patch.object(ado_client._session, 'get') as mock_get:
        mock_response = MagicMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "title": "Test PR",
            "description": "Desc",
            "createdBy": {"displayName": "User"},
            "lastMergeSourceCommit": {"commitId": "123"},
            "lastMergeTargetCommit": {"commitId": "456"}
        }
        mock_get.return_value = mock_response
        
        pr = ado_client.get_pr_details("repo", 123, "proj")
        
        assert pr["title"] == "Test PR"
        assert pr["author"] == "User"

def test_get_pr_details_error_handling(ado_client):
    """Testa comportamento quando ocorre erro de rede (deve retornar None)."""
    with patch.object(ado_client._session, 'get') as mock_get:
        # Simular uma exceção de rede que é capturada pelo client
        mock_get.side_effect = RequestException("Timeout")
        
        pr = ado_client.get_pr_details("repo", 999, "proj")
        assert pr is None

def test_post_comment_success(ado_client):
    """Testa publicao de comentrio."""
    with patch.object(ado_client._session, 'post') as mock_post:
        mock_response = MagicMock(spec=Response)
        mock_response.status_code = 201
        mock_post.return_value = mock_response
        
        ado_client.post_comment("repo", 123, "proj", "test comment")
        assert mock_post.called

def test_get_work_items_empty(ado_client):
    """Testa recuperao de WIs quando no existem (método correto: get_work_items)."""
    with patch.object(ado_client._session, 'get') as mock_get:
        mock_response = MagicMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"value": []}
        mock_get.return_value = mock_response
        
        wis = ado_client.get_work_items("repo", 123, "proj")
        assert len(wis) == 0
