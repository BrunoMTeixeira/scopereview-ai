from src.domain.requirements_verdict import (
    apply_domain_verdict_rules,
    compute_canonical_verdict,
    explain_must_gate_breach,
)


def test_no_requirements():
    assert compute_canonical_verdict([]) == "NO_REQUIREMENTS"


def test_must_missing_forces_needs_work():
    reqs = [
        {"id": "WI-1-AC-1", "priority": "MUST", "status": "MISSING", "description": "x"},
        {"id": "WI-1-AC-2", "priority": "SHOULD", "status": "IMPLEMENTED", "description": "y"},
    ]
    assert compute_canonical_verdict(reqs) == "NEEDS_WORK"


def test_must_partial_forces_needs_work():
    reqs = [{"id": "a", "priority": "must", "status": "partial", "description": ""}]
    assert compute_canonical_verdict(reqs) == "NEEDS_WORK"


def test_any_gap_forces_needs_work_even_without_must():
    reqs = [
        {"id": "a", "priority": "SHOULD", "status": "PARTIAL", "description": ""},
        {"id": "b", "priority": "COULD", "status": "IMPLEMENTED", "description": ""},
    ]
    assert compute_canonical_verdict(reqs) == "NEEDS_WORK"


def test_all_implemented_approved():
    reqs = [
        {"id": "a", "priority": "MUST", "status": "IMPLEMENTED", "description": ""},
        {"id": "b", "priority": "SHOULD", "status": "IMPLEMENTED", "description": ""},
    ]
    assert compute_canonical_verdict(reqs) == "APPROVED"


def test_implemented_plus_unverifiable_approved():
    reqs = [
        {"id": "a", "priority": "MUST", "status": "IMPLEMENTED", "description": ""},
        {"id": "b", "priority": "SHOULD", "status": "UNVERIFIABLE", "description": ""},
    ]
    assert compute_canonical_verdict(reqs) == "APPROVED"


def test_all_unverifiable():
    reqs = [
        {"id": "a", "priority": "MUST", "status": "UNVERIFIABLE", "description": ""},
        {"id": "b", "priority": "MUST", "status": "UNVERIFIABLE", "description": ""},
    ]
    assert compute_canonical_verdict(reqs) == "UNVERIFIABLE"


def test_apply_domain_overrides_wrong_llm_verdict():
    base = {
        "requirements": [
            {"id": "1", "priority": "MUST", "status": "MISSING", "description": "d"},
        ],
        "work_items_analysed": [],
        "overall_verdict": "APPROVED",
        "verdict_reason": "model hallucination",
        "implementation_summary": "",
    }
    out = apply_domain_verdict_rules(base)
    assert out["overall_verdict"] == "NEEDS_WORK"
    assert "Policy" in out["verdict_reason"]
    assert "APPROVED" in out["verdict_reason"]


def test_apply_domain_idempotent_when_agrees():
    base = {
        "requirements": [{"id": "1", "priority": "MUST", "status": "IMPLEMENTED", "description": ""}],
        "work_items_analysed": [],
        "overall_verdict": "APPROVED",
        "verdict_reason": "ok",
        "implementation_summary": "",
    }
    out = apply_domain_verdict_rules(base)
    assert out["overall_verdict"] == "APPROVED"
    assert out["verdict_reason"] == "ok"


def test_explain_must_gate():
    reqs = [{"id": "x", "priority": "MUST", "status": "MISSING", "description": ""}]
    breached, ids = explain_must_gate_breach(reqs)
    assert breached is True
    assert ids == ["x"]
