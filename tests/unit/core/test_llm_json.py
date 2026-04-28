import json

from src.core.llm_json import parse_llm_json_object, sanitize_llm_json_fragment


def test_sanitize_trailing_comma_roundtrip():
    assert json.loads(sanitize_llm_json_fragment('{"a": 1,}')) == {"a": 1}


def test_parse_strict_ok():
    raw = (
        '{"requirements": [], "work_items_analysed": [], '
        '"overall_verdict": "NO_REQUIREMENTS", "verdict_reason": "", "implementation_summary": ""}'
    )
    d = parse_llm_json_object(raw, log_context="test")
    assert d["overall_verdict"] == "NO_REQUIREMENTS"


def test_parse_json_repair_trailing_comma():
    broken = '{"x": 1, "y": 2,}'
    d = parse_llm_json_object(broken, log_context="test")
    assert d == {"x": 1, "y": 2}
