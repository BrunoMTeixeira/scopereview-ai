import pytest
from src.templates.markdown import format_code_review, format_requirements_review

def test_format_code_review_no_findings():
    """Testa a formatao de um review sem achados."""
    result = {"findings": []}
    metrics = {"time": 1.5, "tokens": 100}
    md = format_code_review(result, metrics, model_display_name="GPT-4")
    assert "No security issues or bugs detected" in md

def test_format_requirements_review_approved():
    """Testa a formatao de um veredito APPROVED."""
    result = {
        "overall_verdict": "APPROVED",
        "verdict_reason": "Everything matches.",
        "requirements": [
            {"id": "R1", "status": "IMPLEMENTED", "description": "D1", "evidence_code": ["code"]}
        ],
        "work_items_analysed": [{"id": 1, "title": "T1", "type": "Story", "has_acceptance_criteria": True}]
    }
    pr_info = {"title": "PR Title", "author": "Bruno"}
    metrics = {"time": 2.0, "tokens": 200}
    md = format_requirements_review(result, pr_info, metrics, model_display_name="Llama-3")
    
    assert "Approved — all verifiable requirements are implemented" in md
    assert "T1" in md
    assert "Llama-3" in md

def test_format_requirements_review_needs_work():
    """Testa a formatao de um veredito NEEDS_WORK."""
    result = {
        "overall_verdict": "NEEDS_WORK",
        "verdict_reason": "Missing AC.",
        "requirements": [
            {"id": "R1", "status": "MISSING", "description": "D1", "missing_detail": "Missing logic"}
        ],
        "work_items_analysed": [{"id": 1, "title": "T1", "type": "Story", "has_acceptance_criteria": True}]
    }
    pr_info = {"title": "PR", "author": "A"}
    metrics = {"time": 1, "tokens": 10}
    md = format_requirements_review(result, pr_info, metrics, model_display_name="M")
    assert "Changes needed" in md
    assert "Missing logic" in md
