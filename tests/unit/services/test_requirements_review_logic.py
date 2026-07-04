import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from src.services.requirements_review import RequirementsReviewService

@pytest.fixture
def rr_service():
    ai = MagicMock()
    ai.complete = AsyncMock()
    return RequirementsReviewService(ai)

@pytest.mark.anyio
async def test_validate_requirements_no_json(rr_service):
    """Testa se o servio lida bem com falta de resposta da IA."""
    rr_service._ai.complete.return_value = (None, {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0})
    
    result, metrics = await rr_service.validate_requirements(
        pr_info={"title": "T"},
        work_items=[],
        regras_repo="",
        mapa_ficheiros={"f.py": "code"}
    )
    
    assert result is None
    assert metrics["tokens"] == 0

@pytest.mark.anyio
async def test_validate_requirements_success(rr_service):
    """Testa o fluxo normal de validao de requisitos com estados cannicos."""
    json_data = {
        "work_items_analysed": [{"id": 1, "title": "T", "type": "Story", "has_acceptance_criteria": True}],
        "requirements": [{"id": "REQ-1", "work_item_id": 1, "status": "IMPLEMENTED", "description": "D"}],
        "overall_verdict": "APPROVED",
        "verdict_reason": "All requirements met",
        "implementation_summary": "Summary"
    }
    import json
    rr_service._ai.complete.return_value = (json.dumps(json_data), {"total_tokens": 500, "prompt_tokens": 400, "completion_tokens": 100})
    
    result, metrics = await rr_service.validate_requirements(
        pr_info={"title": "T", "description": "D", "author": "A"},
        work_items=[{"id": 1, "title": "Requirement 1", "acceptance_criteria": "AC"}],
        regras_repo="Rules",
        mapa_ficheiros={"f.py": "code"},
        injected_findings=[]
    )
    
    assert result["overall_verdict"] == "APPROVED"
    assert metrics["tokens"] == 500

@pytest.mark.anyio
async def test_validate_requirements_parsing_exception(rr_service):
    """Testa o tratamento de erros genricos no parsing do JSON."""
    rr_service._ai.complete.return_value = ('{"invalid": "json"}', {"total_tokens": 100, "prompt_tokens": 100, "completion_tokens": 0})
    
    with patch("src.services.requirements_review.parse_llm_json_object", side_effect=RuntimeError("Fatal Error")):
        result, metrics = await rr_service.validate_requirements(
            pr_info={"title": "T"},
            work_items=[{"id": 1, "title": "R"}],
            regras_repo="",
            mapa_ficheiros={"f.py": "c"}
        )
        assert result is None
