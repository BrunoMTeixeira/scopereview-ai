import pytest
import httpx
from unittest.mock import MagicMock, patch, AsyncMock
from src.infra.azure_devops import AzureDevOpsClient

@pytest.fixture
def ado_client():
    return AzureDevOpsClient(
        organization="test-org",
        pat="test-pat"
    )

@pytest.mark.anyio
async def test_get_pr_details_success(ado_client):
    """Testa a recuperao de detalhes do PR com sucesso."""
    with patch.object(ado_client._client, 'get', new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "title": "Test PR", 
            "description": "Desc", 
            "createdBy": {"displayName": "User"},
            "lastMergeSourceCommit": {"commitId": "sha1"},
            "lastMergeTargetCommit": {"commitId": "base1"}
        }
        mock_get.return_value = mock_resp
        
        details = await ado_client.get_pr_details("repo1", 123, "proj1")
        assert details["title"] == "Test PR"
        assert details["commit_sha"] == "sha1"

@pytest.mark.anyio
async def test_get_pr_details_exception(ado_client):
    """Testa exceo ao recuperar detalhes do PR."""
    with patch.object(ado_client._client, 'get', new_callable=AsyncMock, side_effect=httpx.RequestError("Network Error")):
        details = await ado_client.get_pr_details("repo1", 123, "proj1")
        assert details is None

@pytest.mark.anyio
async def test_get_work_items_success(ado_client):
    """Testa a recuperao de work items e os seus detalhes."""
    with patch.object(ado_client._client, 'get', new_callable=AsyncMock) as mock_get:
        # 1. Lista de Work Items
        mock_resp_links = MagicMock(spec=httpx.Response)
        mock_resp_links.status_code = 200
        mock_resp_links.json.return_value = {"value": [{"url": "https://dev.azure.com/org/_apis/wit/workItems/1"}]}
        
        # 2. Detalhes do Work Item
        mock_resp_wi = MagicMock(spec=httpx.Response)
        mock_resp_wi.status_code = 200
        mock_resp_wi.json.return_value = {"value": [{
            "id": 1,
            "fields": {
                "System.Title": "Story 1",
                "System.WorkItemType": "User Story",
                "System.Description": "Desc",
                "Microsoft.VSTS.Common.AcceptanceCriteria": "AC"
            },
            "_links": {"html": {"href": "http://wi/1"}}
        }]}
        
        mock_get.side_effect = [mock_resp_links, mock_resp_wi]
        
        items = await ado_client.get_work_items("repo1", 123, "proj1")
        assert len(items) == 1
        assert items[0]["title"] == "Story 1"

@pytest.mark.anyio
async def test_get_work_items_exception(ado_client):
    """Testa exceo ao recuperar work items."""
    with patch.object(ado_client._client, 'get', new_callable=AsyncMock, side_effect=httpx.RequestError("Error")):
        items = await ado_client.get_work_items("repo1", 123, "proj1")
        assert items == []

@pytest.mark.anyio
async def test_post_comment_exception(ado_client):
    """Testa exceo ao postar comentário."""
    with patch.object(ado_client._client, 'post', new_callable=AsyncMock, side_effect=httpx.RequestError("Error")):
        # No deve levantar exceo para fora
        await ado_client.post_comment("repo1", 123, "proj1", "Cool PR!")

@pytest.mark.anyio
async def test_get_changed_files_with_diff_logic(ado_client):
    """Testa a lógica de diffing real (base_sha vs commit_sha)."""
    with patch.object(ado_client._client, 'get', new_callable=AsyncMock) as mock_get:
        # 1. Mock Iterations
        mock_iter = MagicMock(spec=httpx.Response)
        mock_iter.json.return_value = {"value": [{"id": 1}]}
        
        # 2. Mock Changes
        mock_changes = MagicMock(spec=httpx.Response)
        mock_changes.json.return_value = {
            "changeEntries": [
                {"item": {"path": "/test.py"}, "changeType": "edit"},
                {"item": {"path": "/ignored.png"}, "changeType": "add"},
                {"item": {"path": "/skipped.py"}, "changeType": "rename"}
            ]
        }
        
        # We also need to patch stream because get_file_content uses stream
        with patch.object(ado_client._client, 'stream') as mock_stream:
            # 3. Mock File Content (Base)
            mock_base = MagicMock()
            mock_base.status_code = 200
            mock_base.headers = {}
            mock_base.aread = AsyncMock(return_value=b"line 1\nline 2")
            mock_base.text = "line 1\nline 2"
            
            # 4. Mock File Content (Current)
            mock_curr = MagicMock()
            mock_curr.status_code = 200
            mock_curr.headers = {}
            mock_curr.aread = AsyncMock(return_value=b"line 1\nline 2 MOD")
            mock_curr.text = "line 1\nline 2 MOD"

            class AsyncContextManagerMock:
                def __init__(self, obj):
                    self.obj = obj
                async def __aenter__(self):
                    return self.obj
                async def __aexit__(self, exc_type, exc_val, exc_tb):
                    pass

            # Since stream is used multiple times (raw_source, raw_base), we need side_effect
            # The order in get_changed_files for a file with base_sha is:
            # raw_source = get_file_content(commit_sha)
            # raw_base = get_file_content(base_sha)
            mock_stream.side_effect = [
                AsyncContextManagerMock(mock_curr),
                AsyncContextManagerMock(mock_base)
            ]
            
            # Sequência: Iterations -> Changes
            mock_get.side_effect = [mock_iter, mock_changes]
            
            map_full, map_diffs, total_eligible = await ado_client.get_changed_files("repo1", 123, "proj1", "sha1", base_sha="base1")
        
        assert "/test.py" in map_full
        assert "/test.py" in map_diffs
        assert "/ignored.png" not in map_full
        assert "-line 2" in map_diffs["/test.py"]
        assert "+line 2 MOD" in map_diffs["/test.py"]

@pytest.mark.anyio
async def test_get_repo_rules_failure(ado_client):
    """Testa falha ao ler ficheiros de regras."""
    with patch.object(ado_client._client, 'get', new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp
        
        rules = await ado_client.get_repo_rules("repo1", "proj1", "sha1")
        assert rules == ""

@pytest.mark.anyio
async def test_get_commit_head_exception(ado_client):
    """Testa exceo ao recuperar o SHA do commit HEAD."""
    with patch.object(ado_client._client, 'get', new_callable=AsyncMock, side_effect=httpx.RequestError("Error")):
        sha = await ado_client.get_commit_head("repo1", 123, "proj1")
        assert sha == ""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from src.infra.azure_devops import AzureDevOpsClient
import httpx

@pytest.mark.anyio
async def test_get_commit_head_error(ado_client):
    with patch.object(ado_client._client, 'get', new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 404
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError("Not found", request=MagicMock(), response=mock_resp)
        mock_get.return_value = mock_resp
        
        with pytest.raises(httpx.HTTPStatusError):
            await ado_client.get_commit_head("repo1", 1, "proj1")

@pytest.mark.anyio
async def test_get_changed_files_file_too_large(ado_client):
    with patch.object(ado_client._client, 'get', new_callable=AsyncMock) as mock_get, \
         patch.object(ado_client, 'get_file_content', new_callable=AsyncMock) as mock_download:
        
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "changes": [
                {"item": {"path": "/big.py"}, "changeType": "edit"}
            ]
        }
        mock_get.return_value = mock_resp
        
        # Mock HEAD request for file size
        mock_head_resp = MagicMock(spec=httpx.Response)
        mock_head_resp.headers = {"Content-Length": "10000000"}  # 10MB
        with patch.object(ado_client._client, 'head', new_callable=AsyncMock) as mock_head:
            mock_head.return_value = mock_head_resp
            full, diff, total = await ado_client.get_changed_files("repo1", 1, "proj", "sha", "base")
            
            assert "big.py" not in full
            assert not mock_download.called

@pytest.mark.anyio
async def test_get_file_content_fails(ado_client):
    with patch.object(ado_client._client, 'get', new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError("Error", request=MagicMock(), response=mock_resp)
        mock_get.return_value = mock_resp
        
        content = await ado_client.get_file_content("repo1", "proj", "/file.py", "sha")
        assert content == ""
