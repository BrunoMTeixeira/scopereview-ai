import pytest
from unittest.mock import MagicMock, patch
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
        mock_post.return_value = mock_response
        
        content, tokens = ai_client.complete("sys", "user")
        
        assert content == '{"result": "ok"}'
        assert tokens == 150

def test_complete_with_retries_on_429(ai_client):
    """Testa se o cliente faz retry em caso de Rate Limit (429)."""
    with patch('requests.post') as mock_post:
        # Primeiro falha com 429, depois tem sucesso
        mock_429 = MagicMock()
        mock_429.status_code = 429
        
        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.json.return_value = {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"total_tokens": 10}
        }
        
        mock_post.side_effect = [mock_429, mock_200]
        
        with patch('time.sleep'): # Não queremos esperar no teste
            content, tokens = ai_client.complete("sys", "user")
            assert content == "{}"
            assert mock_post.call_count == 2

def test_complete_invalid_json_handling(ai_client):
    """Testa como o cliente lida com respostas que não contêm JSON."""
    with patch('requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "I am not a JSON"}}]
        }
        mock_post.return_value = mock_response
        
        content, tokens = ai_client.complete("sys", "user")
        assert content is None
