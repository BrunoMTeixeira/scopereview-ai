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
        pr_reader=ado,
        pr_writer=ado,
        code_review=cr,
        requirements_review=rr,
        dedup=dedup,
        code_model_display_name="CodeModel",
        requirements_model_display_name="ReqModel"
    )

@pytest.mark.anyio
async def test_process_pr_pipeline_success(orchestrator):
    """Testa o fluxo feliz completo do orchestrator."""
    orchestrator._pr_reader.get_pr_details.return_value = {
        "title": "Fix bug", 
        "description": "Desc", 
        "commit_sha": "sha1", 
        "base_sha": "base1"
    }
    orchestrator._pr_reader.get_changed_files.return_value = ({"f1.py": "code"}, {"f1.py": "diff"}, 1)
    orchestrator._pr_reader.get_work_items.return_value = [{"id": 1, "title": "Task"}]
    orchestrator._pr_reader.get_repo_rules.return_value = "rules"
    
    orchestrator._code_review.analyze_pr_code.return_value = ({"findings": []}, {"input_tokens": 500, "output_tokens": 500, "token_budget_exceeded": False})
    orchestrator._requirements_review.validate_requirements.return_value = ({"overall_verdict": "APPROVED"}, {"input_tokens": 250, "output_tokens": 250})
    
    orchestrator._dedup.should_skip_duplicate.return_value = False
    
    await orchestrator.process_pr_pipeline(123, "repo1", "proj1")
    
    # O post_comment  chamado apenas se houver resultados
    assert orchestrator._pr_writer.post_comment.called

@pytest.mark.anyio
async def test_process_pr_pipeline_no_details(orchestrator):
    """Testa quando no se consegue obter detalhes do PR."""
    orchestrator._dedup.should_skip_duplicate.return_value = False
    orchestrator._pr_reader.get_pr_details.return_value = None
    
    await orchestrator.process_pr_pipeline(123, "repo1", "proj1")
    
    assert not orchestrator._pr_reader.get_changed_files.called

@pytest.mark.anyio
async def test_process_pr_pipeline_no_supported_files(orchestrator):
    """Testa quando o PR s tem ficheiros ignorados."""
    orchestrator._pr_reader.get_pr_details.return_value = {"title": "Fix bug", "description": "Desc", "commit_sha": "sha1", "base_sha": "base1"}
    orchestrator._pr_reader.get_changed_files.return_value = ({"f1.py": "code"}, {"f1.py": "diff"}, 1)
    orchestrator._pr_reader.get_work_items.return_value = [{"id": 1, "title": "Task"}]
    orchestrator._pr_reader.get_repo_rules.return_value = "rules"
    orchestrator._code_review.analyze_pr_code.return_value = ({"findings": []}, {"input_tokens": 500, "output_tokens": 500, "token_budget_exceeded": False})
    orchestrator._requirements_review.validate_requirements.return_value = ({"overall_verdict": "APPROVED"}, {"input_tokens": 250, "output_tokens": 250})
    orchestrator._dedup.should_skip_duplicate.return_value = False
    
    await orchestrator.process_pr_pipeline(123, "repo1", "proj1")

@pytest.mark.anyio
async def test_process_pr_pipeline_code_review_fails_but_continues(orchestrator):
    """Testa se o orchestrator continua para requisitos se o CR falhar."""
    orchestrator._dedup.should_skip_duplicate.return_value = False
    orchestrator._pr_reader.get_pr_details.return_value = {"title": "T", "commit_sha": "s", "base_sha": "b"}
    orchestrator._pr_reader.get_changed_files.return_value = ({"f1.py": "c"}, {"f1.py": "d"}, 1)
    orchestrator._pr_reader.get_work_items.return_value = []
    orchestrator._pr_reader.get_repo_rules.return_value = ""
    
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
    
    assert not orchestrator._pr_reader.get_pr_details.called

@pytest.mark.anyio
async def test_orchestrator_empty_files_returns_early(orchestrator):
    """Test that orchestrator aborts if no valid files exist."""
    orchestrator._pr_reader.get_pr_details.return_value = {"commit_sha": "abc", "base_sha": "xyz"}
    orchestrator._pr_reader.get_changed_files.return_value = ({}, {}, 0)
    orchestrator._dedup.should_skip_duplicate.return_value = False
    
    await orchestrator.process_pr_pipeline(1, "repo", "proj")
    
    orchestrator._pr_writer.post_pr_status.assert_called_with("repo", 1, "proj", state="succeeded", description="No valid/supported files to review.")
    
@pytest.mark.anyio
async def test_orchestrator_posts_truncation_warning(orchestrator):
    """Test that it warns when files exceed MAX_FILES limit."""
    orchestrator._pr_reader.get_pr_details.return_value = {"commit_sha": "abc", "base_sha": "xyz"}
    orchestrator._pr_reader.get_work_items.return_value = []
    
    orchestrator._dedup.should_skip_duplicate.return_value = False
    orchestrator._pr_reader.get_changed_files.return_value = ({"a.py": "code"}, {"a.py": "+code"}, 5)
    orchestrator._code_review.analyze_pr_code.return_value = ({"findings": []}, {"total_tokens": 10, "token_budget_exceeded": False})
    orchestrator._requirements_review.validate_requirements.return_value = (None, {"total_tokens": 0})
    
    await orchestrator.process_pr_pipeline(1, "repo", "proj")
    
    calls = orchestrator._pr_writer.post_comment.call_args_list
    assert any("File Limit Exceeded" in call.args[3] for call in calls)

@pytest.mark.anyio
async def test_orchestrator_skips_trivial_files(orchestrator):
    """Test that trivial files are skipped by Triage and process aborts."""
    orchestrator._dedup.should_skip_duplicate.return_value = False
    orchestrator._pr_reader.get_pr_details.return_value = {"commit_sha": "abc", "base_sha": "xyz"}
    orchestrator._pr_reader.get_changed_files.return_value = ({"package-lock.json": "code"}, {"package-lock.json": "+code"}, 1)
    
    await orchestrator.process_pr_pipeline(1, "repo", "proj")
    
    orchestrator._pr_writer.post_pr_status.assert_called_with("repo", 1, "proj", state="succeeded", description="All changed files are trivial (skipped).")
import pytest
from unittest.mock import patch

import httpx

@pytest.mark.anyio
async def test_orchestrator_knowledge_ledger_injection(orchestrator):
    """Test that knowledge ledger is built and injected if findings exist."""
    orchestrator._dedup.should_skip_duplicate.return_value = False
    orchestrator._pr_reader.get_pr_details.return_value = {"commit_sha": "abc", "base_sha": "xyz", "description": "PR desc", "title": "PR"}
    orchestrator._pr_reader.get_changed_files.return_value = ({"a.py": "code"}, {"a.py": "+code"}, 1)
    orchestrator._pr_reader.get_work_items.return_value = [{"id": 1, "title": "WI1"}]
    orchestrator._pr_reader.get_repo_rules.return_value = "rules"
    
    orchestrator._code_review.analyze_pr_code.return_value = (
        {"findings": [{"file": "a.py", "line": 1, "severity": "high", "type": "bug", "nfr_label": "security", "issue": "bug"}]}, 
        {"total_tokens": 10, "token_budget_exceeded": False}
    )
    orchestrator._requirements_review.validate_requirements.return_value = ({"approved": True, "must_gate_breached": False, "findings": []}, {"total_tokens": 0})
    
    with patch("src.services.orchestrator.build_ledger") as mock_build:
        mock_build.return_value = {"security": [{"file": "a.py", "issue": "bug"}]}
        await orchestrator.process_pr_pipeline(1, "repo", "proj")
        mock_build.assert_called_once()
        
@pytest.mark.anyio
async def test_orchestrator_circuit_breaker_skips_requirements(orchestrator):
    """Test circuit breaker stops requirements phase if token budget exceeded."""
    orchestrator._dedup.should_skip_duplicate.return_value = False
    orchestrator._pr_reader.get_pr_details.return_value = {"commit_sha": "abc", "base_sha": "xyz", "description": "PR desc", "title": "PR"}
    orchestrator._pr_reader.get_changed_files.return_value = ({"a.py": "code"}, {"a.py": "+code"}, 1)
    orchestrator._pr_reader.get_work_items.return_value = [{"id": 1, "title": "WI1"}]
    orchestrator._pr_reader.get_repo_rules.return_value = "rules"
    
    orchestrator._code_review.analyze_pr_code.return_value = (
        {"findings": []}, 
        {"total_tokens": 100000, "token_budget_exceeded": True}
    )
    
    await orchestrator.process_pr_pipeline(1, "repo", "proj")
    
    orchestrator._pr_writer.post_pr_status.assert_any_call("repo", 1, "proj", state="succeeded", description="Token budget exceeded. Manual validation required.")
    assert not orchestrator._requirements_review.validate_requirements.called

@pytest.mark.anyio
async def test_orchestrator_pr_rejection(orchestrator):
    """Test PR status failure if CR not approved."""
    orchestrator._dedup.should_skip_duplicate.return_value = False
    orchestrator._pr_reader.get_pr_details.return_value = {"commit_sha": "abc", "base_sha": "xyz", "description": "PR desc", "title": "PR"}
    orchestrator._pr_reader.get_changed_files.return_value = ({"a.py": "code"}, {"a.py": "+code"}, 1)
    orchestrator._pr_reader.get_work_items.return_value = [{"id": 1, "title": "WI1"}]
    orchestrator._pr_reader.get_repo_rules.return_value = "rules"
    
    orchestrator._code_review.analyze_pr_code.return_value = (
        {"findings": [], "approve": False}, 
        {"total_tokens": 10, "token_budget_exceeded": False}
    )
    
    orchestrator._requirements_review.validate_requirements.return_value = (
        {"approved": True, "must_gate_breached": False, "findings": []}, 
        {"total_tokens": 0}
    )
    
    await orchestrator.process_pr_pipeline(1, "repo", "proj")
    
    orchestrator._pr_writer.post_pr_status.assert_called_with("repo", 1, "proj", state="failed", description="ScopeReview AI found critical issues or MUST requirements that were not met.")

@pytest.mark.anyio
@pytest.mark.parametrize("exc_type, expected_desc", [
    ("httpx", "Analysis unavailable due to network failure (Fail-Open)."),
    ("os", "Analysis unavailable due to network failure (Fail-Open)."),
    ("value", "Analysis unavailable due to data formatting error (Fail-Open)."),
    ("generic", "Analysis unavailable due to internal error (Fail-Open)."),
])
async def test_orchestrator_error_handlers(orchestrator, exc_type, expected_desc):
    orchestrator._dedup.should_skip_duplicate.return_value = False
    
    if exc_type == "httpx":
        exc = httpx.RequestError("Network", request=httpx.Request("GET", "http://localhost"))
    elif exc_type == "os":
        exc = OSError("Disk")
    elif exc_type == "value":
        exc = ValueError("Format")
    else:
        exc = Exception("Generic")
        
    orchestrator._pr_reader.get_pr_details.side_effect = exc
    
    await orchestrator.process_pr_pipeline(1, "repo", "proj")
    
    orchestrator._pr_writer.post_pr_status.assert_any_call("repo", 1, "proj", state="succeeded", description=expected_desc)
