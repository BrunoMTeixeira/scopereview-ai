import pytest
import requests
from unittest.mock import MagicMock, patch
from src.infra.azure_devops import AzureDevOpsClient

@pytest.fixture
def ado_client():
    return AzureDevOpsClient(
        organization="test-org",
        pat="test-pat"
    )

def test_get_pr_details_success(ado_client):
    """Testa a recuperao de detalhes do PR com sucesso."""
    with patch.object(ado_client._session, 'get') as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "title": "Test PR", 
            "description": "Desc", 
            "createdBy": {"displayName": "User"},
            "lastMergeSourceCommit": {"commitId": "sha1"},
            "lastMergeTargetCommit": {"commitId": "base1"}
        }
        mock_get.return_value = mock_resp
        
        details = ado_client.get_pr_details("repo1", 123, "proj1")
        assert details["title"] == "Test PR"
        assert details["commit_sha"] == "sha1"

def test_get_pr_details_exception(ado_client):
    """Testa exceo ao recuperar detalhes do PR."""
    # O código captura especificamente requests.RequestException
    with patch.object(ado_client._session, 'get', side_effect=requests.RequestException("Network Error")):
        details = ado_client.get_pr_details("repo1", 123, "proj1")
        assert details is None

def test_get_work_items_success(ado_client):
    """Testa a recuperao de work items e os seus detalhes."""
    with patch.object(ado_client._session, 'get') as mock_get:
        # 1. Lista de Work Items
        mock_resp_links = MagicMock()
        mock_resp_links.status_code = 200
        mock_resp_links.json.return_value = {"value": [{"url": "https://wi/1"}]}
        
        # 2. Detalhes do Work Item
        mock_resp_wi = MagicMock()
        mock_resp_wi.status_code = 200
        mock_resp_wi.json.return_value = {
            "id": 1,
            "fields": {
                "System.Title": "Story 1",
                "System.WorkItemType": "User Story",
                "System.Description": "Desc",
                "Microsoft.VSTS.Common.AcceptanceCriteria": "AC"
            },
            "_links": {"html": {"href": "http://wi/1"}}
        }
        
        mock_get.side_effect = [mock_resp_links, mock_resp_wi]
        
        items = ado_client.get_work_items("repo1", 123, "proj1")
        assert len(items) == 1
        assert items[0]["title"] == "Story 1"

def test_get_work_items_exception(ado_client):
    """Testa exceo ao recuperar work items."""
    with patch.object(ado_client._session, 'get', side_effect=requests.RequestException("Error")):
        items = ado_client.get_work_items("repo1", 123, "proj1")
        assert items == []

def test_post_comment_exception(ado_client):
    """Testa exceo ao postar comentário."""
    with patch.object(ado_client._session, 'post', side_effect=requests.RequestException("Error")):
        # No deve levantar exceo para fora
        ado_client.post_comment("repo1", 123, "proj1", "Cool PR!")

def test_get_changed_files_with_diff_logic(ado_client):
    """Testa a lógica de diffing real (base_sha vs commit_sha)."""
    with patch.object(ado_client._session, 'get') as mock_get:
        # 1. Mock Iterations
        mock_iter = MagicMock()
        mock_iter.json.return_value = {"value": [{"id": 1}]}
        
        # 2. Mock Changes
        mock_changes = MagicMock()
        mock_changes.json.return_value = {
            "changeEntries": [
                {"item": {"path": "/test.py"}, "changeType": "edit"},
                {"item": {"path": "/ignored.png"}, "changeType": "add"},
                {"item": {"path": "/skipped.py"}, "changeType": "rename"}
            ]
        }
        
        # 3. Mock File Content (Base)
        mock_base = MagicMock()
        mock_base.status_code = 200
        mock_base.text = "line 1\nline 2"
        
        # 4. Mock File Content (Current)
        mock_curr = MagicMock()
        mock_curr.status_code = 200
        mock_curr.text = "line 1\nline 2 MOD"
        
        # Sequência: Iterations -> Changes -> Diff(Base) -> Diff(Current) -> FullContent(Current)
        mock_get.side_effect = [mock_iter, mock_changes, mock_curr, mock_base, mock_curr]
        
        map_full, map_diffs = ado_client.get_changed_files("repo1", 123, "proj1", "sha1", base_sha="base1")
        
        assert "/test.py" in map_full
        assert "/test.py" in map_diffs
        assert "/ignored.png" not in map_full
        assert "-line 2" in map_diffs["/test.py"]
        assert "+line 2 MOD" in map_diffs["/test.py"]

def test_get_repo_rules_failure(ado_client):
    """Testa falha ao ler ficheiros de regras."""
    with patch.object(ado_client._session, 'get') as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp
        
        rules = ado_client.get_repo_rules("repo1", "proj1", "sha1")
        assert rules == ""

def test_get_commit_head_exception(ado_client):
    """Testa exceo ao recuperar o SHA do commit HEAD."""
    with patch.object(ado_client._session, 'get', side_effect=requests.RequestException("Error")):
        sha = ado_client.get_commit_head("repo1", 123, "proj1")
        assert sha == ""
