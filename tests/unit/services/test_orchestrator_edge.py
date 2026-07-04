import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from src.services.orchestrator import PipelineOrchestrator

@pytest.fixture
def orchestrator():
    # Mocking dependencies
    ado = MagicMock()
    ado.get_pr_details = AsyncMock()
    ado.get_changed_files = AsyncMock()
    ado.get_work_items = AsyncMock()
    ado.get_repo_rules = AsyncMock()
    ado.post_comment = AsyncMock()
    ado.post_pr_status = AsyncMock()
    
    cr = MagicMock()
    cr.analyze_pr_code = AsyncMock()
    
    rr = MagicMock()
    rr.validate_requirements = AsyncMock()
    
    dedup = MagicMock()
    return PipelineOrchestrator(
        ado, cr, rr, dedup,
        code_model_display_name="CodeModel",
        requirements_model_display_name="ReqModel"
    )

@pytest.mark.anyio
async def test_process_pr_pipeline_success(orchestrator):
    """Testa o fluxo feliz completo do orchestrator."""
    orchestrator._ado.get_pr_details.return_value = {
        "title": "Fix bug", 
        "description": "Desc", 
        "commit_sha": "sha1", 
        "base_sha": "base1"
    }
    orchestrator._ado.get_changed_files.return_value = ({"f1.py": "code"}, {"f1.py": "diff"}, 1)
    orchestrator._ado.get_work_items.return_value = [{"id": 1, "title": "Task"}]
    orchestrator._ado.get_repo_rules.return_value = "rules"
    
    orchestrator._code_review.analyze_pr_code.return_value = ({"findings": []}, {"input_tokens": 500, "output_tokens": 500, "token_budget_exceeded": False})
    orchestrator._requirements_review.validate_requirements.return_value = ({"overall_verdict": "APPROVED"}, {"input_tokens": 250, "output_tokens": 250})
    
    orchestrator._dedup.should_skip_duplicate.return_value = False
    
    await orchestrator.process_pr_pipeline(123, "repo1", "proj1")
    
    # O post_comment  chamado apenas se houver resultados
    assert orchestrator._ado.post_comment.called

@pytest.mark.anyio
async def test_process_pr_pipeline_no_details(orchestrator):
    """Testa quando no se consegue obter detalhes do PR."""
    orchestrator._dedup.should_skip_duplicate.return_value = False
    orchestrator._ado.get_pr_details.return_value = None
    
    await orchestrator.process_pr_pipeline(123, "repo1", "proj1")
    
    assert not orchestrator._ado.get_changed_files.called

@pytest.mark.anyio
async def test_process_pr_pipeline_no_supported_files(orchestrator):
    """Testa quando o PR s tem ficheiros ignorados."""
    orchestrator._ado.get_pr_details.return_value = {"title": "Fix bug", "description": "Desc", "commit_sha": "sha1", "base_sha": "base1"}
    orchestrator._ado.get_changed_files.return_value = ({"f1.py": "code"}, {"f1.py": "diff"}, 1)
    orchestrator._ado.get_work_items.return_value = [{"id": 1, "title": "Task"}]
    orchestrator._ado.get_repo_rules.return_value = "rules"
    orchestrator._code_review.analyze_pr_code.return_value = ({"findings": []}, {"input_tokens": 500, "output_tokens": 500, "token_budget_exceeded": False})
    orchestrator._requirements_review.validate_requirements.return_value = ({"overall_verdict": "APPROVED"}, {"input_tokens": 250, "output_tokens": 250})
    orchestrator._dedup.should_skip_duplicate.return_value = False
    
    await orchestrator.process_pr_pipeline(123, "repo1", "proj1")

@pytest.mark.anyio
async def test_process_pr_pipeline_code_review_fails_but_continues(orchestrator):
    """Testa se o orchestrator continua para requisitos se o CR falhar."""
    orchestrator._dedup.should_skip_duplicate.return_value = False
    orchestrator._ado.get_pr_details.return_value = {"title": "T", "commit_sha": "s", "base_sha": "b"}
    orchestrator._ado.get_changed_files.return_value = ({"f1.py": "c"}, {"f1.py": "d"}, 1)
    orchestrator._ado.get_work_items.return_value = []
    orchestrator._ado.get_repo_rules.return_value = ""
    
    # Simular falha no Code Review
    orchestrator._code_review.analyze_pr_code.return_value = (None, {"tokens": 0, "time": 0, "token_budget_exceeded": False})
    orchestrator._requirements_review.validate_requirements.return_value = (None, {"tokens": 0, "time": 0})
    
    await orchestrator.process_pr_pipeline(123, "repo1", "proj1")
    
    # Deve ter tentado o Requirements Review mesmo assim
    assert orchestrator._requirements_review.validate_requirements.called

@pytest.mark.anyio
async def test_process_pr_pipeline_dedup_blocks(orchestrator):
    """Testa se o orchestrator para se o PR já estiver a ser processado."""
    orchestrator._dedup.should_skip_duplicate.return_value = True
    
    await orchestrator.process_pr_pipeline(123, "repo1", "proj1")
    
    assert not orchestrator._ado.get_pr_details.called

@pytest.mark.anyio
async def test_orchestrator_empty_files_returns_early(orchestrator):
    """Testa se o orchestrator aborta caso nao existam ficheiros validos."""
    orchestrator._ado.get_pr_details.return_value = {"commit_sha": "abc", "base_sha": "xyz"}
    orchestrator._ado.get_changed_files.return_value = ({}, {}, 0)
    orchestrator._dedup.should_skip_duplicate.return_value = False
    
    await orchestrator.process_pr_pipeline(1, "repo", "proj")
    
    orchestrator._ado.post_pr_status.assert_called_with("repo", 1, "proj", state="succeeded", description="No valid/supported files to review.")
    
@pytest.mark.anyio
async def test_orchestrator_posts_truncation_warning(orchestrator):
    """Testa se emite aviso quando ficheiros excedem MAX_FILES."""
    orchestrator._ado.get_pr_details.return_value = {"commit_sha": "abc", "base_sha": "xyz"}
    orchestrator._ado.get_work_items.return_value = []
    
    orchestrator._dedup.should_skip_duplicate.return_value = False
    orchestrator._ado.get_changed_files.return_value = ({"a.py": "code"}, {"a.py": "+code"}, 5)
    orchestrator._code_review.analyze_pr_code.return_value = ({"findings": []}, {"total_tokens": 10, "token_budget_exceeded": False})
    orchestrator._requirements_review.validate_requirements.return_value = (None, {"total_tokens": 0})
    
    await orchestrator.process_pr_pipeline(1, "repo", "proj")
    
    calls = orchestrator._ado.post_comment.call_args_list
    assert any("Aviso de Limite Excedido" in call.args[3] for call in calls)

@pytest.mark.anyio
async def test_orchestrator_skips_trivial_files(orchestrator):
    """Testa se ficheiros trivial (ex: lock files) sofrem skip via Triage e abortam o processo."""
    orchestrator._dedup.should_skip_duplicate.return_value = False
    orchestrator._ado.get_pr_details.return_value = {"commit_sha": "abc", "base_sha": "xyz"}
    orchestrator._ado.get_changed_files.return_value = ({"package-lock.json": "code"}, {"package-lock.json": "+code"}, 1)
    
    await orchestrator.process_pr_pipeline(1, "repo", "proj")
    
    orchestrator._ado.post_pr_status.assert_called_with("repo", 1, "proj", state="succeeded", description="All changed files are trivial (skipped).")
