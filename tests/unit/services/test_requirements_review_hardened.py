import pytest
import json
from unittest.mock import MagicMock, patch
from src.services.requirements_review import RequirementsReviewService

@pytest.fixture
def rr_service():
    ai = MagicMock()
    return RequirementsReviewService(ai)

def test_validate_requirements_domain_override(rr_service):
    """
    TESTE DE RIGOR: A IA alucina e diz 'APPROVED', mas um requisito MUST esta MISSING.
    O sistema DEVE ignorar o veredito da IA e aplicar a regra de domnio 'NEEDS_WORK'.
    """
    json_hallucinated = {
        "work_items_analysed": [{"id": 1, "title": "T", "type": "Story", "has_acceptance_criteria": True}],
        "requirements": [
            {
                "id": "REQ-1", 
                "work_item_id": 1, 
                "status": "MISSING", 
                "priority": "MUST", 
                "description": "D"
            }
        ],
        "overall_verdict": "APPROVED",
        "verdict_reason": "I think it is ok",
        "implementation_summary": "Summary"
    }
    rr_service._ai.complete.return_value = (json.dumps(json_hallucinated), 500)
    
    result, _ = rr_service.validate_requirements(
        pr_info={"title": "T"},
        work_items=[{"id": 1, "title": "R"}],
        regras_repo="",
        mapa_ficheiros={"f.py": "c"}
    )
    
    assert result["overall_verdict"] == "NEEDS_WORK"
    assert "[Policy: verdict normalized to 'NEEDS_WORK'" in result["verdict_reason"]

def test_validate_requirements_markdown_stripping(rr_service):
    """
    TESTE DE RESILINCIA: A IA envolve o JSON em blocos de markdown e texto extra.
    O parser (via json-repair) deve extrair o objeto corretamente.
    Inclumos um requisito para evitar que o domnio force 'NO_REQUIREMENTS'.
    """
    json_payload = {
        "work_items_analysed": [{"id": 1, "title": "T", "type": "Story", "has_acceptance_criteria": True}],
        "requirements": [{"id": "REQ-1", "status": "IMPLEMENTED", "description": "D"}],
        "overall_verdict": "APPROVED",
        "verdict_reason": "Ok",
        "implementation_summary": "Ok"
    }
    raw_response = "Analysis finished:\n```json\n" + json.dumps(json_payload) + "\n```\nHope this helps!"
    
    rr_service._ai.complete.return_value = (raw_response, 100)
    
    result, _ = rr_service.validate_requirements(
        pr_info={"title": "T"},
        work_items=[{"id": 1, "title": "R"}],
        regras_repo="",
        mapa_ficheiros={"f.py": "c"}
    )
    
    assert result is not None
    assert result["overall_verdict"] == "APPROVED"
