import pytest
from unittest.mock import MagicMock, patch
from requests.exceptions import HTTPError
from src.infra.azure_ai import AzureOpenAIClient

@pytest.fixture
def ai_client():
    return AzureOpenAIClient(
        endpoint="https://test.openai.azure.com/",
        api_key="test-key",
        model_name="gpt-4o",
        max_retries=2
    )

def test_complete_success(ai_client):
    """Testa chamada completa com sucesso."""
    with patch('requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {"content": '{"result": "ok"}'},
                "finish_reason": "stop"
            }],
            "usage": {"total_tokens": 150}
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response
        
        content, tokens = ai_client.complete("sys", "user")
        
        assert content == '{"result": "ok"}'
        assert tokens == 150

def test_complete_with_retries_on_429(ai_client):
    """Testa se o cliente faz retry em caso de Rate Limit (429)."""
    with patch('requests.post') as mock_post:
        # Create a proper HTTPError for the 429 response
        mock_429_response = MagicMock()
        mock_429_response.status_code = 429
        http_error = HTTPError(response=mock_429_response)
        
        mock_200_response = MagicMock()
        mock_200_response.status_code = 200
        mock_200_response.raise_for_status = MagicMock()
        mock_200_response.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 10}
        }
        
        # First call raises 429, second call succeeds
        call_count = 0
        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                resp = MagicMock()
                resp.status_code = 429
                resp.raise_for_status.side_effect = HTTPError(response=resp)
                return resp
            return mock_200_response
        
        mock_post.side_effect = side_effect
        
        content, tokens = ai_client.complete("sys", "user")
        assert content == '{"ok": true}'
        assert mock_post.call_count == 2

def test_complete_invalid_json_handling(ai_client):
    """Testa como o cliente lida com respostas que não contêm JSON."""
    with patch('requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "I am not a JSON"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 0}
        }
        mock_post.return_value = mock_response
        
        content, tokens = ai_client.complete("sys", "user")
        assert content is None
