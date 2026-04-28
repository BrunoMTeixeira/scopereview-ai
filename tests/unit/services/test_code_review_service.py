import pytest
from unittest.mock import MagicMock
from src.services.code_review import CodeReviewService

@pytest.fixture
def mock_ai():
    client = MagicMock()
    # Retorno padrão para o complete
    client.complete.return_value = ('{"findings": []}', 100)
    return client

def test_code_review_token_budget_exceeded(mock_ai):
    """Testa se o serviço respeita o limite de tokens e para de processar."""
    # Budget pequeno de 150 tokens. Cada bloco gasta 100 (conforme mock_ai).
    service = CodeReviewService(mock_ai, max_high_block=2, max_token_budget=150)
    
    # 3 ficheiros. Cada um deve gerar pelo menos um bloco.
    mapa_arquivos = {
        "file1.py": "def code1(): pass",
        "file2.py": "def code2(): pass",
        "file3.py": "def code3(): pass"
    }
    
    _, metrics = service.analyze_pr_code(mapa_arquivos)
    
    # Deve ter excedido o budget (100 + 100 > 150)
    assert metrics["token_budget_exceeded"] is True
    # O mock_ai.complete deve ter sido chamado apenas 2 vezes (ou 1 dependendo da ordem)
    assert mock_ai.complete.call_count < 3

def test_code_review_deduplicates_static_and_ai_findings(mock_ai):
    """Testa se erros iguais na mesma linha (static vs ai) são deduplicados."""
    service = CodeReviewService(mock_ai, max_high_block=2, max_token_budget=1000)
    
    # Simular IA a encontrar um erro de qualidade na linha 1
    mock_ai.complete.return_value = (
        '{"findings": [{"title": "AI Error", "line": 1, "type": "quality", "severity": "low"}]}', 
        50
    )
    
    # Simular Analisador Estático (print()) também na linha 1
    # O StaticAnalyzer.analyze_file é chamado dentro do service
    # Para testar isto, usamos um código que o StaticAnalyzer REAL detete
    mapa = {"test.py": "print('hello')"} # Gera 'print() used instead of logging' na linha 1
    
    result, _ = service.analyze_pr_code(mapa)
    
    # Mesmo com 2 fontes (Static + AI), a lógica de deduplicação (pelo grupo de linha/tipo) 
    # deve manter apenas um achado se forem do mesmo tipo na mesma linha (ou proximidade)
    # Nota: A nossa lógica de deduplicação agrupa por (file, line//3, type)
    assert len(result["findings"]) == 1
    # O static tem prioridade na ordenação
    assert "print()" in result["findings"][0]["title"]
