import pytest
from unittest.mock import MagicMock, patch
from src.services.orchestrator import PipelineOrchestrator

@pytest.fixture
def orchestrator():
    # Mocking dependencies
    ado = MagicMock()
    cr = MagicMock()
    rr = MagicMock()
    dedup = MagicMock()
    return PipelineOrchestrator(
        ado, cr, rr, dedup,
        code_model_display_name="CodeModel",
        requirements_model_display_name="ReqModel"
    )

def test_process_pr_pipeline_success(orchestrator):
    """Testa o fluxo feliz completo do orchestrator."""
    orchestrator._ado.get_pr_details.return_value = {
        "title": "Fix bug", 
        "description": "Desc", 
        "commit_sha": "sha1", 
        "base_sha": "base1"
    }
    orchestrator._ado.get_changed_files.return_value = ({"f1.py": "code"}, {"f1.py": "diff"})
    orchestrator._ado.get_work_items.return_value = [{"id": 1, "title": "Task"}]
    orchestrator._ado.get_repo_rules.return_value = "rules"
    
    orchestrator._code_review.analyze_pr_code.return_value = ({"findings": []}, 1000)
    orchestrator._requirements_review.validate_requirements.return_value = ({"overall_verdict": "APPROVED"}, 500)
    
    orchestrator._dedup.should_skip_duplicate.return_value = False
    
    orchestrator.process_pr_pipeline(123, "repo1", "proj1")
    
    # O post_comment  chamado apenas se houver resultados
    assert orchestrator._ado.post_comment.called

def test_process_pr_pipeline_no_details(orchestrator):
    """Testa quando no se consegue obter detalhes do PR."""
    orchestrator._dedup.should_skip_duplicate.return_value = False
    orchestrator._ado.get_pr_details.return_value = None
    
    orchestrator.process_pr_pipeline(123, "repo1", "proj1")
    
    assert not orchestrator._ado.get_changed_files.called

def test_process_pr_pipeline_no_supported_files(orchestrator):
    """Testa quando o PR s tem ficheiros ignorados."""
    orchestrator._dedup.should_skip_duplicate.return_value = False
    orchestrator._ado.get_pr_details.return_value = {"title": "T", "commit_sha": "s"}
    # Devolve mapas vazios
    orchestrator._ado.get_changed_files.return_value = ({}, {})
    
    orchestrator.process_pr_pipeline(123, "repo1", "proj1")
    
    assert not orchestrator._code_review.analyze_pr_code.called

def test_process_pr_pipeline_code_review_fails_but_continues(orchestrator):
    """Testa se o orchestrator continua para requisitos se o CR falhar."""
    orchestrator._dedup.should_skip_duplicate.return_value = False
    orchestrator._ado.get_pr_details.return_value = {"title": "T", "commit_sha": "s", "base_sha": "b"}
    orchestrator._ado.get_changed_files.return_value = ({"f1.py": "c"}, {"f1.py": "d"})
    
    # Simular falha no Code Review
    orchestrator._code_review.analyze_pr_code.return_value = (None, 0)
    
    orchestrator.process_pr_pipeline(123, "repo1", "proj1")
    
    # Deve ter tentado o Requirements Review mesmo assim
    assert orchestrator._requirements_review.validate_requirements.called

def test_process_pr_pipeline_dedup_blocks(orchestrator):
    """Testa se o orchestrator para se o PR já estiver a ser processado."""
    orchestrator._dedup.should_skip_duplicate.return_value = True
    
    orchestrator.process_pr_pipeline(123, "repo1", "proj1")
    
    assert not orchestrator._ado.get_pr_details.called
