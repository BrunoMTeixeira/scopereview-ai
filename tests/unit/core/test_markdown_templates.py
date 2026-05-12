import pytest
from src.templates.markdown import format_code_review, format_requirements_review

def test_format_code_review_no_findings():
    """Testa formatação de CR quando não há achados."""
    res = {"findings": []}
    metrics = {"time": 1.5, "tokens": 100}
    md = format_code_review(res, metrics, model_display_name="GPT-4o")
    
    assert "No security issues or bugs detected" in md
    assert "ScopeReview AI" in md

def test_format_code_review_with_findings():
    """Testa formatação de CR com achados reais."""
    res = {
        "security_score": 7,
        "approve": False,
        "findings": [
            {
                "severity": "high",
                "title": "SQL Injection",
                "file": "db.py",
                "line": 10,
                "vulnerable_code": ["query = '...'"],
                "recommendation": "Use params"
            }
        ]
    }
    metrics = {"time": 2.0, "tokens": 500}
    md = format_code_review(res, metrics, model_display_name="GPT-4o")
    
    assert "Review required" in md
    assert "SQL Injection" in md
    assert "db.py:10" in md
    assert "7 / 10" in md

def test_format_requirements_review_mixed():
    """Testa formatação de requisitos com estados variados."""
    res = {
        "overall_verdict": "FAILED",
        "implementation_summary": "Missing some features",
        "requirements": [
            {"id": "R1", "status": "IMPLEMENTED", "description": "Req 1", "source": "acceptance_criteria"},
            {"id": "R2", "status": "MISSING", "description": "Req 2", "source": "description"}
        ],
        "work_items_analysed": [{"id": 101, "title": "WI 1", "type": "User Story", "has_acceptance_criteria": True}]
    }
    pr_info = {"author": "Bruno"}
    metrics = {"time": 3.0, "tokens": 800, "input_tokens": 600, "output_tokens": 200}
    
    md = format_requirements_review(res, pr_info, metrics, model_display_name="DeepSeek")
    
    assert "Changes needed" in md
    assert "Bruno" in md
    assert "Req 1" in md
    assert "Req 2" in md
    assert "#101" in md
    assert "600 in" in md
    assert "200 out" in md
