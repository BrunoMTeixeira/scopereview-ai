import pytest
from unittest.mock import MagicMock, AsyncMock
from src.services.code_review import CodeReviewService

@pytest.fixture
def mock_ai():
    client = MagicMock()
    # Retorno padrão para o complete
    client.complete = AsyncMock(return_value=('{"findings": []}', {"total_tokens": 100, "prompt_tokens": 100, "completion_tokens": 0}))
    return client

@pytest.mark.anyio
async def test_code_review_token_budget_exceeded(mock_ai):
    """Testa se o serviço respeita o limite de tokens e para de processar."""
    # Budget pequeno de 150 tokens. Cada bloco gasta 100 (conforme mock_ai).
    service = CodeReviewService(mock_ai, MagicMock(), max_high_block=2, max_token_budget=150)
    
    # 3 ficheiros. Cada um deve gerar pelo menos um bloco.
    mapa_arquivos = {
        "file1.py": "def code1(): pass",
        "file2.py": "def code2(): pass",
        "file3.py": "def code3(): pass"
    }
    
    _, metrics = await service.analyze_pr_code(mapa_arquivos)
    
    # Deve ter excedido o budget (100 + 100 > 150)
    assert metrics["token_budget_exceeded"] is True
    # O mock_ai.complete deve ter sido chamado 3 vezes, mas o budget_exceeded é True
    assert mock_ai.complete.call_count == 3

@pytest.mark.anyio
async def test_code_review_deduplicates_static_and_ai_findings(mock_ai):
    """Testa se erros iguais na mesma linha (static vs ai) são deduplicados."""
    mock_static = MagicMock()
    mock_static.analyze_file.return_value = [{"file": "test.py", "title": "print() used instead of logging", "line": 1, "type": "quality", "severity": "low", "vulnerable_code": ["print('hello')"]}]
    service = CodeReviewService(mock_ai, mock_static, max_high_block=2, max_token_budget=1000)
    
    # Simular IA a encontrar um erro de qualidade na linha 1
    mock_ai.complete = AsyncMock(return_value=(
        '{"findings": [{"title": "AI Error", "line": 1, "type": "quality", "severity": "low"}]}', 
        {"total_tokens": 50, "prompt_tokens": 50, "completion_tokens": 0}
    ))
    
    # Simular Analisador Estático (print()) também na linha 1
    # O StaticAnalyzer.analyze_file é chamado dentro do service
    # Para testar isto, usamos um código que o StaticAnalyzer REAL detete
    mapa = {"test.py": "print('hello')"} # Gera 'print() used instead of logging' na linha 1
    
    result, _ = await service.analyze_pr_code(mapa)
    
    # Mesmo com 2 fontes (Static + AI), a lógica de deduplicação (pelo grupo de linha/tipo) 
    # deve manter apenas um achado se forem do mesmo tipo na mesma linha (ou proximidade)
    # Nota: A nossa lógica de deduplicação agrupa por (file, line//3, type)
    assert len(result["findings"]) == 1
    # O static tem prioridade na ordenação
    assert "print()" in result["findings"][0]["title"]

@pytest.mark.anyio
async def test_code_review_snippet_enrichment(mock_ai):
    """Testa se os snippets de código (vulnerable_code) são enriquecidos com line_cache."""
    service = CodeReviewService(mock_ai, MagicMock(), max_high_block=2, max_token_budget=1000)
    
    # Simulate AI returning a finding without vulnerable_code or with a lazy one
    mock_ai.complete = AsyncMock(return_value=(
        '{"findings": [{"title": "Lazy Error", "line": 2, "type": "bug", "severity": "high", "vulnerable_code": ["2"]}]}', 
        {"total_tokens": 50, "prompt_tokens": 50, "completion_tokens": 0}
    ))
    
    # A diff file map that contains diff markers
    diff_file_map = {
        "lazy.py": "@@ -1,3 +1,3 @@\n 1| def func():\n+2|     return None\n 3| "
    }
    
    result, _ = await service.analyze_pr_code(diff_file_map)
    
    # The snippet should have been replaced with the actual code from line 2
    finding = result["findings"][0]
    assert finding["vulnerable_code"][0].strip() == "return None"

@pytest.mark.anyio
async def test_code_review_malformed_json_fallback(mock_ai):
    """Testa a falha graciosa e/ou fallback em caso de JSON muito mal formado que nem o repair salva."""
    service = CodeReviewService(mock_ai, MagicMock(), max_high_block=2, max_token_budget=1000)
    
    # Simulate a completely broken response
    mock_ai.complete = AsyncMock(return_value=(
        'I am an AI and I refuse to answer in JSON', 
        {"total_tokens": 10, "prompt_tokens": 10, "completion_tokens": 0}
    ))
    
    diff_file_map = {"broken.py": "def test(): pass"}
    result, metrics = await service.analyze_pr_code(diff_file_map)
    
    # Should not crash. Should return empty findings
    assert len(result["findings"]) == 0
    assert result["security_score"] == 10
    assert result["approve"] is True

@pytest.mark.anyio
async def test_code_review_empty_results():
    """Testa quando a IA e o analisador estático não encontram nada e retornam listas vazias."""
    mock_ai_empty = MagicMock()
    mock_ai_empty.complete = AsyncMock(return_value=(None, {}))
    
    service = CodeReviewService(mock_ai_empty, MagicMock(), max_high_block=2, max_token_budget=1000)
    diff_file_map = {"empty.py": "def fine(): pass"}
    result, metrics = await service.analyze_pr_code(diff_file_map)
    
    assert len(result["findings"]) == 0
    assert len(result["findings"]) == 0
    assert result["security_score"] == 10

@pytest.mark.anyio
async def test_code_review_with_full_map_and_work_items(mock_ai):
    """Testa a analise quando e fornecido o codigo completo e os work items (skeletonization e prompts avançados)."""
    service = CodeReviewService(mock_ai, MagicMock(), max_high_block=2, max_token_budget=1000)
    
    mock_ai.complete = AsyncMock(return_value=(
        '{"findings": [{"title": "Business Logic Error", "line": 5, "type": "bug", "severity": "high"}]}', 
        {"total_tokens": 150, "prompt_tokens": 100, "completion_tokens": 50}
    ))
    
    diff_file_map = {"app.py": "@@ -1,5 +1,5 @@\n+def foo(): return 1\n"}
    full_file_map = {"app.py": "def foo():\n    return 1\ndef bar():\n    return 2"}
    work_items = [{"id": 1, "title": "Feature X"}]
    
    result, metrics = await service.analyze_pr_code(diff_file_map, full_file_map=full_file_map, work_items=work_items)
    
    # Must have used the LLM and the skeletonizer
    assert len(result["findings"]) == 1
    assert result["findings"][0]["title"] == "Business Logic Error"
    assert metrics["input_tokens"] > 0
